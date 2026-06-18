"""
gmail_sender.py
───────────────
Thin Gmail API wrapper.
Uses OAuth2 (Client ID + Secret + Refresh Token) — no browser needed after
the one-time setup_gmail_auth.py step.

Environment variables required:
  GMAIL_CLIENT_ID
  GMAIL_CLIENT_SECRET
  GMAIL_REFRESH_TOKEN
  SENDING_EMAIL
"""

import os
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _build_service():
    """
    Build and return an authenticated Gmail API service.
    Uses the refresh token stored in env vars — never needs a browser
    after the one-time setup step.
    """
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
        scopes=SCOPES,
    )

    # Refresh to get a valid access token
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def send_email(to: str, subject: str, body: str, html_body: str = None) -> bool:
    """
    Send a plain-text (and optionally HTML) email via Gmail API.
    When html_body is provided, sends a multipart/alternative message so
    email clients render the HTML version (with tracking pixel + UTM links)
    while plain-text clients still get the readable version.
    Returns True on success, False on failure.
    """
    from_email = os.getenv("SENDING_EMAIL", "")
    if not from_email:
        print("  ❌ SENDING_EMAIL env var not set.")
        return False

    try:
        service = _build_service()

        msg = MIMEMultipart("alternative")
        msg["From"]    = from_email
        msg["To"]      = to
        msg["Subject"] = subject

        # Plain text part (always included — fallback for plain-text clients)
        msg.attach(MIMEText(body, "plain"))

        # HTML part — carries tracking pixel + UTM links
        # Must be attached LAST so email clients prefer it over plain text
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        return True

    except Exception as e:
        print(f"  ❌ Gmail API error: {e}")
        return False
