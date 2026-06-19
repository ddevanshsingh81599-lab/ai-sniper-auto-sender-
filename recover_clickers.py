"""
recover_clickers.py
────────────────────
Run this ONCE to recover the 5 people who clicked your email links
but whose clicks were never recorded in the Google Sheet
(because the tracker server was asleep / cache was empty).

HOW IT WORKS
────────────
1. Reads your Google Sheet — finds every row that was Sent but has NO click recorded
2. Checks Render logs via the Render API to find /track/click requests
   that contain email UUIDs from your sheet
3. For any match found → backfills col X (Clicked?) with an estimated timestamp
4. Also outputs a "hot leads" list: everyone who was sent an email recently
   (last 30 days) but has no click/reply yet — so you can send a manual follow-up

USAGE
─────
    python recover_clickers.py                  # check & backfill from Render logs
    python recover_clickers.py --no-render      # skip Render log check, just show hot leads
    python recover_clickers.py --show-sent      # show all sent emails with status

Set RENDER_API_KEY in your .env to enable Render log fetching.
Get it from: https://dashboard.render.com/u/settings → API Keys
"""

import os
import sys
import json
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

IST      = timezone(timedelta(hours=5, minutes=30))
NO_RENDER= "--no-render" in sys.argv
SHOW_SENT= "--show-sent" in sys.argv

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Sheet columns (0-based)
COL_NAME    = 0   # A
COL_EMAIL   = 1   # B
COL_SOURCE  = 3   # D
COL_SENT    = 17  # R
COL_REPLY   = 18  # S
COL_NOTES   = 20  # U
COL_EMAIL_ID= 21  # V
COL_OPENED  = 22  # W
COL_CLICKED = 23  # X

# gspread column numbers (1-based)
GCOL_CLICKED = 24  # X
GCOL_OPENED  = 23  # W
GCOL_NOTES   = 21  # U


# ── Sheet connection ──────────────────────────────────────────────────────────

def _get_sheet():
    cred_env = os.getenv("GOOGLE_CREDENTIALS")
    if cred_env:
        creds = Credentials.from_service_account_info(json.loads(cred_env), scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(os.getenv("GOOGLE_SHEET_ID")).sheet1


def _load_rows(sheet):
    rows = sheet.get("A2:X") or []
    result = []
    for i, row in enumerate(rows):
        row = row + [""] * (24 - len(row))
        result.append({
            "row_num":   i + 2,
            "name":      row[COL_NAME].strip(),
            "email":     row[COL_EMAIL].strip(),
            "source":    row[COL_SOURCE].strip(),
            "sent":      row[COL_SENT].strip(),
            "reply":     row[COL_REPLY].strip(),
            "notes":     row[COL_NOTES].strip(),
            "email_id":  row[COL_EMAIL_ID].strip(),
            "opened":    row[COL_OPENED].strip(),
            "clicked":   row[COL_CLICKED].strip(),
        })
    return result


# ── Render log fetching ───────────────────────────────────────────────────────

def _fetch_render_logs(service_id: str, api_key: str, limit: int = 500) -> list[str]:
    """
    Fetch recent logs from Render API.
    Returns list of raw log lines.
    """
    url = f"https://api.render.com/v1/services/{service_id}/logs"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept":        "application/json",
    }
    params = {"limit": limit}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # Render returns {"logs": [{"message": "...", "timestamp": "..."}]}
            if isinstance(data, dict) and "logs" in data:
                return [(l.get("timestamp", ""), l.get("message", "")) for l in data["logs"]]
            elif isinstance(data, list):
                return [(l.get("timestamp", ""), l.get("message", "")) for l in data]
        else:
            print(f"  ⚠️  Render API {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  ⚠️  Render log fetch failed: {e}")
    return []


def _extract_click_events_from_logs(logs: list) -> dict:
    """
    Parse Render log lines to find /track/click?id=<uuid> requests.
    Returns dict: email_id -> timestamp_string
    """
    found = {}
    # Pattern: /track/click?id=<uuid>
    click_pattern = re.compile(r"/track/click\?id=([0-9a-f-]{36})", re.IGNORECASE)
    open_pattern  = re.compile(r"/track/open\?id=([0-9a-f-]{36})",  re.IGNORECASE)

    for ts, line in logs:
        m = click_pattern.search(line)
        if m:
            eid = m.group(1)
            if eid not in found:
                found[eid] = {"type": "click", "ts": ts or "unknown time"}
                print(f"  🔍 Found click in logs: ID={eid[:8]}… at {ts}")

        m = open_pattern.search(line)
        if m:
            eid = m.group(1)
            if eid not in found:
                found[eid] = {"type": "open", "ts": ts or "unknown time"}
                print(f"  🔍 Found open in logs: ID={eid[:8]}… at {ts}")

    return found


