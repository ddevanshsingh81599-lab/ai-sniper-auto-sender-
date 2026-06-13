"""
Contra.com Scraper
==================
Contra is a platform for independent freelancers with public profiles.
The site is a full client-side React app — Playwright required.

Strategy:
  Phase 1: Load contra.com/discover, scroll to collect profile slugs.
  Phase 2: Visit each profile page with Playwright.
            → Check location — skip if not USA or Europe (no India etc.)
            → Extract email directly from rendered HTML (often present!)
            → If no email: extract their website link → scan for email
            → If no website: try GitHub commits via API

Key findings from live probe:
  - Emails ARE in the rendered HTML on many profiles
    (e.g. hello@eider.design found directly on profile page)
  - Location is embedded: "London, UK" / "Surat, India"
  - Use domcontentloaded — networkidle times out on some profiles
"""
import asyncio
import re
import time
from playwright.async_api import async_playwright
from scrapers.email_hunter import scan_website_for_email, email_from_github_commits

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Location keywords to KEEP (USA + Europe)
USA_KEYWORDS = {
    "usa", "united states", "u.s.", "u.s.a", "new york", "nyc", "san francisco",
    "los angeles", "la", "chicago", "austin", "seattle", "boston", "denver",
    "miami", "atlanta", "dallas", "portland", "phoenix", "houston", "remote",
    "california", "texas", "florida", "washington", "colorado", "massachusetts",
    "new jersey", "illinois", "georgia", "north carolina", "virginia", "ohio",
}
EUROPE_KEYWORDS = {
    "uk", "united kingdom", "england", "scotland", "wales",
    "london", "berlin", "amsterdam", "paris", "barcelona", "madrid", "rome",
    "milan", "stockholm", "oslo", "copenhagen", "helsinki", "dublin", "prague",
    "warsaw", "vienna", "zurich", "geneva", "brussels", "lisbon", "porto",
    "athens", "budapest", "tallinn", "riga", "vilnius", "rotterdam", "munich",
    "hamburg", "frankfurt", "cologne", "manchester", "birmingham", "edinburgh",
    "germany", "france", "spain", "italy", "netherlands", "belgium", "sweden",
    "norway", "denmark", "finland", "ireland", "portugal", "austria",
    "switzerland", "poland", "czech", "slovakia", "hungary", "romania",
    "bulgaria", "croatia", "serbia", "ukraine", "europe", "eu",
}
ALL_TARGETS = USA_KEYWORDS | EUROPE_KEYWORDS

# System / non-profile slugs to skip
SKIP_SLUGS = {
    "search", "discover", "login", "signup", "about", "pricing", "blog",
    "jobs", "projects", "p", "api", "static", "assets", "video", "image",
    "help", "terms", "privacy", "enterprise", "for-companies", "for-independents",
    "explore", "community", "home", "new", "feed",
}

# Bad email parts — platform emails that leak through website builders
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "contra.com", "sentry.io", "cloudflare",
    "@2x.", ".png@", ".jpg@", ".svg@", "apple-touch", "webpack",
    "builds.contra", "example.com",
    # Website builder support / system emails that bleed through
    "readymag.com", "squarespace.com", "wix.com", "webflow.io",
    "framer.com", "cargo.site", "format.com", "zenfolio.com",
    "smugmug.com", "pixieset.com", "strikingly.com", "weebly.com",
]


def _is_valid_email(email: str) -> bool:
    e = email.lower()
    if any(bad in e for bad in BAD_EMAIL_PARTS):
        return False
    local = e.split("@")[0]
    if len(local) < 3:
        return False
    domain = e.split("@")[-1]
    if "." not in domain:
        return False
    return True


def _is_target_location(location_str: str) -> bool:
    """Return True if the location is USA or Europe."""
    loc = location_str.lower()
    return any(kw in loc for kw in ALL_TARGETS)


def _extract_email_from_html(html: str) -> str:
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


def _extract_website(html: str) -> str:
    """Extract the freelancer's personal website from their Contra profile."""
    skip_domains = {
        "contra.com", "builds.contra.com", "help.contra.com",
        "twitter.com", "x.com", "linkedin.com", "instagram.com",
        "facebook.com", "github.com", "behance.net", "dribbble.com",
        "youtube.com", "vimeo.com", "sentry.io", "cloudflare.com",
        "apple.com", "google.com",
    }
    for url in re.findall(r'href="(https?://[^"]{10,})"', html):
        try:
            domain = url.split("/")[2].lower().replace("www.", "")
        except IndexError:
            continue
        if any(skip in domain for skip in skip_domains):
            continue
        # Must look like a real personal site (not a CDN chunk)
        if "assets/chunks" in url or "assets/entries" in url:
            continue
        return url.rstrip("/")
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


