"""
clean_emails.py
═══════════════
One-time cleanup script. Reads the Google Sheet, validates every email,
and DELETES rows with invalid emails.

Usage:
    python clean_emails.py --dry-run    # preview only (no changes)
    python clean_emails.py              # actually delete bad rows

What it checks:
    1. "Email" prefix from scraper bugs (Emailfoo@bar.com → foo@bar.com)
    2. Blocked domains (instagram.com, company.com, gov, etc.)
    3. Business/agency prefixes (info@, support@, etc.)
    4. DNS/MX record validation (does the domain actually accept mail?)
"""

import os
import sys
import json
import time
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from email_validator_utils import validate_email_address, fix_email_prefix

load_dotenv()

DRY_RUN = "--dry-run" in sys.argv

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheet():
    """Connect to Google Sheets."""
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    cred_env = os.getenv("GOOGLE_CREDENTIALS")

    if cred_env:
        creds_info = json.loads(cred_env)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    elif os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    else:
        raise FileNotFoundError("Google Sheets credentials not found.")

    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def main():
    print("=" * 60)
    print("  AUCTRON EMAIL CLEANER")
    print("=" * 60)
    if DRY_RUN:
        print("  ⚠️  DRY-RUN mode — no changes will be made\n")
    else:
        print("  🔴 LIVE mode — invalid rows WILL be deleted\n")

    # ── Connect ────────────────────────────────────────────────────────
    print("[1/4] Connecting to Google Sheets...")
    sheet = get_sheet()
    print("  ✅ Connected.\n")

    # ── Read all data ──────────────────────────────────────────────────
    print("[2/4] Reading all rows...")
    all_rows = sheet.get("A2:U")  # skip header
    total = len(all_rows)
    print(f"  📊 Found {total} rows (excluding header).\n")

    if not all_rows:
        print("  ⚠️  Sheet is empty — nothing to clean.")
        return

    # ── Validate each row ──────────────────────────────────────────────
    print("[3/4] Validating emails...\n")

    # Track results
    valid_rows = []       # (row_index_1based, email) — keep
    invalid_rows = []     # (row_index_1based, email, reason) — delete
    fixed_rows = []       # (row_index_1based, old_email, new_email) — fix in-place

    for i, row in enumerate(all_rows):
        row_num = i + 2  # 1-based, skip header

        # Pad short rows
        row = row + [""] * (21 - len(row))
        raw_email = row[1].strip()  # Column B = Email Address

        if not raw_email:
            invalid_rows.append((row_num, "(empty)", "no email address"))
            continue

        # Check if "Email" prefix needs fixing
        fixed = fix_email_prefix(raw_email)
        email_was_fixed = (fixed != raw_email)

        # Run full validation (including DNS/MX)
        is_valid, cleaned, reason = validate_email_address(
            fixed, check_dns=True, check_business=True
        )

        if is_valid:
            if email_was_fixed:
                fixed_rows.append((row_num, raw_email, cleaned))
                print(f"  🔧 Row {row_num}: FIXED {raw_email} → {cleaned}")
            valid_rows.append((row_num, cleaned))
        else:
            invalid_rows.append((row_num, raw_email, reason))
            print(f"  ❌ Row {row_num}: {raw_email} — {reason}")

        # Rate limit DNS lookups (avoid hammering resolvers)
        if (i + 1) % 20 == 0:
            print(f"     ... processed {i + 1}/{total} rows ...")
            time.sleep(0.5)

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  VALIDATION RESULTS")
    print("=" * 60)
    print(f"  ✅ Valid emails:   {len(valid_rows)}")
    print(f"  🔧 Fixed emails:  {len(fixed_rows)}")
    print(f"  ❌ Invalid emails: {len(invalid_rows)}")
    print(f"  📊 Total rows:    {total}")

    if invalid_rows:
        print("\n  Invalid email details:")
        for row_num, email, reason in invalid_rows:
            print(f"    Row {row_num}: {email} — {reason}")

    if fixed_rows:
        print("\n  Fixed email details:")
        for row_num, old, new in fixed_rows:
            print(f"    Row {row_num}: {old} → {new}")

    if DRY_RUN:
        print("\n  ⚠️  DRY-RUN — no changes made. Run without --dry-run to apply.")
        return

    # ── Apply changes ──────────────────────────────────────────────────
    print(f"\n[4/4] Applying changes...")

    # Step 1: Fix "Email" prefix emails in-place
    for row_num, old_email, new_email in fixed_rows:
        try:
            sheet.update_cell(row_num, 2, new_email)  # Col B = 2
            print(f"  🔧 Fixed row {row_num}: {old_email} → {new_email}")
            time.sleep(0.3)  # Sheets API rate limit
        except Exception as e:
            print(f"  ⚠️  Failed to fix row {row_num}: {e}")

    # Step 2: Delete invalid rows (bottom-up to preserve row numbers)
    if invalid_rows:
        print(f"\n  🗑️  Deleting {len(invalid_rows)} invalid rows...")
        # Sort by row number descending — delete from bottom up
        rows_to_delete = sorted(
            [row_num for row_num, _, _ in invalid_rows],
            reverse=True,
        )

        for row_num in rows_to_delete:
            try:
                sheet.delete_rows(row_num)
                print(f"  🗑️  Deleted row {row_num}")
                time.sleep(0.3)  # Sheets API rate limit
            except Exception as e:
                print(f"  ⚠️  Failed to delete row {row_num}: {e}")

    print(f"\n  ✅ Cleanup complete!")
    print(f"  📊 Rows remaining: ~{len(valid_rows)}")


if __name__ == "__main__":
    main()
