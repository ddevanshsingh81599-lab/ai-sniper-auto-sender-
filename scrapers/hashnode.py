"""
Hashnode Scraper — Playwright Feed + Blog Scanner (no Serper)
=============================================================
Problem history:
  v1: Scraped /explore & /community → got /@explore, /@community (nav links)
  v2: GraphQL API → now a paid offering (returns HTML, not JSON)
  v3: Plain requests to *.hashnode.dev blogs → Cloudflare 403 blocks all requests

Final working approach:
  Step 1: Use Playwright to load hashnode.com feed pages
          → collect links going to USERNAME.hashnode.dev/...
          → extract username from subdomain (zero false positives)

  Step 2: For each username, use Playwright to scan their blog pages
          (plain requests always 403 due to Cloudflare — browser only)
          → scan / and /about page for email (mailto: or bare email)
          → look for GitHub link on /about page

  Step 3: GitHub commit history via authenticated API (no browser needed)

No Serper. No system handles possible. Browser bypasses Cloudflare.
"""
import asyncio
import re
import time
from playwright.async_api import async_playwright
from scrapers.email_hunter import email_from_github_commits

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# System handles that could appear as subdomains — block them
SYSTEM_HANDLES = {
    "featured", "explore", "community", "changelog", "about",
    "privacy", "terms", "hashnode", "security", "guest", "admin",
    "support", "help", "home", "feed", "login", "signup", "new",
    "team", "blog", "press", "legal", "status", "api", "tos",
    "cdn", "assets", "media", "images", "static", "www",
}

# Fake / template / placeholder emails
BLOCKED_EMAILS = {
    "user@test.com", "test@test.com", "example@example.com",
    "admin@example.com", "noreply@hashnode.com", "no-reply@hashnode.com",
    "hello@example.com", "info@example.com", "test@example.com",
    "your@email.com", "you@example.com",
}

BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "sentry.io", "cloudflare", "hashnode.com",
    "@2x.", ".png@", ".jpg@", "webpack", "postcss",
]


def _is_valid_email(email: str) -> bool:
    if not email:
        return False
    e = email.lower()
    if e in BLOCKED_EMAILS:
        return False
    if any(bad in e for bad in BAD_EMAIL_PARTS):
        return False
    local = e.split("@")[0]
    if len(local) < 3:
        return False
    return True


def _extract_email(html: str) -> str:
    """Extract first valid email — prefers mailto: links."""
    # Priority 1: mailto links
    for m in re.findall(
        r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
        html, re.IGNORECASE
    ):
        if _is_valid_email(m):
            return m
    # Priority 2: bare email pattern
    for m in re.findall(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        html
    ):
        if _is_valid_email(m):
            return m
    return ""


def _extract_github(html: str) -> str:
    """Extract GitHub username from HTML."""
    gh = re.search(r'github\.com/([a-zA-Z0-9_\-]+)', html)
    if gh:
        u = gh.group(1)
        if u not in ("sponsors", "login", "features", "about", "pricing",
                     "marketplace", "topics", "explore", "orgs"):
            return u
    return ""


async def _collect_usernames(page, limit: int) -> list:
    """
    Load Hashnode feed pages via Playwright and extract usernames
    from *.hashnode.dev article links — guaranteed to be real users.
    """
    seen = set()
    usernames = []

    feed_urls = [
        "https://hashnode.com/",
        "https://hashnode.com/featured",
        "https://hashnode.com/recent",
    ]

    for feed_url in feed_urls:
        if len(usernames) >= limit * 3:
            break
        try:
            print(f"  Loading feed: {feed_url} ...")
            await page.goto(feed_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(3000)

            # Scroll to load more articles in the feed
            for _ in range(8):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1200)

            html = await page.content()

            # ONLY match *.hashnode.dev links — these are always real user blogs
            for match in re.finditer(
                r'https://([a-zA-Z0-9][a-zA-Z0-9\-]{1,30})\.hashnode\.dev',
                html
            ):
                username = match.group(1).lower()
                if username in SYSTEM_HANDLES:
                    continue
                if username in seen:
                    continue
                seen.add(username)
                usernames.append(username)

        except Exception as e:
            print(f"  Error loading feed {feed_url}: {e}")

    print(f"  → Collected {len(usernames)} unique real usernames")
    return usernames


async def _scan_blog_for_email(page, username: str) -> tuple:
    """
    Use Playwright to scan a Hashnode blog for email.
    Tries homepage then /about page (bypasses Cloudflare).
    Returns (email, github_username).
    """
    blog_url = f"https://{username}.hashnode.dev"
    email = ""
    github_user = ""

    pages_to_try = [blog_url, blog_url + "/about", blog_url + "/contact"]

    for url in pages_to_try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(1500)
            html = await page.content()

            # Try to find email
            if not email:
                found = _extract_email(html)
                if found:
                    email = found

            # Try to find GitHub (especially on /about page)
            if not github_user:
                github_user = _extract_github(html)

            # If we have both, stop early
            if email and github_user:
                break

        except Exception:
            continue

    return email, github_user


async def scrape_hashnode_async(limit: int = 15) -> list:
    contacts = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA)
        page = await context.new_page()

        # ── Phase 1: Collect real usernames from the article feed ────
        usernames = await _collect_usernames(page, limit)

        # ── Phase 2: Visit each blog, run email waterfall ────────────
        print(f"\n  Processing {min(len(usernames), limit)} users...\n")

        for username in usernames[:limit]:
            blog_url = f"https://{username}.hashnode.dev"
            print(f"  @{username}")

            # Step 1: Playwright blog scan (bypasses Cloudflare)
            email, github_user = await _scan_blog_for_email(page, username)

            if email:
                print(f"    ✅ blog → {email}")
            else:
                print(f"    ✗ no email in blog pages")

            # Step 2: GitHub commit history (if GitHub found on /about)
            if not email and github_user:
                print(f"    → GitHub commits: @{github_user}")
                email = email_from_github_commits(github_user)
                if email and _is_valid_email(email):
                    print(f"    ✅ github_commits → {email}")
                else:
                    email = ""

            if not email:
                print(f"    ✗ no email found for @{username}")

            contacts.append({
                "name": username,
                "username": username,
                "role": "Developer Writer",
                "bio": "",
                "email": email,
                "url": blog_url,
                "source": "Hashnode",
            })

        await browser.close()

    print(f"\n  Hashnode finished. {len(contacts)} contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    pct = int(emails_found / max(len(contacts), 1) * 100)
    print(f"  → {emails_found} have emails ({pct}% hit rate)")
    return contacts


def scrape_hashnode(limit: int = 15) -> list:
    return asyncio.run(scrape_hashnode_async(limit))
