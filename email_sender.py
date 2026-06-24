"""
email_sender.py
───────────────
Always-on sender loop. Runs 24/7 on Railway.

What it does:
  1. Reads Google Sheet (same sheet as main.py)
  2. Finds rows where col N (AI Email) is ready AND col R (Sent?) is blank
  3. Sends email via Gmail API
  4. Writes sent-date to col R and subject to col O
  5. Sleeps 8-20 min between sends (human-like cadence)
  6. Only sends inside 9 AM – 9 PM IST window
  7. Caps at 30 emails per day
  8. Skips weekends

Sheet column index reference (0-based for list access, 1-based for gspread):
  0=A  Full Name         1=B  Email
  2=C  Role              3=D  Source
  4=E  Bio               5=F  Segment
  6=G  Pain Points       7=H  Current Tools
  8=I  Client Type       9=J  Experience
  10=K Best Angle        11=L Is Freelancer
  12=M Confidence
  13=N AI Generated Email   (must be non-blank, non-PENDING to send)
  14=O Trigger Angle / Subject Written Back
  15=P Profile URL
  16=Q Date Added
  17=R Sent?             ← blank = not sent; write date here after sending
  18=S Reply?
  19=T Signed Up?
  20=U Notes

Usage:
  python email_sender.py            # production loop
  python email_sender.py --dry-run  # prints what would be sent, no actual email
"""

import os
import sys
import time
import uuid
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from gmail_sender import send_email
from email_validator_utils import validate_email_quick
from email_tracker import inject_tracking, register_email_id, start_tracker_server

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────
DAILY_LIMIT       = 30
SEND_WINDOW_START = 9   # 9 AM IST
SEND_WINDOW_END   = 21  # 9 PM IST
DELAY_MIN_SEC     = 8  * 60   # 8 minutes
DELAY_MAX_SEC     = 20 * 60   # 20 minutes
POLL_INTERVAL_SEC = 5  * 60   # 5 minutes (when idle / waiting for next window)

IST = timezone(timedelta(hours=5, minutes=30))

# ── Sheet column indices (0-based for reading rows) ─────────────────────────
# Confirmed from live sheet audit 2026-06-18:
#   A=Full Name  B=Email  C=Role  D=Source  E=Bio
#   F=AI Draft   G=AngleCode  H=ProfileURL  I=DateAdded
#   J=Segment    K=PainPoints  L=CurrentTools  M=ClientType
#   N=AI Generated Email (REAL)   O=Subject Sent
#   P=Experience  Q=BestAngle
#   R=Sent?   S=Reply?   T=SignedUp?   U=Notes
#   V=Email ID   W=Opened?   X=Clicked?
COL_NAME       = 0    # A
COL_EMAIL      = 1    # B
COL_ROLE       = 2    # C
COL_SOURCE     = 3    # D
COL_AI_EMAIL   = 13   # N — real full AI email
COL_ANGLE      = 14   # O — subject line used
COL_SENT       = 17   # R — sent timestamp

# ── gspread column numbers (1-based) for update calls ───────────────────────
GCOL_ANGLE     = 15   # O — subject sent
GCOL_SENT      = 18   # R — sent timestamp
GCOL_REPLY     = 19   # S — reply timestamp (written by reply_monitor)
GCOL_NOTES     = 21   # U — notes / confidence
GCOL_EMAIL_ID  = 22   # V — UUID for tracking
GCOL_OPENED    = 23   # W — open pixel timestamp
GCOL_CLICKED   = 24   # X — click timestamp

# Tracker base URL — set this to your Railway/public URL
TRACKER_BASE_URL = os.getenv("TRACKER_BASE_URL", "http://localhost:8082")

SHEET_RANGE    = "A2:X"
DRY_RUN        = "--dry-run" in sys.argv

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ── Sheet helpers ─────────────────────────────────────────────────────────────

