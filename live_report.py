import os, sys
sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import json
from collections import defaultdict
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

cred_env = os.getenv("GOOGLE_CREDENTIALS")
if cred_env:
    creds = Credentials.from_service_account_info(json.loads(cred_env), scopes=SCOPES)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

client = gspread.authorize(creds)
sheet  = client.open_by_key(os.getenv("GOOGLE_SHEET_ID")).sheet1

print("✅ Connected. Fetching live data...")
rows = sheet.get("A1:X")
if not rows:
    print("Sheet is empty!"); exit()

headers = rows[0]
data = []
for row in rows[1:]:
    row = row + [""] * (max(len(headers), 24) - len(row))
    data.append(dict(zip(headers, row)))

# --- Buckets ---
sent     = [r for r in data if r.get("Sent?","").strip()]
unsent   = [r for r in data if not r.get("Sent?","").strip()
            and r.get("AI Generated Email","").strip()
            and r.get("AI Generated Email","").strip() not in ("PENDING","N/A","")]
replied  = [r for r in data if r.get("Reply?","").strip()]
opened   = [r for r in data if r.get("Opened?","").strip()]
clicked  = [r for r in data if r.get("Clicked?","").strip()]
signedup = [r for r in data if r.get("Signed Up?","").strip()]
tracked  = [r for r in sent if r.get("Email ID","").strip()]

sent_by_source = defaultdict(int)
for r in sent:
    sent_by_source[r.get("Source","Unknown")] += 1

open_rate  = (len(opened)/len(sent)*100)  if sent else 0
reply_rate = (len(replied)/len(sent)*100) if sent else 0
click_rate = (len(clicked)/len(sent)*100) if sent else 0

print("\n" + "=" * 65)
print("   AUCTRON LIVE OUTREACH REPORT")
print("=" * 65)
print(f"\n{'📊 Total in sheet':<30}: {len(data)}")
print(f"{'📤 Sent':<30}: {len(sent)}")
print(f"{'📬 Queued (AI ready, unsent)':<30}: {len(unsent)}")
print(f"{'🔑 Tracking IDs written':<30}: {len(tracked)}")
print()
print(f"{'📨 Replies':<30}: {len(replied)}")
print(f"{'👁️  Opens (pixel)':<30}: {len(opened)}")
print(f"{'🖱️  Clicks (link)':<30}: {len(clicked)}")
print(f"{'✅ Signed Up':<30}: {len(signedup)}")
print()
print(f"{'📈 Open Rate':<30}: {open_rate:.1f}%  (of {len(sent)} sent)")
print(f"{'📈 Reply Rate':<30}: {reply_rate:.1f}%")
print(f"{'📈 Click Rate':<30}: {click_rate:.1f}%")

print(f"\n{'─'*65}")
print(f"📂 SENT BY SOURCE:")
for src, cnt in sorted(sent_by_source.items(), key=lambda x: -x[1]):
    pct = cnt/len(sent)*100 if sent else 0
    print(f"   {src:<30} {cnt:>4}  ({pct:.0f}%)")

print(f"\n{'─'*65}")
print(f"📤 ALL SENT EMAILS ({len(sent)} total):")
print(f"\n{'Name':<28} {'Email':<33} {'Sent':<12} {'Reply':<8} {'Open':<8} {'Click'}")
print("─" * 105)
for r in sent:
    name  = r.get("Full Name","?")[:27]
    email = r.get("Email Address","?")[:32]
    sdate = r.get("Sent?","")[:10]
    rep   = "✅ YES" if r.get("Reply?","").strip()   else "—"
    opn   = "👁️ YES" if r.get("Opened?","").strip()  else "—"
    clk   = "🖱️ YES" if r.get("Clicked?","").strip() else "—"
    print(f"{name:<28} {email:<33} {sdate:<12} {rep:<8} {opn:<8} {clk}")

if replied:
    print(f"\n{'─'*65}")
    print(f"🔔 REPLIES ({len(replied)}):")
    for r in replied:
        print(f"\n   👤 {r.get('Full Name')} <{r.get('Email Address')}>")
        print(f"      Replied at : {r.get('Reply?')}")
        print(f"      Sent at    : {r.get('Sent?')}")
        print(f"      Opened?    : {r.get('Opened?','—')}")
        if r.get("Notes","").strip():
            print(f"      Notes      : {r.get('Notes')}")
else:
    print(f"\n   (no replies yet)")

if opened:
    print(f"\n{'─'*65}")
    print(f"👁️  OPENS ({len(opened)}):")
    for r in opened:
        print(f"   {r.get('Full Name','?'):<28} <{r.get('Email Address','?')}> — {r.get('Opened?')}")

if clicked:
    print(f"\n{'─'*65}")
    print(f"🖱️  CLICKS ({len(clicked)}):")
    for r in clicked:
        print(f"   {r.get('Full Name','?'):<28} <{r.get('Email Address','?')}> — {r.get('Clicked?')}")

print(f"\n{'='*65}")
print("  Report complete.")
print(f"{'='*65}")
