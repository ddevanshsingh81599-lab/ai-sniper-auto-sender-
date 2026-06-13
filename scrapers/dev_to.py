"""
Dev.to Scraper — Expanded Targeting
=====================================
Uses the Dev.to API to find developers writing about freelancing, invoicing,
and billing pain points. Prioritises India but includes any developer without
an explicit non-Indian location, maximising lead volume.
"""
import os
import requests
import re
import time
from scrapers.email_hunter import scan_website_for_email, email_from_github_commits

def scrape_dev_to(limit=40):
    print("Starting Dev.to Scraper (Expanded — India + Invoice/Freelance targeting)...")
    contacts = []
    
    api_key = os.getenv("DEV_TO_API_KEY")
    headers = {"api-key": api_key} if api_key else {}
    
    # 1. Expanded query set — invoice/billing pain points + freelancing India
    search_url = "https://dev.to/api/articles"
    queries = [
        # Original India-focused queries
        "freelance India",
        "hire me India",
        "freelancer India",
        "open to work India",
        # Invoice & billing pain points (directly relevant to product)
        "freelance invoice",
        "invoicing freelancers",
        "billing clients freelance",
        "invoice tool developer",
        # Availability signals
        "available for work freelance",
        "looking for freelance work",
        "remote freelance developer",
        "freelance web developer portfolio",
    ]
    
    seen_usernames = set()
    
    for query in queries:
        if len(contacts) >= limit:
            break
            
        params = {"q": query, "per_page": 30}
        
        try:
            resp = requests.get(search_url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            articles = resp.json()
        except Exception as e:
            print(f"  Dev.to article search failed for '{query}': {e}")
            continue
            
        for article in articles:
            if len(contacts) >= limit:
                break
                
            user = article.get("user", {})
            username = user.get("username")
            
            if not username or username in seen_usernames:
                continue
            seen_usernames.add(username)
            
            # 2. Fetch full user profile
            try:
                user_url = f"https://dev.to/api/users/by_username?url={username}"
                user_resp = requests.get(user_url, headers=headers, timeout=10)
                if user_resp.status_code != 200:
                    continue
                    
                user_data = user_resp.json()
                
                name = user_data.get("name") or username
                bio = user_data.get("summary") or ""
                website = user_data.get("website_url") or ""
                location = user_data.get("location") or ""
                github_user = user_data.get("github_details") or "" 
                twitter_user = user_data.get("twitter_username") or ""
                
                # Loose India filter on location if it exists
                if location and "india" not in location.lower() and "in" not in location.lower().split(","):
                    # If location explicitly states somewhere else, skip
                    if any(x in location.lower() for x in ["usa", "uk", "europe", "canada", "australia", "nigeria", "germany"]):
                        continue
                
                print(f"  @{username} 📍 {location or 'Unknown'}")
                
                # Check for email in bio first
                email = ""
                method = "none"
                
                if bio:
                    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', bio)
                    if email_match:
                        email = email_match.group(0)
                        method = "bio"
                        print(f"    ✅ bio → {email}")
                
                # Step 1: scan website
                if not email and website and "dev.to" not in website and "github.com" not in website:
                    print(f"    → Scanning website: {website[:55]}")
                    email = scan_website_for_email(website)
                    if email:
                        method = "website"
                        print(f"    ✅ website → {email}")
                
                # Step 2: Github commits
                if not email and github_user:
                    print(f"    → GitHub commits: @{github_user}")
                    email = email_from_github_commits(github_user)
                    if email:
                        method = "github_commits"
                        print(f"    ✅ github_commits → {email}")
                
                if not email:
                    print(f"    ✗ no email found")
                
                contact = {
                    "name": name,
                    "role": "Developer Writer",
                    "bio": bio[:150],
                    "email": email,
                    "url": website or f"https://dev.to/{username}",
                    "location": location,
                    "source": "Dev.to"
                }
                contacts.append(contact)
                time.sleep(0.5) # rate limit buffer
                
            except Exception as e:
                print(f"  Error fetching Dev.to user {username}: {e}")
                
    print(f"\\nDev.to Scraper finished. Found {len(contacts)} contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    pct = int(emails_found / max(len(contacts), 1) * 100)
    print(f"  → {emails_found} have emails ({pct}% hit rate)")
    return contacts

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    results = scrape_dev_to(limit=5)
    print("\\n=== DEV.TO RESULTS ===")
    for r in results:
        print(f"  {r['name']} | {r.get('location')} | {r.get('email', 'NO EMAIL')} | {r['url']}")
