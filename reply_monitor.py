"""
reply_monitor.py
────────────────
Always-on reply watcher. Runs alongside email_sender.py on Railway.

What it does:
  1. Polls Gmail inbox every 2 minutes for new replies
  2. Matches replies against the outreach emails in the Google Sheet
  3. When a reply is found:
     a. Sends you an INSTANT alert email (to SUMMARY_EMAIL_TO)
     b. Marks col S ("Reply?") in the sheet with timestamp
     c. Generates a contextual follow-up reply via Gemini AI
     d. Sends the follow-up reply automatically
  4. Tracks processed message IDs to never double-process

Gmail scopes needed (already in setup_gmail_auth.py):
  - gmail.send   (for sending follow-up)
  - gmail.modify (for reading inbox)

Usage:
  python reply_monitor.py            # production loop
  python reply_monitor.py --dry-run  # prints what would happen, no sends

Environment variables (all already in .env):
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
  SENDING_EMAIL, GOOGLE_SHEET_ID
  SUMMARY_EMAIL_TO, SUMMARY_EMAIL_FROM, SUMMARY_EMAIL_APP_PASSWORD
  GEMINI_API_KEY
"""

import os
import sys
import json
import time
import base64
import smtplib
import threading
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, BaseHTTPRequestHandler

import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 2 * 60      # check every 2 minutes
IST = timezone(timedelta(hours=5, minutes=30))
DRY_RUN = "--dry-run" in sys.argv

# File to persist processed message IDs across restarts
PROCESSED_IDS_FILE = "processed_reply_ids.json"

# Sheet column indices (0-based for reading)
# Confirmed from live sheet audit 2026-06-18:
#   N=AI Email(13)  O=Subject(14)  R=Sent(17)  S=Reply(18)  U=Notes(20)
COL_NAME     = 0   # A
COL_EMAIL    = 1   # B
COL_ROLE     = 2   # C
COL_AI_EMAIL = 13  # N — real AI email sent
COL_ANGLE    = 14  # O — subject line
COL_SENT     = 17  # R — sent timestamp
COL_REPLY    = 18  # S — reply timestamp

# gspread column numbers (1-based for writing)
GCOL_REPLY   = 19  # S — reply timestamp
GCOL_NOTES   = 21  # U — notes

SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
]


# ── Gmail service ─────────────────────────────────────────────────────────────

def _build_gmail_service():
    """Build authenticated Gmail API service using OAuth refresh token."""
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
        scopes=GMAIL_SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


# ── Google Sheets ─────────────────────────────────────────────────────────────

def _get_sheet():
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    cred_env = os.getenv("GOOGLE_CREDENTIALS")

    if cred_env:
        creds_info = json.loads(cred_env)
        creds = SACredentials.from_service_account_info(creds_info, scopes=SHEET_SCOPES)
    elif os.path.exists("credentials.json"):
        creds = SACredentials.from_service_account_file("credentials.json", scopes=SHEET_SCOPES)
    else:
        raise FileNotFoundError("Google Sheets credentials not found.")

    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


# ── Processed IDs persistence ─────────────────────────────────────────────────

def _load_processed_ids() -> set:
    """Load already-processed Gmail message IDs from disk."""
    if os.path.exists(PROCESSED_IDS_FILE):
        try:
            with open(PROCESSED_IDS_FILE, "r") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, Exception):
            pass
    return set()


def _save_processed_ids(ids: set):
    """Persist processed IDs to disk."""
    with open(PROCESSED_IDS_FILE, "w") as f:
        json.dump(list(ids), f)


# ── Alert email (instant notification to you) ─────────────────────────────────

def _send_alert(contact_name: str, contact_email: str, reply_snippet: str):
    """Send an instant notification email to SUMMARY_EMAIL_TO."""
    sender    = os.getenv("SUMMARY_EMAIL_FROM")
    password  = os.getenv("SUMMARY_EMAIL_APP_PASSWORD")
    recipient = os.getenv("SUMMARY_EMAIL_TO")

    if not all([sender, password, recipient]):
        print("  ⚠️  Alert email skipped (missing SMTP env vars).")
        return

    now = datetime.now(IST).strftime("%H:%M IST")
    subject = f"🔔 Reply from {contact_name} ({now})"

    body = (
        f"You got a reply!\n\n"
        f"From: {contact_name} <{contact_email}>\n"
        f"Time: {now}\n\n"
        f"── Their reply ──\n"
        f"{reply_snippet}\n"
        f"─────────────────\n\n"
        f"Check your inbox and respond if the auto-reply didn't fit."
    )

    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print(f"  📨 Alert sent to {recipient}")
    except Exception as e:
        print(f"  ❌ Alert email failed: {e}")


# ── AI follow-up reply generation ─────────────────────────────────────────────

