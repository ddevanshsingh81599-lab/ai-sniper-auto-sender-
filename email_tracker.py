"""
email_tracker.py
────────────────
Lightweight HTTP server that tracks email opens and link clicks.
Runs as a separate thread inside email_sender.py (or standalone).

Endpoints:
  GET /track/open?id=<email_id>    → returns 1x1 transparent pixel, writes Open timestamp to Sheet col W
  GET /track/click?id=<email_id>   → records click in Sheet col X, then 301-redirects to target URL
  GET /health                      → returns 200 OK

Sheet tracking columns:
  V  Email ID   — UUID assigned at send time
  W  Opened?    — ISO timestamp of first open
  X  Clicked?   — ISO timestamp of first click

HOW IT WORKS:
  1. email_sender.py generates a UUID for each email before sending.
  2. UUID is stored in Sheet col V.
  3. The email body HTML contains a 1×1 img tag pointing to /track/open?id=<uuid>
  4. Every auctron.in / auctron.net.in link in the body is rewritten to
     /track/click?id=<uuid>&url=<original_encoded_url>
  5. When Gmail loads the email, the pixel fires → open is recorded.
  6. When the recipient clicks a link → click is recorded + redirect happens.

FIXES (2026-06-19):
  - BUG FIX 1: _record_open/_record_click now fall back to a live sheet scan
    when the email_id is NOT in the in-memory cache (handles Render cold-starts
    and any cache-miss where 5 real clicks were silently dropped).
  - BUG FIX 2: inject_tracking now matches ALL URL forms:
      https://auctron.net.in, http://auctron.net.in, auctron.net.in,
      www.auctron.net.in — without requiring https:// prefix.
  - BUG FIX 3: inject_tracking no longer produces broken double-encoded hrefs.
    The HTML <a href> is built cleanly in a single pass.
  - BUG FIX 4: Plain-text bare URLs (no scheme) are normalised to https://
    before wrapping so UTM params are valid and Clarity receives them correctly.

Environment variables:
  TRACKER_BASE_URL   — e.g. https://auctron-tracker.railway.app
  PORT               — port to listen on (default 8080 or TRACKER_PORT)
  GOOGLE_SHEET_ID    — same as email_sender.py
  GOOGLE_CREDENTIALS — same as email_sender.py
"""

import os
import re
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

# ── Transparent 1×1 GIF (bytes) ───────────────────────────────────────────────
PIXEL_GIF = bytes([
    0x47,0x49,0x46,0x38,0x39,0x61,0x01,0x00,0x01,0x00,0x80,0x00,0x00,
    0xFF,0xFF,0xFF,0x00,0x00,0x00,0x21,0xF9,0x04,0x00,0x00,0x00,0x00,
    0x00,0x2C,0x00,0x00,0x00,0x00,0x01,0x00,0x01,0x00,0x00,0x02,0x02,
    0x44,0x01,0x00,0x3B,
])

# Sheet column numbers (1-based for gspread)
GCOL_EMAIL_ID = 22   # V
GCOL_OPENED   = 23   # W
GCOL_CLICKED  = 24   # X

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ── Sheet helpers ─────────────────────────────────────────────────────────────