def _get_sheet():
    sheet_id  = os.getenv("GOOGLE_SHEET_ID")
    cred_path = "credentials.json"

    # Try loading from the environment variable first (for Railway)
    cred_env = os.getenv("GOOGLE_CREDENTIALS")
    
    if cred_env:
        import json
        creds_info = json.loads(cred_env)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    elif os.path.exists(cred_path):
        # Fallback to local file
        creds = Credentials.from_service_account_file(cred_path, scopes=SCOPES)
    else:
        raise FileNotFoundError(
            "Google Sheets credentials not found. "
            "Please provide GOOGLE_CREDENTIALS in env vars or credentials.json file."
        )

    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def _get_next_contact(sheet):
    """
    Read the sheet and return the first row that:
      - has a non-blank AI email (col N)
      - AI email is not 'PENDING'
      - Sent? (col R) is blank
    Returns (row_number_1based, contact_dict) or (None, None).
    """
    rows = sheet.get(SHEET_RANGE)
    if not rows:
        return None, None

    for i, row in enumerate(rows):
        # Pad short rows to avoid index errors
        row = row + [""] * (21 - len(row))

        ai_email = row[COL_AI_EMAIL].strip()
        sent     = row[COL_SENT].strip()
        email    = row[COL_EMAIL].strip()

        if not ai_email or ai_email.upper() == "PENDING":
            continue
        if sent:
            continue
        if not email:
            continue

        # Quick email validation safety net
        is_valid, cleaned, reason = validate_email_quick(email)
        if not is_valid:
            print(f"  ⚠️  Skipping row {i + 2}: {email} — {reason}")
            continue

        return i + 2, {   # +2 because sheet is 1-based and we skip header
            "row":      i + 2,
            "name":     row[COL_NAME].strip(),
            "email":    cleaned,  # use validated/cleaned email
            "role":     row[COL_ROLE].strip(),
            "source":   row[COL_SOURCE].strip(),
            "ai_email": ai_email,
            "angle":    row[COL_ANGLE].strip(),
        }

    return None, None


def _mark_sent(sheet, row_number: int, subject: str, email_id: str):
    """
    Write the sent date to col R, subject line to col O, and email_id to col V.
    """
    date_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    # Update col O (Trigger Angle / Subject)
    sheet.update_cell(row_number, GCOL_ANGLE, subject)
    # Update col R (Sent?)
    sheet.update_cell(row_number, GCOL_SENT,  date_str)
    # Update col V (Email ID for tracking)
    if email_id:
        register_email_id(sheet, row_number, email_id)


# ── Time helpers ──────────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    return datetime.now(IST)


def _is_weekend() -> bool:
    return _ist_now().weekday() >= 5   # 5=Sat, 6=Sun


def _in_send_window() -> bool:
    hour = _ist_now().hour
    return SEND_WINDOW_START <= hour < SEND_WINDOW_END