# ── Main recovery logic ───────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("  AUCTRON — CLICK RECOVERY & HOT LEADS SCAN")
    print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    print("=" * 60)

    # ── 1. Load sheet ─────────────────────────────────────────────
    print("\n[1/4] Loading Google Sheet...")
    sheet = _get_sheet()
    rows  = _load_rows(sheet)
    sent_rows    = [r for r in rows if r["sent"]]
    unsent_rows  = [r for r in rows if not r["sent"] and r["email"]]
    clicked_rows = [r for r in sent_rows if r["clicked"]]
    opened_rows  = [r for r in sent_rows if r["opened"]]
    replied_rows = [r for r in sent_rows if r["reply"]]

    print(f"  ✅ Sheet loaded — {len(rows)} total rows")
    print(f"  📤 Sent:    {len(sent_rows)}")
    print(f"  👁  Opened: {len(opened_rows)}")
    print(f"  🖱  Clicked:{len(clicked_rows)}")
    print(f"  💬 Replied:{len(replied_rows)}")

    # ── 2. Show all sent emails (optional) ────────────────────────
    if SHOW_SENT:
        print(f"\n{'─'*60}")
        print(f"📤 ALL SENT EMAILS ({len(sent_rows)} total):\n")
        print(f"{'Name':<28} {'Email':<33} {'Sent':<12} {'Open':<6} {'Click':<6} {'Reply'}")
        print("─" * 100)
        for r in sent_rows:
            print(
                f"{r['name'][:27]:<28} {r['email'][:32]:<33} "
                f"{r['sent'][:10]:<12} "
                f"{'👁' if r['opened']  else '—':<6} "
                f"{'🖱' if r['clicked'] else '—':<6} "
                f"{'💬' if r['reply']   else '—'}"
            )

    # ── 3. Render log scan ────────────────────────────────────────
    recovered = {}

    if not NO_RENDER:
        print(f"\n[2/4] Scanning Render logs for lost click/open events...")
        render_api_key  = os.getenv("RENDER_API_KEY", "")
        render_service_id = os.getenv("RENDER_SERVICE_ID", "")

        if not render_api_key or not render_service_id:
            print("  ⚠️  RENDER_API_KEY or RENDER_SERVICE_ID not set in .env")
            print("      → Get API key from: https://dashboard.render.com/u/settings")
            print("      → Get Service ID from the URL of your Render service")
            print("      → Set them in .env as RENDER_API_KEY and RENDER_SERVICE_ID")
            print("      → Skipping Render log scan (use --no-render to suppress this)")
        else:
            logs = _fetch_render_logs(render_service_id, render_api_key, limit=1000)
            print(f"  📋 Fetched {len(logs)} log lines")

            if logs:
                found_events = _extract_click_events_from_logs(logs)

                # Build email_id → row mapping from sheet
                id_to_row = {r["email_id"]: r for r in rows if r["email_id"]}

                for eid, event in found_events.items():
                    if eid in id_to_row:
                        row = id_to_row[eid]
                        event_type = event["type"]
                        event_ts   = event["ts"]

                        if event_type == "click" and not row["clicked"]:
                            print(f"\n  ✅ RECOVERY: Click for {row['name']} <{row['email']}>")
                            print(f"     Render log timestamp: {event_ts}")
                            try:
                                sheet.update_cell(row["row_num"], GCOL_CLICKED, f"recovered~{event_ts[:16]}")
                                recovered[row["email"]] = {"type": "click", "row": row}
                                print(f"     ✅ Written to sheet row {row['row_num']}")
                            except Exception as e:
                                print(f"     ❌ Sheet write failed: {e}")

                        elif event_type == "open" and not row["opened"]:
                            print(f"\n  ✅ RECOVERY: Open for {row['name']} <{row['email']}>")
                            try:
                                sheet.update_cell(row["row_num"], GCOL_OPENED, f"recovered~{event_ts[:16]}")
                                print(f"     ✅ Written to sheet row {row['row_num']}")
                            except Exception as e:
                                print(f"     ❌ Sheet write failed: {e}")
                    else:
                        print(f"  ⚠️  Found ID {eid[:8]}… in logs but NOT in sheet (orphan — safe to ignore)")

                if not found_events:
                    print("  📭 No click/open events found in Render logs")
    else:
        print("\n[2/4] Skipping Render log scan (--no-render flag)")

    # ── 4. Hot Leads: sent but no signal ────────────────────────────
    print(f"\n[3/4] Identifying hot leads (sent + showed engagement)...")

    hot = []
    warm = []  # sent recently but zero signal

    for r in sent_rows:
        if r["reply"]:  # already replied, handled by reply_monitor
            continue
        if r["clicked"] or r["opened"]:
            hot.append(r)   # showed interest — MUST follow up
        else:
            warm.append(r)  # sent but no signal

    print(f"\n  🔥 HOT LEADS (clicked or opened, no reply yet): {len(hot)}")
    if hot:
        print(f"\n  {'Name':<28} {'Email':<33} {'Signal':<12} {'When'}")
        print("  " + "─" * 90)
        for r in hot:
            signal = "🖱 Click" if r["clicked"] else "👁 Open"
            when   = (r["clicked"] or r["opened"])[:16]
            print(f"  {r['name'][:27]:<28} {r['email'][:32]:<33} {signal:<12} {when}")

    print(f"\n  🌡  WARM (sent, zero engagement): {len(warm)}")

    # ── 5. Summary & next steps ──────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Recovered from Render logs : {len(recovered)}")
    print(f"  🔥 Hot leads to follow up  : {len(hot)}")
    print(f"  🌡  Warm to re-engage       : {len(warm)}")
    print()

    if hot:
        print("  NEXT STEP → Run followup_sender.py to automatically")
        print("  send a warm follow-up to all hot leads above.")
        print()
        print("  Or manually email them — they clearly read your message.")

    if not recovered and not NO_RENDER:
        print()
        print("  TIP: To recover the 5 lost clicks, make sure you set:")
        print("    RENDER_API_KEY=rnd_xxxx   (from render.com → Settings → API Keys)")
        print("    RENDER_SERVICE_ID=srv-xxx  (from the URL of your service)")
        print("  Then re-run: python recover_clickers.py")


if __name__ == "__main__":
    run()
