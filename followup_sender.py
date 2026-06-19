"""
followup_sender.py
──────────────────
Scans your Google Sheet for "hot leads" — people who:
  - Were sent an email (col R is non-blank)
  - Have opened OR clicked (col W or X is non-blank)
  - Have NOT replied yet (col S is blank)
  - Have NOT already been followed up (col U doesn't say "followed-up")

For each hot lead, generates a short, warm follow-up email via Gemini AI
and sends it via Gmail. Marks col U with "followed-up YYYY-MM-DD".

USAGE
─────
    python followup_sender.py              # real run — sends emails
    python followup_sender.py --dry-run    # prints what would be sent, no sends
    python followup_sender.py --show-only  # just show who needs follow-up

DAILY LIMIT: max 20 follow-up emails per run to stay safe with Gmail.
"""

import os
import sys
import json
import time
import base64
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

IST       = timezone(timedelta(hours=5, minutes=30))
DRY_RUN   = "--dry-run"   in sys.argv
SHOW_ONLY = "--show-only" in sys.argv
DAILY_CAP = 20

# Sheet columns (0-based)
COL_NAME    = 0   # A
COL_EMAIL   = 1   # B
COL_ROLE    = 2   # C
COL_SOURCE  = 3   # D
COL_AI_EMAIL= 13  # N
COL_ANGLE   = 14  # O  (subject line used)
COL_SENT    = 17  # R
COL_REPLY   = 18  # S
COL_NOTES   = 20  # U
COL_EMAIL_ID= 21  # V
COL_OPENED  = 22  # W
COL_CLICKED = 23  # X

# gspread (1-based)
GCOL_NOTES = 21   # U

SCOPES_SHEET = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GMAIL = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
]


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_sheet():
    cred_env = os.getenv("GOOGLE_CREDENTIALS")
    if cred_env:
        creds = SACredentials.from_service_account_info(json.loads(cred_env), scopes=SCOPES_SHEET)
    else:
        creds = SACredentials.from_service_account_file("credentials.json", scopes=SCOPES_SHEET)
    return gspread.authorize(creds).open_by_key(os.getenv("GOOGLE_SHEET_ID")).sheet1


def _build_gmail():
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
        scopes=SCOPES_GMAIL,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


# ── Load hot leads from sheet ─────────────────────────────────────────────────

def _load_hot_leads(sheet) -> list[dict]:
    """
    Returns rows where:
      - Sent? (R) is non-blank
      - Opened? (W) OR Clicked? (X) is non-blank
      - Reply? (S) is blank           (not already replied)
      - Notes (U) doesn't start with "followed-up"  (not already followed up)
    """
    rows = sheet.get("A2:X") or []
    hot  = []

    for i, row in enumerate(rows):
        row = row + [""] * (24 - len(row))

        sent    = row[COL_SENT].strip()
        reply   = row[COL_REPLY].strip()
        notes   = row[COL_NOTES].strip().lower()
        opened  = row[COL_OPENED].strip()
        clicked = row[COL_CLICKED].strip()
        email   = row[COL_EMAIL].strip()

        if not sent:       continue    # not sent
        if not email:      continue    # no email
        if reply:          continue    # already replied
        if not (opened or clicked): continue  # no engagement signal
        if notes.startswith("followed-up"):   continue  # already followed up

        signal = "click" if clicked else "open"
        when   = (clicked or opened)[:16]

        hot.append({
            "row_num":      i + 2,
            "name":         row[COL_NAME].strip(),
            "email":        email,
            "role":         row[COL_ROLE].strip(),
            "source":       row[COL_SOURCE].strip(),
            "original_body":row[COL_AI_EMAIL].strip(),
            "subject_used": row[COL_ANGLE].strip(),
            "sent_at":      sent[:10],
            "signal":       signal,
            "signal_at":    when,
            "opened":       opened,
            "clicked":      clicked,
        })

    return hot


# ── Generate follow-up email via Gemini ──────────────────────────────────────

FOLLOWUP_PROMPT = """\
You are Devansh, founder of Auctron — an AI-powered invoicing tool for freelancers (auctron.net.in).

You sent a cold outreach email to this person. They {signal_description} — which means they're interested but didn't reply.

Person:
  Name   : {name}
  Role   : {role}
  Source : {source}
  Signal : {signal}  (at {signal_at})

Your original email to them:
{original_body}

Write a SHORT follow-up email (NOT a reply — a fresh nudge). Rules:
→ Max 5 sentences total
→ Reference that you noticed they checked out the link (but do it naturally, not creepy)
→ Ask ONE simple question or make ONE simple offer (e.g., "happy to show you a quick 2 min demo")
→ Include auctron.net.in once
→ Plain text only, no formatting
→ Lowercase start, casual tone
→ Sign off: just "Devansh"
→ No exclamation marks
→ No "just following up" — say something fresh
→ No "feel free", "don't hesitate", "leverage", "synergy"

Output the email body only. No subject line. No preamble.
"""


