"""
email_tracker.py
────────────────
Lightweight HTTP server that tracks email opens and link clicks.
Runs as a separate thread inside email_sender.py (or standalone).

Endpoints:
  GET /track/open?id=<email_id>    → returns 1x1 transparent pixel, writes Open timestamp to Sheet col W
  GET /track/click?id=<email_id>   → records click in Sheet col X, then 301-redirects to target URL
  GET /health                      → returns 200 OK

Sheet tracking columns (appended to the right of existing 21 cols):
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

Environment variables:
  TRACKER_BASE_URL   — e.g. https://auctron-tracker.railway.app
                        (the public URL of this server)
  PORT               — port to listen on (default 8080 or TRACKER_PORT)
  GOOGLE_SHEET_ID    — same as email_sender.py
  GOOGLE_CREDENTIALS — same as email_sender.py
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode, quote

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
SHEET_RANGE   = "A2:X"

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


# ── In-memory ID→row cache (refreshed from sheet on startup) ──────────────────
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


def _record_open(email_id: str):
    """Write open timestamp to col W for the given email_id."""
    with _cache_lock:
        entry = _id_cache.get(email_id)
    if not entry or entry["opened"]:
        return   # not found, or already recorded
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
    if not entry or entry["clicked"]:
        return
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
            self.send_response(301)
            self.send_header("Location", target_url)
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


def inject_tracking(body: str, email_id: str, base_url: str) -> str:
    """
    Takes a plain-text email body and returns an HTML email body that:
      1. Wraps the plain text in a minimal HTML shell
      2. Rewrites all auctron.in / auctron.net.in links to click-tracking URLs
      3. Appends a hidden 1x1 tracking pixel at the very bottom
      4. Also adds UTM parameters to every link
    """
    import re

    tracker_base = base_url.rstrip("/")

    # Build the pixel img tag
    pixel_url  = f"{tracker_base}/track/open?id={email_id}"
    pixel_html = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="" />'

    # Rewrite all auctron links: add UTM + wrap in click tracker
    AUCTRON_DOMAINS = ["auctron.in", "auctron.net.in", "www.auctron.in"]

    def _rewrite_link(match):
        url = match.group(0)
        # Add UTM params
        sep = "&" if "?" in url else "?"
        utm = f"utm_source=auctron&utm_medium=email&utm_campaign=cold_outreach&utm_content={email_id[:8]}"
        tracked_dest = url + sep + utm
        # Wrap in click tracker
        click_url = f"{tracker_base}/track/click?id={email_id}&url={quote(tracked_dest, safe='')}"
        return click_url

    # Match http(s) URLs containing any auctron domain
    pattern = r'https?://(?:www\.)?(?:auctron\.in|auctron\.net\.in)[^\s\)\]"\'<>]*'
    body_rewritten = re.sub(pattern, _rewrite_link, body)

    # Convert plain text to simple HTML (preserve line breaks)
    html_body = body_rewritten.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Restore the click tracking URLs we just inserted (they contain < etc.)
    # Actually: let's build the HTML differently — wrap raw text, then inject links
    # Simpler: escape first, then do link rewriting on the escaped text
    # Re-do: escape the original body first, then rewrite links in the escaped version

    safe_body = body.replace("&", "&amp;")   # minimal escape for HTML

    def _rewrite_link_html(match):
        url = match.group(0)
        sep = "&amp;" if "?" in url else "?"
        utm = (
            f"utm_source=auctron&amp;utm_medium=email"
            f"&amp;utm_campaign=cold_outreach&amp;utm_content={email_id[:8]}"
        )
        tracked_dest = url + sep + utm
        click_url = f"{tracker_base}/track/click?id={email_id}&amp;url={quote(tracked_dest.replace('&amp;','&'), safe='')}"
        return f'<a href="{click_url}">{url}</a>'

    # Rewrite auctron links with HTML-safe href
    html_pattern = r'https?://(?:www\.)?(?:auctron\.in|auctron\.net\.in)[^\s\)\]"\'<>]*'
    safe_body = re.sub(html_pattern, _rewrite_link_html, safe_body)

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