FOLLOW_UP_PROMPT = """
You are Devansh, founder of Auctron (auctron.in),
replying to someone who responded to your cold outreach
about your invoicing tool.

Their name: {name}
Their role: {role}
What you originally wrote to them:
{original_email}

Their reply:
{their_reply}

Write a short, warm follow-up. Rules:
→ max 60 words
→ acknowledge what they said specifically
→ if they're interested: give them a direct link (auctron.in) and offer a quick call
→ if they said not interested / unsubscribe: thank them genuinely, no pushback
→ if they asked a question: answer it honestly
→ plain text, no formatting
→ casual tone, lowercase start
→ sign off with just "Devansh"
→ no exclamation marks
→ no corporate words (leverage, synergy, etc.)
→ no "feel free" or "don't hesitate"

Output the reply text only. No subject line. No preamble.
"""


def _generate_follow_up(name: str, role: str, original_email: str, their_reply: str) -> str | None:
    """Use Gemini to generate a contextual follow-up reply."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("  ⚠️  GEMINI_API_KEY not set — skipping auto-reply.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = FOLLOW_UP_PROMPT.format(
            name=name[:40],
            role=role[:50],
            original_email=original_email[:500],
            their_reply=their_reply[:500],
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.7,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return response.text.strip()
    except Exception as e:
        print(f"  ❌ Gemini follow-up failed: {e}")
        return None


# ── Send follow-up reply via Gmail API ────────────────────────────────────────

def _send_follow_up(gmail_service, to_email: str, subject: str,
                     body: str, thread_id: str, message_id_header: str) -> bool:
    """Send the AI-generated follow-up as a reply in the same Gmail thread."""
    from_email = os.getenv("SENDING_EMAIL", "")
    if not from_email:
        print("  ❌ SENDING_EMAIL not set.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]       = from_email
        msg["To"]         = to_email
        msg["Subject"]    = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg["In-Reply-To"] = message_id_header
        msg["References"]  = message_id_header
        msg.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail_service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()

        return True
    except Exception as e:
        print(f"  ❌ Follow-up send failed: {e}")
        return False


# ── Core: fetch replies from Gmail ────────────────────────────────────────────

def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    return ""


def _get_header(headers: list, name: str) -> str:
    """Get a specific header value from Gmail message headers."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _fetch_new_replies(gmail_service, sent_emails: dict, processed_ids: set) -> list:
    """
    Query Gmail for replies to our outreach emails.

    sent_emails: dict mapping lowercase email -> row data from sheet
    processed_ids: set of already-handled Gmail message IDs

    Returns list of dicts with reply info.
    """
    sending_email = os.getenv("SENDING_EMAIL", "").lower()
    if not sending_email:
        return []

    replies = []

    try:
        # Search for messages in inbox that are replies (in: inbox, to: our sending email)
        # We look for messages sent TO us (replies) from the people we emailed
        query = f"in:inbox is:unread to:{sending_email}"
        result = gmail_service.users().messages().list(
            userId="me", q=query, maxResults=20
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return []

        for msg_meta in messages:
            msg_id = msg_meta["id"]
            if msg_id in processed_ids:
                continue

            # Fetch the full message
            msg = gmail_service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers   = msg.get("payload", {}).get("headers", [])
            from_raw  = _get_header(headers, "From")
            subject   = _get_header(headers, "Subject")
            msg_id_h  = _get_header(headers, "Message-ID")
            thread_id = msg.get("threadId", "")

            # Extract sender email from "Name <email>" format
            if "<" in from_raw and ">" in from_raw:
                from_email = from_raw.split("<")[1].split(">")[0].lower().strip()
            else:
                from_email = from_raw.lower().strip()

            # Check if this sender is in our outreach list
            if from_email not in sent_emails:
                continue

            # Extract the reply body
            body = _extract_body(msg.get("payload", {}))
            snippet = msg.get("snippet", "")

            replies.append({
                "gmail_msg_id":     msg_id,
                "thread_id":        thread_id,
                "message_id_header": msg_id_h,
                "from_email":       from_email,
                "from_raw":         from_raw,
                "subject":          subject,
                "body":             body[:2000],
                "snippet":          snippet,
                "contact":          sent_emails[from_email],
            })

    except Exception as e:
        print(f"  ⚠️  Gmail fetch error: {e}")

    return replies


# ── Sheet: load sent contacts ─────────────────────────────────────────────────

def _load_sent_contacts(sheet) -> dict:
    """
    Load all rows where col R (Sent?) is non-blank.
    Returns dict: lowercase_email -> {name, role, email, row, original_email, subject, reply_status}
    """
    rows = sheet.get("A2:U")
    if not rows:
        return {}

    contacts = {}
    for i, row in enumerate(rows):
        row = row + [""] * (21 - len(row))

        email = row[COL_EMAIL].strip().lower()
        sent  = row[COL_SENT].strip()
        reply = row[COL_REPLY].strip()

        if not email or not sent:
            continue

        contacts[email] = {
            "name":           row[COL_NAME].strip(),
            "email":          email,
            "role":           row[COL_ROLE].strip(),
            "row":            i + 2,   # 1-based, skip header
            "original_email": row[COL_AI_EMAIL].strip(),
            "subject":        row[COL_ANGLE].strip(),
            "reply_status":   reply,
        }

    return contacts


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("  AUCTRON REPLY MONITOR")
    print(f"  Started: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    if DRY_RUN:
        print("  ⚠️  DRY-RUN mode — no replies will be sent")
    print("=" * 60)

    processed_ids = _load_processed_ids()
    print(f"  📦 Loaded {len(processed_ids)} previously processed message IDs.")

    gmail_service = None
    sheet = None
    consecutive_errors = 0

    while True:
        try:
            # ── Connect services ──────────────────────────────────────
            if gmail_service is None:
                print("  📧 Connecting to Gmail API...")
                gmail_service = _build_gmail_service()
                print("  ✅ Gmail connected.")

            if sheet is None:
                print("  📊 Connecting to Google Sheets...")
                sheet = _get_sheet()
                print("  ✅ Sheet connected.")

            # ── Load sent contacts from sheet ─────────────────────────
            sent_contacts = _load_sent_contacts(sheet)
            if not sent_contacts:
                print(f"  📭 No sent contacts in sheet. Sleeping {POLL_INTERVAL_SEC // 60} min.")
                time.sleep(POLL_INTERVAL_SEC)
                continue

            # ── Check for new replies ─────────────────────────────────
            replies = _fetch_new_replies(gmail_service, sent_contacts, processed_ids)

            if not replies:
                now = datetime.now(IST).strftime("%H:%M")
                print(f"  [{now}] No new replies. Checking again in {POLL_INTERVAL_SEC // 60} min.")
                time.sleep(POLL_INTERVAL_SEC)
                consecutive_errors = 0
                continue

            # ── Process each reply ────────────────────────────────────
            for reply in replies:
                contact = reply["contact"]
                name    = contact["name"]
                email   = reply["from_email"]
                row_num = contact["row"]

                print(f"\n  {'─' * 50}")
                print(f"  🔔 REPLY from {name} <{email}>")
                print(f"  📝 Subject: {reply['subject']}")
                print(f"  💬 Snippet: {reply['snippet'][:150]}")

                if DRY_RUN:
                    print(f"  [DRY-RUN] Would process reply from {email}")
                    processed_ids.add(reply["gmail_msg_id"])
                    continue

                # ── Step 1: Send instant alert to you ─────────────────
                _send_alert(name, email, reply["body"][:1000] or reply["snippet"])

                # ── Step 2: Mark reply in Google Sheet ────────────────
                try:
                    timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
                    sheet.update_cell(row_num, GCOL_REPLY, timestamp)
                    print(f"  ✅ Sheet updated — row {row_num}, col S = {timestamp}")
                except Exception as e:
                    print(f"  ⚠️  Sheet update failed: {e}")

                # ── Step 3: Generate AI follow-up ─────────────────────
                follow_up = _generate_follow_up(
                    name=name,
                    role=contact["role"],
                    original_email=contact["original_email"],
                    their_reply=reply["body"] or reply["snippet"],
                )

                if follow_up:
                    print(f"  🤖 AI follow-up generated ({len(follow_up)} chars)")

                    # ── Step 4: Send the follow-up reply ──────────────
                    success = _send_follow_up(
                        gmail_service=gmail_service,
                        to_email=email,
                        subject=reply["subject"],
                        body=follow_up,
                        thread_id=reply["thread_id"],
                        message_id_header=reply["message_id_header"],
                    )

                    if success:
                        print(f"  ✅ Follow-up sent to {email}")
                        # Add note to sheet
                        try:
                            sheet.update_cell(
                                row_num, GCOL_NOTES,
                                f"Auto-replied {timestamp}"
                            )
                        except Exception:
                            pass
                    else:
                        print(f"  ❌ Follow-up send failed for {email}")
                else:
                    print(f"  ⚠️  No AI follow-up generated — reply manually")

                # ── Mark as read in Gmail ─────────────────────────────
                try:
                    gmail_service.users().messages().modify(
                        userId="me",
                        id=reply["gmail_msg_id"],
                        body={"removeLabelIds": ["UNREAD"]},
                    ).execute()
                except Exception:
                    pass  # non-critical

                # ── Mark as processed ─────────────────────────────────
                processed_ids.add(reply["gmail_msg_id"])

                # Small delay between processing multiple replies
                time.sleep(15)

            # ── Persist processed IDs ─────────────────────────────────
            _save_processed_ids(processed_ids)
            consecutive_errors = 0
            time.sleep(POLL_INTERVAL_SEC)

        except KeyboardInterrupt:
            print("\n  👋 Reply monitor stopped by user.")
            _save_processed_ids(processed_ids)
            break

        except Exception as e:
            consecutive_errors += 1
            backoff = min(300, 60 * consecutive_errors)  # max 5 min backoff
            print(f"  ⚠️  Error: {e}. Retrying in {backoff}s (error #{consecutive_errors})")
            gmail_service = None
            sheet = None
            time.sleep(backoff)


# ── Health check server (separate port from email_sender) ─────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Auctron reply monitor is running.")

    def log_message(self, *args):
        pass


def _start_health_server():
    port = int(os.getenv("REPLY_MONITOR_PORT", 8081))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  🩺 Health-check server on port {port}")


if __name__ == "__main__":
    _start_health_server()
    run()
