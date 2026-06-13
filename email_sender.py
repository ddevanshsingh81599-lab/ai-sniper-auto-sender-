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
import random
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from gmail_sender import send_email

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────
DAILY_LIMIT       = 30
SEND_WINDOW_START = 9   # 9 AM IST
SEND_WINDOW_END   = 21  # 9 PM IST
DELAY_MIN_SEC     = 8  * 60   # 8 minutes
DELAY_MAX_SEC     = 20 * 60   # 20 minutes
POLL_INTERVAL_SEC = 5  * 60   # 5 minutes (when idle / waiting for next window)

IST = timezone(timedelta(hours=5, minutes=30))

# Sheet column indices (0-based)
COL_NAME       = 0
COL_EMAIL      = 1
COL_ROLE       = 2
COL_SOURCE     = 3
COL_AI_EMAIL   = 13   # N
COL_ANGLE      = 14   # O  — we write subject here too
COL_SENT       = 17   # R

# gspread column numbers (1-based) for update calls
GCOL_ANGLE     = 15   # O
GCOL_SENT      = 18   # R

SHEET_RANGE    = "A2:U"
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

        return i + 2, {   # +2 because sheet is 1-based and we skip header
            "row":      i + 2,
            "name":     row[COL_NAME].strip(),
            "email":    email,
            "role":     row[COL_ROLE].strip(),
            "source":   row[COL_SOURCE].strip(),
            "ai_email": ai_email,
            "angle":    row[COL_ANGLE].strip(),
        }

    return None, None


def _mark_sent(sheet, row_number: int, subject: str):
    """
    Write the sent date to col R and the subject line to col O.
    """
    date_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    # Update col O (Trigger Angle / Subject)
    sheet.update_cell(row_number, GCOL_ANGLE, subject)
    # Update col R (Sent?)
    sheet.update_cell(row_number, GCOL_SENT,  date_str)


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

def _make_subject(contact: dict) -> str:
    """
    Generate a short, role-specific subject line.
    Keeps it under 50 chars and human-sounding.
    """
    role   = (contact.get("role") or "").lower()
    name   = (contact.get("name") or "").split()[0]   # first name only
    source = (contact.get("source") or "").lower()

    if "designer" in role:
        options = [
            f"your design work",
            f"quick thought on your portfolio",
            f"seen your dribbble shots",
        ]
    elif "developer" in role or "dev" in role or "engineer" in role:
        options = [
            f"quick question",
            f"your github projects",
            f"indie dev thing",
        ]
    elif "founder" in role or "indie" in source:
        options = [
            f"honest question",
            f"one thing about invoicing",
            f"your product",
        ]
    elif "writer" in role or "content" in role:
        options = [
            f"quick thought",
            f"your writing work",
            f"one thing",
        ]
    else:
        options = [
            f"quick question",
            f"one thing",
            f"honestly curious",
        ]

    return random.choice(options)


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

            success = send_email(to=to_email, subject=subject, body=body)

            if success:
                _mark_sent(sheet, row_number, subject)
                _sent_today += 1
                print(
                    f"  ✅ Sent {_sent_today}/{DAILY_LIMIT} → {to_email}"
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


if __name__ == "__main__":
    run()