def _generate_followup(lead: dict) -> str | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    signal_desc = (
        "clicked the link in your email" if lead["signal"] == "click"
        else "opened your email"
    )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = FOLLOWUP_PROMPT.format(
            name=lead["name"][:40] or "there",
            role=lead["role"][:50] or "freelancer",
            source=lead["source"][:30],
            signal=lead["signal"],
            signal_at=lead["signal_at"],
            signal_description=signal_desc,
            original_body=lead["original_body"][:600],
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=250,
                temperature=0.75,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return response.text.strip()
    except Exception as e:
        print(f"  ⚠️  Gemini error: {e}")
        return None


# ── Send follow-up via Gmail API ──────────────────────────────────────────────

_SUBJECT_POOL = [
    "one more thing",
    "still thinking about this?",
    "wanted to check in",
    "quick one",
    "still relevant?",
    "had a thought",
    "noticed you checked it out",
    "auctron — still relevant?",
    "saw you looked",
    "following up differently",
]

import random

def _make_subject(lead: dict) -> str:
    base = random.choice(_SUBJECT_POOL)
    # 25% chance to personalise
    if lead["name"] and random.random() < 0.25:
        first = lead["name"].split()[0]
        return f"{first} — {base}"
    return base


def _send_via_gmail(gmail_service, to: str, subject: str, body: str) -> bool:
    from_email = os.getenv("SENDING_EMAIL", "")
    if not from_email:
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = from_email
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail_service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return True
    except Exception as e:
        print(f"  ❌ Gmail send failed: {e}")
        return False


def _send_alert(name: str, email: str, body_preview: str):
    """Notify you that a follow-up was sent."""
    sender    = os.getenv("SUMMARY_EMAIL_FROM")
    password  = os.getenv("SUMMARY_EMAIL_APP_PASSWORD")
    recipient = os.getenv("SUMMARY_EMAIL_TO")
    if not all([sender, password, recipient]):
        return

    now = datetime.now(IST).strftime("%H:%M IST")
    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = f"📤 Follow-up sent to {name} ({now})"
    msg.attach(MIMEText(
        f"Sent a follow-up to:\n  {name} <{email}>\n\nBody preview:\n{body_preview[:300]}\n",
        "plain"
    ))
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587)
        s.starttls()
        s.login(sender, password)
        s.send_message(msg)
        s.quit()
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def run(silent_mode: bool = False):
    if not silent_mode:
        print("=" * 60)
        print("  AUCTRON — HOT LEAD FOLLOW-UP SENDER")
        print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
        if DRY_RUN:
            print("  ⚠️  DRY-RUN — no emails will be sent")
        if SHOW_ONLY:
            print("  ℹ️  SHOW-ONLY — just listing, no sends")
        print("=" * 60)

    # ── 1. Connect ─────────────────────────────────────────────────────
    if not silent_mode:
        print("\n[1/3] Connecting to Google Sheets...")
    sheet = _get_sheet()
    if not silent_mode:
        print("  ✅ Sheet connected")

    if not DRY_RUN and not SHOW_ONLY:
        if not silent_mode:
            print("[1/3] Connecting to Gmail...")
        gmail = _build_gmail()
        if not silent_mode:
            print("  ✅ Gmail connected")
    else:
        gmail = None

    # ── 2. Find hot leads ─────────────────────────────────────────────
    if not silent_mode:
        print("\n[2/3] Scanning for hot leads (opened or clicked, no reply)...")
    hot_leads = _load_hot_leads(sheet)

    if not hot_leads:
        if not silent_mode:
            print("\n  📫 No hot leads found right now.")
        return

    print(f"\n  🔥 Found {len(hot_leads)} hot lead(s) to follow up:\n")
    print(f"  {'#':<3} {'Name':<26} {'Email':<33} {'Signal':<10} {'When'}")
    print("  " + "─" * 90)
    for idx, lead in enumerate(hot_leads, 1):
        signal_emoji = "🖱" if lead["signal"] == "click" else "👁"
        print(
            f"  {idx:<3} {lead['name'][:25]:<26} {lead['email'][:32]:<33} "
            f"{signal_emoji} {lead['signal']:<8} {lead['signal_at']}"
        )

    if SHOW_ONLY:
        print(f"\n  Run without --show-only to send follow-ups.")
        return

    # ── 3. Generate + send follow-ups ────────────────────────────
    print(f"\n[3/3] Generating and sending follow-ups (cap: {DAILY_CAP})...")
    sent_count = 0

    for lead in hot_leads:
        if sent_count >= DAILY_CAP:
            print(f"\n  🛑 Reached daily cap of {DAILY_CAP}. Stopping.")
            break

        name  = lead["name"] or "there"
        email = lead["email"]
        print(f"\n  ── Lead {sent_count + 1} ──")
        print(f"  👤 {name} <{email}>  [{lead['signal']} signal at {lead['signal_at']}]")

        # Generate AI follow-up
        body = _generate_followup(lead)
        if not body:
            print(f"  ⚠️  Gemini failed — skipping {email}")
            continue

        subject = _make_subject(lead)
        print(f"  📝 Subject: {subject}")
        print(f"  📄 Body preview: {body[:120]}...")

        if DRY_RUN:
            print("  [DRY-RUN] Would send the above.")
            sent_count += 1
            continue

        # Send
        ok = _send_via_gmail(gmail, email, subject, body)
        if ok:
            # Mark in sheet
            ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
            note = f"followed-up {ts}"
            try:
                sheet.update_cell(lead["row_num"], GCOL_NOTES, note)
            except Exception as e:
                print(f"  ⚠️  Sheet note failed: {e}")

            # Alert you
            _send_alert(name, email, body)

            sent_count += 1
            print(f"  ✅ Sent + marked in sheet row {lead['row_num']}")

            # Human-like delay
            if sent_count < len(hot_leads):
                delay = random.randint(90, 300)  # 1.5–5 min gap
                print(f"  ⏳ Waiting {delay//60}m {delay%60}s before next send...")
                time.sleep(delay)
        else:
            print(f"  ❌ Failed to send to {email}")

    # ── Done ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  ✅ Follow-ups sent: {sent_count}")
    print(f"  Sheet URL: https://docs.google.com/spreadsheets/d/{os.getenv('GOOGLE_SHEET_ID')}/edit")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()
