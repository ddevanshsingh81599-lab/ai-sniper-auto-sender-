import os
import time
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv
from sheets_manager import SheetsManager
from ai_agent import generate_personalized_email
from profile_extractor import extract_profile
from email_validator_utils import validate_email_address

load_dotenv()

MAX_CONTACTS_PER_DAY = 50

SCRAPER_REGISTRY = [
    {
        "name": "GitHub",
        "module": "scrapers.github",
        "function": "scrape_github",
        "kwargs": {"limit": 30},
    },
    {
        "name": "Contra",
        "module": "scrapers.contra",
        "function": "scrape_contra",
        "kwargs": {"limit": 20},
    },
    {
        "name": "Dev.to (India + Invoice)",
        "module": "scrapers.dev_to",
        "function": "scrape_dev_to",
        "kwargs": {"limit": 40},
    },
    {
        "name": "IndieHackers",
        "module": "scrapers.indiehackers",
        "function": "scrape_indiehackers",
        "kwargs": {"limit": 20},
    },
    {
        "name": "Serper (Google Search)",
        "module": "scrapers.serper_search",
        "function": "scrape_serper",
        "kwargs": {"limit_per_query": 10},
    },
]


def run_scraper(entry):
    """
    Dynamically imports and runs a scraper.
    Returns (contacts_list, error_string_or_None).
    If the scraper crashes, returns ([], error_string) so the
    bot can continue with the next source.
    """
    name = entry["name"]
    try:
        import importlib
        mod = importlib.import_module(entry["module"])
        fn = getattr(mod, entry["function"])
        contacts = fn(**entry["kwargs"])
        return contacts, None
    except Exception as e:
        err = f"{name}: {e}"
        print(f"  ⚠️  {name} FAILED — {e}")
        traceback.print_exc()
        return [], err


def send_summary_email(added_count, sources_stats, total_contacts, errors):
    sender = os.getenv("SUMMARY_EMAIL_FROM")
    password = os.getenv("SUMMARY_EMAIL_APP_PASSWORD")
    recipient = os.getenv("SUMMARY_EMAIL_TO")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{os.getenv('GOOGLE_SHEET_ID')}/edit"

    if not sender or not password or not recipient:
        print("Skipping summary email (missing App Password in .env)")
        return

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    body  = f"Date of run: {date_str}\n"
    body += f"Total new contacts added today: {added_count}\n\n"
    body += f"Breakdown by source:\n"
    for src, cnt in sources_stats.items():
        body += f"  • {src}: {cnt}\n"
    body += f"\nTotal contacts in sheet so far: {total_contacts}\n"
    body += f"\nFailed sources:\n"
    if errors:
        for err in errors:
            body += f"  ❌ {err}\n"
    else:
        body += "  ✅ All sources ran successfully\n"
    body += f"\nGoogle Sheet: {sheet_url}\n"

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"Auctron Outreach — Daily Report ({date_str})"
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("✅ Summary email sent successfully!")
    except Exception as e:
        print(f"Failed to send summary email: {e}")


