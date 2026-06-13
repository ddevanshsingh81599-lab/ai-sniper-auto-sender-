"""
Serper.dev Google Search scraper.
Uses the Serper API (serper.dev) instead of Google Custom Search.
"""
import os
import re
import requests
import random
from dotenv import load_dotenv

load_dotenv()


def serper_search(query, num=10, page=1):
    """Call Serper.dev API and return organic results."""
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        print("  ⚠️  SERPER_API_KEY not set in .env")
        return []

    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": num, "page": page}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("organic", [])
    except Exception as e:
        print(f"  Serper API error for '{query}': {e}")
        return []


def scrape_serper(limit_per_query=5):
    """
    Runs targeted Google searches via Serper.dev to find
    USA and European freelancers with contact info.

    Credit budget: ≤75 credits per run.
    Each query at num=10 = 1 credit → max 75 queries.
    """
    CREDIT_CAP = 75          # hard stop — never exceed this per run
    credits_used = 0

    print("Starting Serper Google Search Scraper (USA & Europe targeting)...")
    print(f"  Credit budget: {CREDIT_CAP} max")
    contacts = []
    seen_urls = set()

    # ── 50 highest-value queries (sorted: warmest leads first) ──────
    # Each query = 1 Serper credit at num≤10
    queries = [
        # 🔥 Warmest — email directly visible in snippet
        '"@gmail.com" "freelance" "designer" USA',
        '"@gmail.com" "freelance" "developer" USA',
        '"@gmail.com" "freelance" "designer" Europe',
        '"@gmail.com" "freelance" "developer" Europe',
        '"@gmail.com" "freelance" USA portfolio site:carrd.co OR site:framer.app',
        '"@yahoo.com" "freelance" USA portfolio',
        '"@gmail.com" "freelance" Europe portfolio',
        '"@yahoo.com" "freelance" Europe portfolio',

        # 🔥 Invoice pain point — most relevant to your product
        '"invoice" freelancer USA problem email',
        '"invoice" freelancer Europe problem email',
        '"freelancer" USA "billing" "invoice" contact',
        '"freelancer" Europe "billing" "invoice" contact',

        # 🔥 Explicit "hire me" intent
        '"hire me" "freelancer" USA portfolio email',
        '"hire me" "freelancer" Europe portfolio email',
        '"open to work" "freelance" USA "get in touch"',
        '"open to work" "freelance" Europe "get in touch"',

        # 🔥 Email phrase in snippet
        '"reach me at" "freelance" USA portfolio',
        '"reach me at" "freelance" Europe portfolio',
        '"get in touch" email "freelance" USA 2024 2025',
        '"get in touch" email "freelance" Europe 2024 2025',
        '"available for projects" USA "email me"',
        '"available for projects" Europe "email me"',

        # Portfolio platforms (high email yield)
        'site:carrd.co "freelance" USA email',
        'site:carrd.co "freelance" Europe email',
        'site:github.io "freelance" USA email contact',
        'site:github.io "freelance" Europe email contact',
        'site:notion.site "freelance" USA "contact"',
        'site:notion.site "freelance" Europe "contact"',
        'site:framer.app "freelance" USA contact',
        'site:framer.app "freelance" Europe contact',
        'site:webflow.io "freelance" USA "hire"',
        'site:webflow.io "freelance" Europe "hire"',
        'site:read.cv "USA" "freelance" "designer"',
        'site:contra.com "USA" "available"',

        # Contact-intent phrases
        '"contact me" "available for freelance" USA',
        '"contact me" "available for freelance" Europe',
        '"freelance developer" USA "contact me" email',
        '"freelance designer" USA "you can reach me"',
        '"i am a freelance" USA email portfolio',
        '"i am a freelance" Europe email portfolio',

        # USA cities
        '"freelance designer" "New York" email contact portfolio',
        '"freelance developer" "San Francisco" email contact',
        '"freelance designer" "Los Angeles" email contact',
        '"freelance developer" "Austin" email contact',

        # Europe cities
        '"freelance designer" "London" email contact portfolio',
        '"freelance developer" "Berlin" email contact',
        '"freelance designer" "Amsterdam" email contact',
        '"freelance developer" "Paris" email contact',
        '"freelance" "Barcelona" developer designer email portfolio',
        '"freelance" "Stockholm" developer designer email contact',
        '"freelance" "Dublin" developer designer email contact',
    ]
    # Total: 50 queries = 50 credits at num≤10 (well under 75 cap)
    
    # The twist: shuffle the queries so a different subset is used first
    random.shuffle(queries)

    for query in queries:
        if credits_used >= CREDIT_CAP:
            print(f"  🛑 Credit cap ({CREDIT_CAP}) reached — stopping early.")
            break

        # The twist: pick a random page between 1 and 4 to find new contacts each run
        random_page = random.randint(1, 4)
        print(f"  [{credits_used+1}/{CREDIT_CAP}] Searching: {query[:60]}... (Page {random_page})")
        results = serper_search(query, num=limit_per_query, page=random_page)
        credits_used += 1                # 1 credit per query at num≤10

        for item in results:
            url = item.get("link", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = item.get("title", "")
            snippet = item.get("snippet", "")

            # Try to extract email from snippet
            email = ""
            email_match = re.search(
                r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', snippet
            )
            if email_match:
                email = email_match.group(0)

            # Clean up name from title
            name = title.split("|")[0].split("-")[0].split("·")[0].strip()
            if not name or len(name) > 60:
                name = "Freelancer"

            contact = {
                "name": name,
                "role": "Freelancer",
                "bio": snippet[:150],
                "email": email,
                "url": url,
                "source": "Google (Serper)",
            }
            contacts.append(contact)

    print(f"Serper Scraper finished. Credits used: {credits_used}/{CREDIT_CAP}")
    print(f"  Total contacts found: {len(contacts)}")
    emails_found = sum(1 for c in contacts if c.get("email"))
    print(f"  → {emails_found} have emails.")
    return contacts


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    results = scrape_serper(limit_per_query=3)
    for r in results:
        print(f"  {r['name']} | {r.get('email','NO EMAIL')} | {r['url']}")