async def _collect_slugs(page, limit: int) -> list:
    """
    Load contra.com/discover and scroll to collect profile slugs.
    Returns a list of unique username slugs.
    """
    seen = set()
    slugs = []

    print("  Loading contra.com/discover ...")
    try:
        await page.goto(
            "https://contra.com/discover",
            wait_until="domcontentloaded",
            timeout=25000,
        )
        await page.wait_for_timeout(4000)

        # Scroll multiple times to load more profiles
        for i in range(10):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            if i % 3 == 2:
                html = await page.content()
                for match in re.finditer(r'href="/([a-zA-Z0-9_\-]{3,35})"', html):
                    slug = match.group(1)
                    if slug in SKIP_SLUGS or slug in seen:
                        continue
                    # Must look like a username (not a path segment)
                    if re.match(r'^[a-zA-Z][a-zA-Z0-9_\-]{2,34}$', slug):
                        seen.add(slug)
                        slugs.append(slug)
            if len(slugs) >= limit * 4:
                break

    except Exception as e:
        print(f"  Error loading discover page: {e}")

    # Deduplicate while preserving order
    slugs = list(dict.fromkeys(slugs))
    print(f"  Collected {len(slugs)} unique profile slugs")
    return slugs


async def scrape_contra_async(limit: int = 20) -> list:
    contacts = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=UA)
        page = await context.new_page()

        # ── Phase 1: Collect profile slugs ──────────────────────────
        slugs = await _collect_slugs(page, limit)

        # ── Phase 2: Visit each profile → extract data → waterfall ──
        processed = 0
        print(f"\n  Processing profiles (target: {limit} USA/Europe contacts)...\n")

        for slug in slugs:
            if processed >= limit:
                break

            profile_url = f"https://contra.com/{slug}"
            try:
                await page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=18000,
                )
                await page.wait_for_timeout(2000)
                html = await page.content()

                # ── Location filter ──────────────────────────────────
                # Use ONLY JSON pattern — the <span> approach matched
                # nav buttons like 'Sign Up' before the real location.
                location = ""
                loc_match = re.search(
                    r'"location"\s*:\s*"([^"]{2,60})"', html
                )
                if loc_match:
                    location = loc_match.group(1).strip()

                # Only skip if we have a CONFIRMED non-USA/Europe location.
                # Empty location = process anyway (better than missing leads).
                if location and not _is_target_location(location):
                    print(f"  @{slug:<25} ⏭  Skipping ({location})")
                    continue

                # Get name from page title — strip Contra's "Work by " prefix
                title = await page.title()
                name = re.split(r"[|\-–—]", title)[0].strip()
                name = re.sub(r'^Work by\s+', '', name, flags=re.IGNORECASE).strip()
                if not name or "contra" in name.lower() or len(name) > 60:
                    name = slug.replace("_", " ").title()

                print(f"  @{slug:<25} 📍 {location or 'Unknown'}")

                # ── Email extraction ─────────────────────────────────
                email = ""
                method = "none"

                # Step 1: Email directly in profile HTML (often present!)
                email = _extract_email_from_html(html)
                if email:
                    method = "profile_html"
                    print(f"    ✅ profile HTML → {email}")

                # Step 2: Their personal website → scan pages
                if not email:
                    website = _extract_website(html)
                    if website:
                        print(f"    → Scanning website: {website[:55]}")
                        email = scan_website_for_email(website)
                        if email:
                            method = "website"
                            print(f"    ✅ website → {email}")

                # Step 3: GitHub commits
                if not email:
                    github_user = _extract_github(html)
                    if github_user:
                        print(f"    → GitHub commits: @{github_user}")
                        email = email_from_github_commits(github_user)
                        if email:
                            method = "github_commits"
                            print(f"    ✅ github_commits → {email}")

                if not email:
                    print(f"    ✗ no email found")

                contacts.append({
                    "name": name,
                    "role": "Freelancer",
                    "bio": "",
                    "email": email,
                    "url": profile_url,
                    "location": location,
                    "source": "Contra",
                })
                processed += 1

            except Exception as e:
                print(f"  @{slug}: error — {e}")
                continue

        await browser.close()

    print(f"\n  Contra finished. {len(contacts)} USA/Europe contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    pct = int(emails_found / max(len(contacts), 1) * 100)
    print(f"  → {emails_found} have emails ({pct}% hit rate)")
    return contacts


def scrape_contra(limit: int = 20) -> list:
    return asyncio.run(scrape_contra_async(limit))