def main():
    print("=" * 60)
    print("  AUCTRON FREELANCER OUTREACH BOT")
    print(f"  Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── 1. Connect to Google Sheets ──────────────────────────────
    print("\n[1/4] Connecting to Google Sheets...")
    try:
        sheets = SheetsManager()
        print("  ✅ Google Sheets connected.")
    except Exception as e:
        print(f"  ❌ FATAL: Cannot connect to Google Sheets: {e}")
        return

    # ── 2. Run all scrapers with fallback ────────────────────────
    print("\n[2/4] Scraping contacts from all sources...")
    scraped_contacts = []
    failed_sources = []
    successful_sources = []

    for entry in SCRAPER_REGISTRY:
        print(f"\n  ▶ Running {entry['name']} scraper...")
        contacts, error = run_scraper(entry)

        if error:
            failed_sources.append(error)
            print(f"  ⏭️  Skipping {entry['name']}, moving to next source.")
        else:
            count = len(contacts)
            successful_sources.append(f"{entry['name']} ({count} contacts)")
            scraped_contacts.extend(contacts)
            print(f"  ✅ {entry['name']} returned {count} contacts.")

    print(f"\n  ── Scraping Summary ──")
    print(f"  Total raw contacts: {len(scraped_contacts)}")
    print(f"  Sources OK:   {', '.join(successful_sources) or 'None'}")
    print(f"  Sources FAIL: {', '.join(failed_sources) or 'None'}")

    if not scraped_contacts:
        print("\n  ⚠️  No contacts scraped from any source. Nothing to process.")
        return

    # ── 3. Filter, generate AI emails, push to Sheets ────────────
    # NOTE: If Gemini fails/rate-limits, contact is still pushed with
    # a blank AI email (marked PENDING). Nothing is lost.
    print(f"\n[3/4] Processing contacts (max {MAX_CONTACTS_PER_DAY}/day)...")
    added_today = 0
    sources_breakdown = {}
    skipped_no_email = 0
    skipped_duplicate = 0
    pushed_without_ai = 0
    skipped_business_email = 0
    skipped_invalid_email = 0

    for contact in scraped_contacts:
        if added_today >= MAX_CONTACTS_PER_DAY:
            print(f"\n  🛑 Reached daily limit of {MAX_CONTACTS_PER_DAY}. Stopping.")
            break

        email = contact.get("email")
        if not email:
            skipped_no_email += 1
            continue

        # Filter out obvious business/agency emails
        # Hard block: these prefixes are NEVER personal
        hard_block_prefixes = (
            "info@", "sales@", "support@", "team@",
            "admin@", "help@", "office@", "marketing@", "press@",
            "careers@", "jobs@", "enquiries@", "noreply@", "no-reply@",
        )
        # Soft block: only block if domain looks like an agency/company
        soft_block_prefixes = ("hello@", "hi@", "contact@",)
        agency_domain_keywords = (
            "studio", "agency", "design", "creative", "labs", "media",
            "solutions", "group", "digital", "works", "brand", "collective",
        )

        email_lower = email.lower()
        is_business = False

        if email_lower.startswith(hard_block_prefixes):
            is_business = True
        elif email_lower.startswith(soft_block_prefixes):
            # Only block if domain has a company-sounding name
            domain_part = email_lower.split("@")[-1].split(".")[0]
            if any(kw in domain_part for kw in agency_domain_keywords):
                is_business = True

        if is_business:
            print(f"  ⏭️  Skipping {email} (business/agency email)")
            skipped_business_email += 1
            continue

        # ── Email validation gate (syntax + DNS/MX) ──────────────────
        is_valid, cleaned_email, rejection_reason = validate_email_address(
            email, check_dns=True, check_business=False  # business already checked above
        )
        if not is_valid:
            print(f"  ⏭️  Skipping {email} (invalid: {rejection_reason})")
            skipped_invalid_email += 1
            continue
        # Use the cleaned/normalized email going forward
        contact['email'] = cleaned_email
        email = cleaned_email

        if sheets.is_duplicate(email):
            skipped_duplicate += 1
            continue

        name = contact.get("name", "Freelancer")
        print(f"  ✉️  Generating email for {name} ({email})...")

        # ── Step A: Extract structured profile (segment / pain points / etc.) ──
        # Falls back gracefully if Gemini fails — never loses the lead.
        extracted_profile = None
        try:
            extracted_profile = extract_profile(contact)
            print(
                f"  🔍 Profile extracted → "
                f"segment={extracted_profile.get('segment')} | "
                f"angle={extracted_profile.get('bestAngle')} | "
                f"confidence={extracted_profile.get('confidence')}"
            )
        except Exception as e:
            print(f"  ⚠️  Profile extractor crashed for {name} ({e}) — continuing without it.")

        # ── Step B: Generate personalised outreach email ──────────────────────
        # If Gemini fails for ANY reason, fall back to blank/PENDING.
        # Nothing is ever lost.
        ai_email = ""
        angle_used = "PENDING"
        try:
            result, angle_used = generate_personalized_email(contact)
            if "ERROR" not in result:
                ai_email = result
            else:
                print(f"  ⚠️  Gemini error for {name} — pushing contact without AI email.")
                pushed_without_ai += 1
        except Exception as e:
            print(f"  ⚠️  Gemini crashed for {name} ({e}) — pushing contact without AI email.")
            pushed_without_ai += 1

        # Always push — even if AI email is blank
        success = sheets.add_contact(contact, ai_email, angle_used, extracted_profile)
        if success:
            added_today += 1
            src = contact.get("source", "Unknown")
            sources_breakdown[src] = sources_breakdown.get(src, 0) + 1
            time.sleep(0.5)  # light rate-limit buffer

    # ── 4. Print final report ────────────────────────────────────
    print("\n" + "=" * 60)
    print("  RUN COMPLETE")
    print("=" * 60)
    print(f"  ✅ New contacts added:    {added_today}")
    print(f"  ⏭️  Skipped (no email):    {skipped_no_email}")
    print(f"  ⏭️  Skipped (business):    {skipped_business_email}")
    print(f"  ⏭️  Skipped (invalid):     {skipped_invalid_email}")
    print(f"  ⏭️  Skipped (duplicate):   {skipped_duplicate}")
    print(f"  📭 Pushed (no AI email):  {pushed_without_ai}  ← fill manually in sheet")
    print(f"  📊 Breakdown: {sources_breakdown}")
    if failed_sources:
        print(f"  ❌ Failed sources: {failed_sources}")

    # ── 5. Send summary email ────────────────────────────────────
    print("\n[4/4] Sending summary email...")
    try:
        total_in_sheet = len(sheets.sheet.col_values(2)) - 1
    except:
        total_in_sheet = "unknown"

    send_summary_email(
        added_count=added_today,
        sources_stats=sources_breakdown,
        total_contacts=total_in_sheet,
        errors=failed_sources,
    )


if __name__ == "__main__":
    main()
