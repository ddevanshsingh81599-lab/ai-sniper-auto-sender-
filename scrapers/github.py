"""
GitHub Scraper — Authenticated (5000 req/hr rate limit)
Targets USA and European freelancers using 3 strategies:

  Strategy 1: Search users by location (USA/Europe cities) + freelance bio keywords
  Strategy 2: Search users by bio keywords ("freelance", "available for hire")
  Strategy 3: Extract email from commit events (most reliable source)

Requires: GITHUB_TOKEN in .env
"""
import os
import re
import time
from dotenv import load_dotenv
import requests

load_dotenv()


def _headers():
    """Return authenticated headers. Falls back to unauthenticated if no token."""
    token = os.getenv("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github.v3+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    else:
        print("  ⚠️  GITHUB_TOKEN not set — rate limit is 60 req/hr (unauthenticated)")
    return h


def _get(url, params=None, timeout=12):
    """Safe GET wrapper with rate-limit handling."""
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        if resp.status_code == 403:
            reset = resp.headers.get("X-RateLimit-Reset", "")
            print(f"  ⚠️  GitHub 403 rate limit. Reset at {reset}. Waiting 30s...")
            time.sleep(30)
            resp = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        print(f"  HTTP error {e}")
        return None
    except Exception as e:
        print(f"  Request error: {e}")
        return None


def _extract_email_from_events(username):
    """
    Looks through a user's public push events to find a real commit email.
    This is the most reliable way to get emails from GitHub.
    """
    data = _get(f"https://api.github.com/users/{username}/events/public")
    if not data:
        return ""
    for event in data[:20]:
        if event.get("type") == "PushEvent":
            commits = event.get("payload", {}).get("commits", [])
            for commit in commits:
                email = commit.get("author", {}).get("email", "")
                if (
                    email
                    and "@" in email
                    and "noreply" not in email.lower()
                    and "users.noreply.github" not in email.lower()
                    and "example" not in email.lower()
                ):
                    return email
    return ""


def _fetch_user_detail(item):
    """
    Fetch full profile for a search result item.
    Returns a contact dict or None.
    """
    username = item.get("login")
    if not username:
        return None

    user_url = item.get("url") or f"https://api.github.com/users/{username}"
    user_data = _get(user_url)
    if not user_data:
        return None

    name  = user_data.get("name") or username
    bio   = user_data.get("bio") or ""
    email = user_data.get("email") or ""
    blog  = user_data.get("blog") or ""
    loc   = user_data.get("location") or ""

    FAKE = {"you@email.com","your@email.com","email@example.com",
            "user@example.com","test@test.com","name@email.com","noreply@github.com"}
    if email.lower() in FAKE:
        email = ""

    # Strategy A: email in bio
    if not email and bio:
        m = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', bio)
        if m:
            email = m.group(0)

    # Strategy B: email in commit events (most reliable)
    if not email:
        email = _extract_email_from_events(username)

    if blog and not blog.startswith("http"):
        blog = "https://" + blog

    return {
        "name": name,
        "username": username,
        "role": "Developer",
        "bio": bio[:150],
        "email": email,
        "url": blog or user_data.get("html_url", f"https://github.com/{username}"),
        "location": loc,
        "source": "GitHub",
    }


def scrape_github(limit=30):
    """
    Searches GitHub for freelance developers in USA and Europe using
    authenticated requests (5000 req/hr).

    Two search modes:
      1. location: filter — targets users who set a USA/Europe location
      2. bio/keyword — finds users with 'freelance' or 'available for hire' in bio
    """
    print("Starting GitHub Scraper (authenticated)...")
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("  ⚠️  No GITHUB_TOKEN — results will be very limited.")

    contacts = []
    seen_usernames = set()
    search_url = "https://api.github.com/search/users"

    # ── Location-based queries: city + freelance keyword in bio ─────
    location_queries = [
        # USA cities
        'location:"New York" "freelance" in:bio',
        'location:"San Francisco" "freelance" in:bio',
        'location:"Los Angeles" "freelance" in:bio',
        'location:"Austin" "freelance" in:bio',
        'location:"Chicago" "freelance" in:bio',
        'location:"Seattle" "freelance" in:bio',
        # Europe cities
        'location:"London" "freelance" in:bio',
        'location:"Berlin" "freelance" in:bio',
        'location:"Amsterdam" "freelance" in:bio',
        'location:"Paris" "freelance" in:bio',
        'location:"Barcelona" "freelance" in:bio',
        'location:"Stockholm" "freelance" in:bio',
        'location:"Dublin" "freelance" in:bio',
        'location:"Lisbon" "freelance" in:bio',
        # Available for hire variants
        'location:"New York" "available for hire" in:bio',
        'location:"London" "available for hire" in:bio',
        'location:"Berlin" "available" "developer" in:bio',
    ]

    # ── Keyword-only queries (catches people who put USA/Europe in bio) ─
    keyword_queries = [
        '"freelance developer" "USA" in:bio',
        '"freelance designer" "USA" in:bio',
        '"freelance developer" "Europe" in:bio',
        '"freelance designer" "Europe" in:bio',
        '"available for hire" "freelance" "USA" in:bio',
        '"available for hire" "freelance" "Europe" in:bio',
        '"open to freelance" in:bio',
        '"hire me" "developer" in:bio',
    ]

    all_queries = location_queries + keyword_queries
    print(f"  Total search queries: {len(all_queries)}")

    for query in all_queries:
        if len(contacts) >= limit:
            break

        print(f"  Searching: {query[:70]}...")
        data = _get(search_url, params={"q": query, "per_page": 10, "sort": "joined", "order": "desc"})
        if not data:
            continue

        items = data.get("items", [])
        print(f"    → {len(items)} results")

        for item in items:
            if len(contacts) >= limit:
                break

            username = item.get("login")
            if not username or username in seen_usernames:
                continue
            seen_usernames.add(username)

            print(f"    Fetching: @{username}...")
            contact = _fetch_user_detail(item)
            if contact:
                contacts.append(contact)

            time.sleep(0.3)   # polite delay — well within 5000/hr limit

        time.sleep(1)   # small pause between search queries

    print(f"\nGitHub Scraper finished. Found {len(contacts)} contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    print(f"  → {emails_found} contacts have emails.")
    return contacts


if __name__ == "__main__":
    load_dotenv()
    results = scrape_github(limit=10)
    for r in results:
        print(f"  {r['name']} ({r.get('location','?')}) | {r.get('email','NO EMAIL')} | {r['url']}")