def _seconds_until_window() -> int:
    """
    How many seconds until 9 AM IST tomorrow (or today if we're before 9 AM).
    """
    now = _ist_now()
    target = now.replace(hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return int((target - now).total_seconds())


def _random_delay() -> int:
    return random.randint(DELAY_MIN_SEC, DELAY_MAX_SEC)


# ── Subject line generator ────────────────────────────────────────────────────

# Large pool of human-sounding, lowercase subjects.
# Gmail flags identical subjects sent in bulk — so we need variety.
# Rules: no caps, no exclamation marks, no corporate words, max 6 words.

_SUBJECT_POOL = [
    # generic / curiosity
    "quick question",
    "one thing",
    "honestly curious",
    "random thought",
    "quick thing",
    "small ask",
    "two minute question",
    "not sure if this fits",
    "might be useful",
    "thought of you",
    "no pitch just a question",
    "this might help",
    "one sec",
    "wanted your take",
    "you'd know better",
    # invoicing / billing angle
    "invoicing thing",
    "billing question",
    "gst stuff",
    "about getting paid",
    "payment headaches",
    "invoice tool",
    "freelancer billing",
    # role-aware
    "saw your work",
    "your projects",
    "your portfolio",
    "fellow freelancer here",
    "founder to founder",
    "indie dev thing",
    "designer question",
    "dev to dev",
    # soft ask
    "would you try something",
    "curious what you think",
    "worth 3 minutes maybe",
    "can i get your opinion",
    "honest feedback",
    "need a second pair of eyes",
    "your honest take",
]

# Subjects that include the first name for extra personalization
_SUBJECT_POOL_WITH_NAME = [
    "{first_name} — quick question",
    "{first_name} — one thing",
    "{first_name} — curious about something",
    "{first_name} — small ask",
    "for {first_name}",
    "hey {first_name} — quick thing",
]


def _make_subject(contact: dict) -> str:
    """
    Pick a random subject line from a large pool.
    ~30% chance of including their first name for personalization.
    Never the same subject twice in a row (tracked in _last_subject).
    """
    global _last_subject

    first_name = (contact.get("name") or "").split()[0]

    # 30% chance to use a name-personalized subject
    if first_name and random.random() < 0.3:
        pool = _SUBJECT_POOL_WITH_NAME
    else:
        pool = _SUBJECT_POOL

    # Pick randomly, but avoid repeating the last subject
    attempts = 0
    while attempts < 5:
        choice = random.choice(pool)
        subj = choice.format(first_name=first_name) if "{first_name}" in choice else choice
        if subj != _last_subject:
            _last_subject = subj
            return subj
        attempts += 1

    # Fallback (should never hit this)
    _last_subject = subj
    return subj


_last_subject = ""


# ── Daily counter ─────────────────────────────────────────────────────────────

_sent_today = 0
_last_date  = _ist_now().strftime("%Y-%m-%d")


def _check_daily_reset():
    global _sent_today, _last_date
    today = _ist_now().strftime("%Y-%m-%d")
    if today != _last_date:
        _sent_today = 0
        _last_date  = today
        print(f"  📅 New day ({today}) — daily counter reset.")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    global _sent_today

    print("=" * 60)
    print("  AUCTRON EMAIL SENDER")
    print(f"  Started: {_ist_now().strftime('%Y-%m-%d %H:%M IST')}")
    if DRY_RUN:
        print("  ⚠️  DRY-RUN mode — no emails will actually be sent")
    print("=" * 60)

    sheet = None

    while True:
        try:
            _check_daily_reset()

            # ── Weekend check ────────────────────────────────────────
            if _is_weekend():
                day = _ist_now().strftime("%A")
                print(f"  😴 {day} — resting. Checking again in 1 hour.")
                time.sleep(3600)
                continue

            # ── Window check ─────────────────────────────────────────
            if not _in_send_window():
                wait = _seconds_until_window()
                h, m = divmod(wait // 60, 60)
                print(
                    f"  🕐 Outside window (9 AM–9 PM IST). "
                    f"Sleeping {h}h {m}m until next window."
                )
                time.sleep(wait)
                continue

            # ── Daily cap check ──────────────────────────────────────
            if _sent_today >= DAILY_LIMIT:
                print(
                    f"  🛑 Daily limit reached ({_sent_today}/{DAILY_LIMIT}). "
                    f"Sleeping 1 hour."
                )
                time.sleep(3600)
                continue

            # ── Connect / reconnect sheet ────────────────────────────
            if sheet is None:
                print("  📊 Connecting to Google Sheets...")
                sheet = _get_sheet()
                print("  ✅ Sheet connected.")

            # ── Find next contact ────────────────────────────────────
            row_number, contact = _get_next_contact(sheet)

            if contact is None:
                print(
                    f"  📭 No ready contacts found. "
                    f"Sleeping {POLL_INTERVAL_SEC // 60} min."
                )
                time.sleep(POLL_INTERVAL_SEC)
                continue

            # ── Build subject + send ─────────────────────────────────
            subject  = _make_subject(contact)
            to_email = contact["email"]
            body     = contact["ai_email"]

            print(
                f"\n  ── Preparing send {_sent_today + 1}/{DAILY_LIMIT} ──"
                f"\n  👤 {contact['name']} ({to_email})"
                f"\n  📝 Subject: {subject}"
            )

            if DRY_RUN:
                print("  [DRY-RUN] Would send:")
                print(f"  To:      {to_email}")
                print(f"  Subject: {subject}")
                print(f"  Body:\n{body[:300]}...")
                _sent_today += 1
                time.sleep(5)
                continue

            # ── Generate unique tracking ID ─────────────────────────
            email_id = str(uuid.uuid4())

            # ── Inject tracking pixel + UTM links ───────────────────
            html_body = inject_tracking(body, email_id, TRACKER_BASE_URL)

            success = send_email(to=to_email, subject=subject, body=body, html_body=html_body)

            if success:
                _mark_sent(sheet, row_number, subject, email_id)
                _sent_today += 1
                print(
                    f"  ✅ Sent {_sent_today}/{DAILY_LIMIT} → {to_email}"
                    f"  [tracking ID: {email_id[:8]}…]"
                )

                # ── Human-like delay before next send ───────────────
                delay    = _random_delay()
                mins     = delay // 60
                secs     = delay % 60
                next_at  = (_ist_now() + timedelta(seconds=delay)).strftime("%H:%M IST")
                print(
                    f"  ⏳ Next send in ~{mins}m {secs}s "
                    f"(around {next_at})"
                )
                time.sleep(delay)

            else:
                print(f"  ❌ Send failed for {to_email} — will retry next cycle.")
                # Short sleep before retry
                time.sleep(POLL_INTERVAL_SEC)

        except gspread.exceptions.APIError as e:
            print(f"  ⚠️  Sheets API error: {e}. Reconnecting in 2 min.")
            sheet = None
            time.sleep(120)

        except KeyboardInterrupt:
            print("\n  👋 Sender stopped by user.")
            break

        except Exception as e:
            print(f"  ⚠️  Unexpected error: {e}. Retrying in 2 min.")
            time.sleep(120)


# Health checks are now handled directly by the tracker server's /health endpoint


# ── Auto Follow-up Loop ───────────────────────────────────────────────────────
# Runs inside a background thread every FOLLOWUP_INTERVAL_SEC.
# Automatically emails anyone who clicked/opened but hasn't replied,
# so zero manual intervention needed when hot leads appear.

FOLLOWUP_INTERVAL_SEC = 60 * 60   # check every 1 hour


def _followup_loop():
    """
    Background thread: every hour, scan the sheet for hot leads
    (opened or clicked, no reply, not yet followed up) and send
    a short warm follow-up email via Gmail.
    Integrated from followup_sender.py.
    """
    import followup_sender
    # Stagger start by 5 min so it doesn't race with the sender on startup
    time.sleep(5 * 60)
    print("  🔥 Follow-up loop started (runs every 60 min).")

    while True:
        try:
            now_str = _ist_now().strftime("%H:%M IST")
            print(f"  [{now_str}] Follow-up loop: scanning for hot leads...")
            followup_sender.run(silent_mode=False)  # FIX: show output so you can debug
        except Exception as e:
            import traceback
            print(f"  ⚠️  Follow-up loop error: {e}")
            traceback.print_exc()
        time.sleep(FOLLOWUP_INTERVAL_SEC)


if __name__ == "__main__":
    # 1. Connect to sheet early so tracker cache is pre-loaded at startup.
    #    This prevents cold-start cache misses where opens/clicks are silently dropped.
    print("  📊 Pre-loading sheet for tracker cache...")
    try:
        _startup_sheet = _get_sheet()
    except Exception as _e:
        print(f"  ⚠️  Could not pre-load sheet: {_e} — tracker will use live fallback.")
        _startup_sheet = None

    # 2. Start the email tracking server (also acts as the Render health check on $PORT)
    #    Pass the sheet so _load_id_cache runs immediately — no more cold-start blank opens.
    start_tracker_server(sheet=_startup_sheet)

    # 3. Start the keep-alive pinger (prevents Render cold-start data loss)
    import keep_alive
    keep_alive.start_keep_alive()

    # 4. Start the Reply Monitor in a background thread
    import reply_monitor
    monitor_thread = threading.Thread(target=reply_monitor.run, daemon=True)
    monitor_thread.start()
    print("  🔁 Reply Monitor started in background.")

    # 5. Start the Follow-up sender loop in a background thread
    #    → auto-emails anyone who opened/clicked but hasn't replied
    followup_thread = threading.Thread(target=_followup_loop, daemon=True)
    followup_thread.start()
    print("  🔥 Follow-up loop started in background (runs every 60 min).")

    # 6. Start the main Sender loop in the main thread
    run()