def _get_sheet():
    sheet_id  = os.getenv("GOOGLE_SHEET_ID")
    cred_env  = os.getenv("GOOGLE_CREDENTIALS")
    if cred_env:
        creds = Credentials.from_service_account_info(json.loads(cred_env), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(sheet_id).sheet1


# ── In-memory ID→row cache ────────────────────────────────────────────────────
#
# Structure: { email_id: {"row": int, "opened": bool, "clicked": bool} }
_id_cache: dict = {}
_cache_lock = threading.Lock()


def _load_id_cache(sheet):
    """Read col V from the sheet and build the ID→row mapping."""
    global _id_cache
    rows = sheet.get("A2:X") or []
    new_cache = {}
    for i, row in enumerate(rows):
        row = row + [""] * (24 - len(row))
        email_id = row[21].strip()   # col V (0-indexed: 21)
        if email_id:
            new_cache[email_id] = {
                "row":     i + 2,
                "opened":  bool(row[22].strip()),  # W
                "clicked": bool(row[23].strip()),  # X
            }
    with _cache_lock:
        _id_cache = new_cache
    print(f"  📧 Tracker cache loaded — {len(new_cache)} tracked emails.")


def _find_row_by_id_fallback(email_id: str):
    """
    FIX #1: Cache-miss fallback.
    When the in-memory cache doesn't have the email_id (e.g. after a cold
    start / Render restart), do a live sheet scan to find it.
    Returns {"row": int, "opened": bool, "clicked": bool} or None.
    """
    try:
        sheet = _get_sheet()
        rows = sheet.get("A2:X") or []
        for i, row in enumerate(rows):
            row = row + [""] * (24 - len(row))
            if row[21].strip() == email_id:
                entry = {
                    "row":     i + 2,
                    "opened":  bool(row[22].strip()),
                    "clicked": bool(row[23].strip()),
                }
                # Warm the cache so subsequent events are instant
                with _cache_lock:
                    _id_cache[email_id] = entry
                print(f"  🔍 Cache-miss fallback: found ID {email_id[:8]}… at row {entry['row']}")
                return entry
    except Exception as e:
        print(f"  ⚠️  Fallback sheet scan failed: {e}")
    return None


def _record_open(email_id: str):
    """Write open timestamp to col W for the given email_id."""
    with _cache_lock:
        entry = _id_cache.get(email_id)

    # FIX #1: If not in cache, do a live lookup
    if not entry:
        entry = _find_row_by_id_fallback(email_id)

    if not entry:
        print(f"  ⚠️  Open: email_id {email_id[:8]}… not found in sheet.")
        return
    if entry.get("opened"):
        return  # already recorded — idempotent

    try:
        sheet = _get_sheet()
        ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
        sheet.update_cell(entry["row"], GCOL_OPENED, ts)
        with _cache_lock:
            _id_cache[email_id]["opened"] = True
        print(f"  👁️  Open recorded for ID {email_id[:8]}… row {entry['row']}")
    except Exception as e:
        print(f"  ⚠️  Failed to record open: {e}")


def _record_click(email_id: str):
    """Write click timestamp to col X for the given email_id."""
    with _cache_lock:
        entry = _id_cache.get(email_id)

    # FIX #1: If not in cache, do a live lookup
    if not entry:
        entry = _find_row_by_id_fallback(email_id)

    if not entry:
        print(f"  ⚠️  Click: email_id {email_id[:8]}… not found in sheet.")
        return
    if entry.get("clicked"):
        return  # already recorded — idempotent

    try:
        sheet = _get_sheet()
        ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
        sheet.update_cell(entry["row"], GCOL_CLICKED, ts)
        with _cache_lock:
            _id_cache[email_id]["clicked"] = True
        print(f"  🖱️  Click recorded for ID {email_id[:8]}… row {entry['row']}")
    except Exception as e:
        print(f"  ⚠️  Failed to record click: {e}")


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _TrackHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed  = urlparse(self.path)
        path    = parsed.path.rstrip("/")
        params  = parse_qs(parsed.query)
        email_id = (params.get("id") or [""])[0]

        if path == "/track/open":
            # Fire-and-forget in background thread so HTTP response is instant
            if email_id:
                threading.Thread(target=_record_open, args=(email_id,), daemon=True).start()
            # Return transparent 1×1 GIF
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.send_header("Content-Length", str(len(PIXEL_GIF)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(PIXEL_GIF)

        elif path == "/track/click":
            target_url = (params.get("url") or ["https://auctron.net.in"])[0]
            if email_id:
                threading.Thread(target=_record_click, args=(email_id,), daemon=True).start()
            # Redirect immediately — don't wait for the sheet write
            self.send_response(301)
            self.send_header("Location", target_url)
            self.send_header("Cache-Control", "no-store, no-cache")
            self.end_headers()

        elif path in ("/health", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Auctron email tracker OK")

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass   # silence request noise in logs


# ── Public helpers used by email_sender.py ────────────────────────────────────

def register_email_id(sheet, row_number: int, email_id: str):
    """
    Write the email_id UUID to col V of the given row.
    Also updates the local cache so opens/clicks can be tracked immediately.
    """
    try:
        sheet.update_cell(row_number, GCOL_EMAIL_ID, email_id)
        with _cache_lock:
            _id_cache[email_id] = {"row": row_number, "opened": False, "clicked": False}
    except Exception as e:
        print(f"  ⚠️  Failed to write email_id to sheet: {e}")


# ── FIX #2, #3, #4: Completely rewritten inject_tracking ─────────────────────

# Matches ALL auctron URL forms, with or without scheme, with or without www:
#   https://auctron.net.in/...   http://auctron.net.in   auctron.net.in
#   https://www.auctron.in/...   auctron.in
_AUCTRON_URL_PATTERN = re.compile(
    r'(?:https?://)?'                       # optional scheme
    r'(?:www\.)?'                           # optional www
    r'(?:auctron\.net\.in|auctron\.in)'     # domain
    r'(?:/[^\s\)\]"\'<>]*)?',              # optional path
    re.IGNORECASE
)


def _normalise_url(raw: str) -> str:
    """
    FIX #4: Ensure the URL has an https:// scheme.
    Bare URLs like 'auctron.net.in' become 'https://auctron.net.in'.
    """
    if not raw.startswith("http://") and not raw.startswith("https://"):
        return "https://" + raw
    return raw


def inject_tracking(body: str, email_id: str, base_url: str) -> str:
    """
    Takes a plain-text email body and returns an HTML email body that:
      1. Wraps the plain text in a minimal HTML shell.
      2. Rewrites ALL auctron.in / auctron.net.in links (with or without
         https://) to click-tracking redirect URLs.
      3. Adds UTM parameters: utm_source, utm_medium, utm_campaign, utm_content.
      4. Appends a hidden 1×1 tracking pixel at the very bottom.

    FIX summary:
      - Regex now matches bare URLs without https:// prefix (FIX #2 + #4).
      - HTML link rewrite is done in a single clean pass — no double-encoding (FIX #3).
      - click_url uses plain & not &amp; in the href (FIX #3).
    """
    tracker_base = base_url.rstrip("/")

    # Build the pixel img tag
    pixel_url  = f"{tracker_base}/track/open?id={email_id}"
    pixel_html = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="" />'

    utm_params = (
        f"utm_source=auctron"
        f"&utm_medium=email"
        f"&utm_campaign=cold_outreach"
        f"&utm_content={email_id[:8]}"
    )

    def _rewrite(match):
        """
        FIX #3: Single-pass, clean rewrite.
        Returns an <a href="..."> element with the click-tracking URL.
        The final destination already has UTM params appended.
        The entire destination URL is percent-encoded so &utm_ params
        don't bleed into the outer tracker query string.
        """
        raw_url = match.group(0)
        canonical = _normalise_url(raw_url)            # ensure https://
        sep = "&" if "?" in canonical else "?"
        dest_with_utm = canonical + sep + utm_params   # add UTM to destination
        # quote(safe='') encodes ALL special chars including & = ? in the dest
        encoded_dest = quote(dest_with_utm, safe="")
        # plain & (not &amp;) in the href — HTML parser handles it fine
        click_url = f"{tracker_base}/track/click?id={email_id}&url={encoded_dest}"
        return f'<a href="{click_url}">{raw_url}</a>'

    # Minimal HTML escape of the body (only & needs escaping before link rewrite)
    safe_body = body.replace("&", "&amp;")

    # Apply link rewriting to the escaped body
    safe_body = _AUCTRON_URL_PATTERN.sub(_rewrite, safe_body)

    # Convert newlines → <br>
    safe_body = safe_body.replace("\n", "<br>\n")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#222;max-width:600px;margin:0 auto;padding:20px">
{safe_body}
{pixel_html}
</body>
</html>"""

    return html


# ── Standalone server start ───────────────────────────────────────────────────

def start_tracker_server(sheet=None):
    """
    Start the tracking HTTP server in a background daemon thread.
    Optionally loads the ID cache from the given sheet.
    Returns the port it's listening on.
    """
    port = int(os.getenv("PORT", os.getenv("TRACKER_PORT", 8080)))
    if sheet:
        threading.Thread(target=_load_id_cache, args=(sheet,), daemon=True).start()
    server = HTTPServer(("0.0.0.0", port), _TrackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  📡 Email tracker server listening on port {port}")
    return port


if __name__ == "__main__":
    # Standalone mode: connect to sheet and run tracker
    print("Starting email tracker server...")
    try:
        sheet = _get_sheet()
        _load_id_cache(sheet)
    except Exception as e:
        print(f"  ⚠️  Could not load sheet cache: {e}")
        sheet = None

    port = int(os.getenv("PORT", os.getenv("TRACKER_PORT", 8080)))
    print(f"  📡 Tracker listening on 0.0.0.0:{port}")
    HTTPServer(("0.0.0.0", port), _TrackHandler).serve_forever()
