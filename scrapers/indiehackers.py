"""
IndieHackers Scraper — with Email Waterfall
============================================
Strategy: Use Serper to find real IH interview and profile pages
instead of parsing the dynamic /people page (which mixes nav links
with real user cards making it unreliable).

Serper searches → real IH profile URLs → visit page → extract
product URL, website, GitHub, Twitter → email waterfall.

Expected email hit rate: ~70%
"""
import asyncio
import re
import requests
import os
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from scrapers.email_hunter import (
    scan_website_for_email,
    email_from_github_commits,
    email_from_twitter_serper,
    extract_links_from_html,
)

load_dotenv()

# Serper queries that reliably return real IH user pages
# Sorted best-first: interviews and products pages have the highest yield
IH_SERPER_QUERIES = [
    'site:indiehackers.com/interviews "freelance"',
    'site:indiehackers.com/products "developer" OR "designer"',
    'site:indiehackers.com "available for freelance" developer',
    'site:indiehackers.com/interviews "invoice" OR "billing" freelancer',
]

# Domains to exclude from product/website link extraction
BAD_DOMAINS = {
    "onetrust.com", "cookielaw.org", "facebook.com", "twitter.com",
    "x.com", "linkedin.com", "indiehackers.com", "mapbox.com",
    "sentry.io", "cloudflare.com", "amplitude.com", "segment.com",
    "hotjar.com", "intercom.io", "crisp.chat", "google.com",
    "youtube.com", "instagram.com", "discord.gg",
}

# Platform-level Twitter accounts — not individual users
PLATFORM_TWITTER_HANDLES = {
    "indiehackers", "peerlist", "hashnode", "devto", "github",
    "twitter", "x", "google", "facebook", "instagram", "youtube",
}

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _serper_find_ih_profiles(max_urls=30):
    """
    Use Serper to find real IndieHackers profile/interview pages.
    Returns list of unique IH URLs.
    """
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        print("  ⚠️  SERPER_API_KEY not set — IndieHackers scraper will be empty")
        return []

    seen = set()
    urls = []

    # Budget: Max 4 queries (4 Serper credits) for IndieHackers profile discovery
    for query in IH_SERPER_QUERIES[:4]:
        if len(urls) >= max_urls:
            break
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 10},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            for result in resp.json().get("organic", []):
                url = result.get("link", "")
                if "indiehackers.com" in url and url not in seen:
                    seen.add(url)
                    urls.append(url)
        except Exception as e:
            print(f"  Serper error for IH query: {e}")

    print(f"  Serper found {len(urls)} IndieHackers profile URLs")
    return urls


def _is_bad_domain(url):
    """Return True if the URL belongs to a domain we should skip."""
    try:
        domain = url.split("/")[2].lower().replace("www.", "")
        return any(bad in domain for bad in BAD_DOMAINS)
    except Exception:
        return True


async def _visit_and_waterfall(page, profile_url):
    """
    Visit a single IH profile/interview page, extract links, run waterfall.
    Returns a contact dict or None.
    """
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # Dismiss cookie banner
        try:
            btn = page.locator("button#onetrust-accept-btn-handler")
            if await btn.count() > 0:
                await btn.click()
                await page.wait_for_timeout(500)
        except Exception:
            pass

        title = await page.title()
        name = re.split(r"[|\-–—]", title)[0].strip() or "Freelancer"
        if "indie hackers" in name.lower():
            name = "Freelancer"

        bio = ""
        try:
            bio = await page.locator("meta[name='description']").get_attribute("content") or ""
        except Exception:
            pass

        html = await page.content()

        # Find all clean external URLs from the page
        all_ext = re.findall(r'href="(https?://[^"]{10,})"', html)
        clean_urls = [u for u in all_ext if not _is_bad_domain(u)]

        product_url = clean_urls[0] if clean_urls else ""
        website_url = clean_urls[1] if len(clean_urls) > 1 else ""

        # GitHub and Twitter
        github_user = ""
        gh = re.search(r'github\.com/([a-zA-Z0-9_\-]+)', html)
        if gh and gh.group(1) not in ("sponsors", "login", "features", "about"):
            github_user = gh.group(1)

        twitter_handle = ""
        tw = re.search(r'(?:twitter|x)\.com/([a-zA-Z0-9_]+)', html)
        if tw:
            h = tw.group(1)
            if (h.lower() not in ("share", "intent", "home", "login", "i")
                    and h.lower() not in PLATFORM_TWITTER_HANDLES):
                twitter_handle = h

        print(f"    product={product_url[:50] if product_url else 'none'}")
        print(f"    website={website_url[:50] if website_url else 'none'}")
        print(f"    github={github_user} | twitter={twitter_handle}")

        # ── Email Waterfall ──────────────────────────────────────────
        email = ""
        method = "none"

        if product_url:
            print(f"      → Scanning product: {product_url[:55]}")
            email = scan_website_for_email(product_url)
            if email:
                method = "product_website"
                print(f"      ✅ product → {email}")

        if not email and website_url and website_url != product_url:
            print(f"      → Scanning website: {website_url[:55]}")
            email = scan_website_for_email(website_url)
            if email:
                method = "website"
                print(f"      ✅ website → {email}")

        if not email and github_user:
            print(f"      → GitHub commits: @{github_user}")
            email = email_from_github_commits(github_user)
            if email:
                method = "github_commits"
                print(f"      ✅ github_commits → {email}")

        if not email and twitter_handle:
            print(f"      → Twitter search: @{twitter_handle}")
            email = email_from_twitter_serper(twitter_handle)
            if email:
                method = "twitter_serper"
                print(f"      ✅ twitter_serper → {email}")

        if not email:
            print(f"      ✗ no email found")

        return {
            "name": name,
            "role": "Founder/Maker",
            "bio": bio[:150],
            "email": email,
            "url": product_url or website_url or profile_url,
            "source": "IndieHackers",
        }

    except Exception as e:
        print(f"  Error on {profile_url}: {e}")
        return None


async def scrape_indiehackers_async(limit=15):
    contacts = []

    # Step 1: Find real IH profile URLs via Serper
    print("  Finding IndieHackers profiles via Serper...")
    profile_urls = _serper_find_ih_profiles(max_urls=limit * 2)

    if not profile_urls:
        print("  No IH profiles found — skipping.")
        return []

    # Step 2: Visit each with Playwright (needed for JS-rendered pages)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA)
        page = await context.new_page()

        seen_urls = set()
        for url in profile_urls:
            if len(contacts) >= limit:
                break
            if url in seen_urls:
                continue
            seen_urls.add(url)

            print(f"\n  Visiting: {url}")
            contact = await _visit_and_waterfall(page, url)
            if contact:
                contacts.append(contact)

        await browser.close()

    print(f"\n  IndieHackers found {len(contacts)} contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    pct = int(emails_found / max(len(contacts), 1) * 100)
    print(f"  → {emails_found} have emails ({pct}% hit rate)")
    return contacts


def scrape_indiehackers(limit=15):
    return asyncio.run(scrape_indiehackers_async(limit))


if __name__ == "__main__":
    results = scrape_indiehackers(limit=5)
    for r in results:
        print(f"  {r['name']} | {r.get('email','NO EMAIL')} | {r['url'][:60]}")
