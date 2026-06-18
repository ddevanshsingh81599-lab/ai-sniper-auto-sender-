"""
setup_gmail_auth.py
───────────────────
Run this ONE TIME locally on your Mac.

  python setup_gmail_auth.py

A browser window opens. Sign in with your Google Workspace email
(e.g. you@yourdomain.com). Click Allow.

The script then prints:
  GMAIL_REFRESH_TOKEN = "1//0g..."

Copy that value. Paste it into:
  1. Your .env file (GMAIL_REFRESH_TOKEN=...)
  2. Your Railway environment variables (same key)

You will NEVER need to run this script again.
The refresh token does not expire unless you revoke it.

This script reads GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET
directly from your .env — no separate credentials.json needed.
"""

import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def main():
    client_id     = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌  GMAIL_CLIENT_ID or GMAIL_CLIENT_SECRET missing in .env")
        return

    # Build the client config dict (same structure as credentials.json)
    client_config = {
        "installed": {
            "client_id":                  client_id,
            "client_secret":              client_secret,
            "auth_uri":                   "https://accounts.google.com/o/oauth2/auth",
            "token_uri":                  "https://oauth2.googleapis.com/token",
            "redirect_uris":              ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    print("\n  Opening browser for Google sign-in...")
    print("  → Sign in with your Workspace email (the one you'll send FROM)")
    print("  → Click Allow on the permissions screen\n")

    # Opens browser for consent
    creds = flow.run_local_server(port=0)

    print("\n" + "=" * 65)
    print("  ✅  Auth complete!")
    print("=" * 65)
    print("\n  Copy this into your .env and Railway env vars:\n")
    print(f"  GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
    print("\n" + "=" * 65)
    print("  Also fill in your sending email address:")
    print("  SENDING_EMAIL = you@yourdomain.com")
    print("=" * 65)
    print("\n  ⚠️  Keep these secret. Do not commit to git.\n")


if __name__ == "__main__":
    main()
