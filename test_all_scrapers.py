"""
Diagnostic: Run ALL scrapers individually and report how many contacts
and emails each one finds. Does NOT write to Google Sheets.
"""
import importlib
import traceback
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

SCRAPERS = [
    {"name": "GitHub",                "module": "scrapers.github",        "function": "scrape_github",       "kwargs": {"limit": 25}},
    {"name": "Serper (Google Search)","module": "scrapers.serper_search", "function": "scrape_serper",       "kwargs": {"limit_per_query": 10}},
    {"name": "Peerlist",              "module": "scrapers.peerlist",       "function": "scrape_peerlist",     "kwargs": {"limit": 20}},
    {"name": "Dev.to",                "module": "scrapers.dev_to",         "function": "scrape_dev_to",       "kwargs": {"limit": 25}},
    {"name": "Hashnode",              "module": "scrapers.hashnode",       "function": "scrape_hashnode",     "kwargs": {"limit": 20}},
    {"name": "IndieHackers",          "module": "scrapers.indiehackers",   "function": "scrape_indiehackers", "kwargs": {"limit": 20}},
]

print("=" * 70)
print(f"  SCRAPER DIAGNOSTIC RUN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

summary = []
all_emails = []

for entry in SCRAPERS:
    name = entry["name"]
    print(f"\n{'─'*70}")
    print(f"  ▶ {name}")
    print(f"{'─'*70}")
    try:
        mod = importlib.import_module(entry["module"])
        fn  = getattr(mod, entry["function"])
        contacts = fn(**entry["kwargs"])

        total   = len(contacts)
        emails  = [c for c in contacts if c.get("email")]
        no_mail = total - len(emails)

        print(f"\n  📊 RESULT  → {total} contacts  |  {len(emails)} have email  |  {no_mail} no-email")

        for c in emails:
            print(f"     ✉  {c.get('name','?'):30s}  {c['email']}")
            all_emails.append({"source": name, "name": c.get("name","?"), "email": c["email"]})

        summary.append({"source": name, "total": total, "emails": len(emails), "status": "OK"})

    except Exception as e:
        print(f"  ❌ FAILED — {e}")
        traceback.print_exc()
        summary.append({"source": name, "total": 0, "emails": 0, "status": f"FAILED: {e}"})

# ── Grand summary ──────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  GRAND SUMMARY")
print("=" * 70)
print(f"  {'Source':<28} {'Total':>6}  {'Emails':>6}  Status")
print(f"  {'-'*28}  {'-'*6}  {'-'*6}  {'-'*20}")
for row in summary:
    status_icon = "✅" if row["status"] == "OK" else "❌"
    print(f"  {row['source']:<28} {row['total']:>6}  {row['emails']:>6}  {status_icon} {row['status']}")

total_contacts = sum(r["total"] for r in summary)
total_emails   = sum(r["emails"] for r in summary)
print(f"\n  TOTAL: {total_contacts} contacts found,  {total_emails} with email addresses")

print(f"\n  All {total_emails} emails collected:")
for i, e in enumerate(all_emails, 1):
    print(f"    {i:>3}. [{e['source']}]  {e['name']:30s}  {e['email']}")

print("\n" + "=" * 70)
print("  DIAGNOSTIC COMPLETE")
print("=" * 70)
