"""
Email Waterfall Hunter
======================
Shared utility used by Peerlist, IndieHackers, and Hashnode scrapers.

Multi-step fallback chain:
  Step 1 — Scan personal website pages (/, /contact, /about, /hire-me …)
            Prefers mailto: links (person put it there intentionally).
  Step 2 — GitHub commit history  (hit rate ~60-70%)
            Reads git author email from non-fork repo commits via API.
  Step 3 — Serper Twitter/X search
            Many devs put email in bio or pinned tweet.

Usage:
    from scrapers.email_hunter import extract_links_from_html, find_email_waterfall
    website, github_user, twitter = extract_links_from_html(html, "peerlist.io")
    email, method = find_email_waterfall(website, github_user, twitter, name="Alice")
"""
import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────
CONTACT_PAGES = [
    "",
    "/contact",
    "/about",
    "/about-me",
    "/hire-me",
    "/work-with-me",
    "/get-in-touch",
    "/reach-me",
    "/say-hello",
    "/contact-me",
]

# Platform/social domains to skip when extracting "personal website" links
SKIP_DOMAINS = {
    "peerlist.io", "indiehackers.com", "hashnode.com", "hashnode.dev",
    "twitter.com", "x.com", "linkedin.com", "github.com", "github.io",
    "facebook.com", "instagram.com", "youtube.com", "discord.gg",
    "google.com", "notion.so", "medium.com", "dev.to", "substack.com",
    "producthunt.com", "buymeacoffee.com", "ko-fi.com",
    # Cookie consent / legal banners — these appear on every page
    "onetrust.com", "cookielaw.org", "cookiebot.com", "trustarc.com",
    "iubenda.com", "cookiepro.com", "gdpr.eu",
    # Analytics / CDN noise
    "sentry.io", "cloudflare.com", "amplitude.com", "segment.com",
    "hotjar.com", "intercom.io", "crisp.chat",
}

# Junk patterns that look like emails but aren't
BAD_EMAIL_PARTS = [
    "noreply", "no-reply", "example.com", "sentry.io",
    "wixpress", "squarespace", "amazonaws", "cloudflare",
    "github.com", "hashnode", "peerlist", "indiehackers",
    "@2x.", ".png@", ".jpg@", ".svg@", "webpack", "babel",
    "postcss", "tailwind", "eslint",
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ── Helpers ──────────────────────────────────────────────────────────

def _is_valid_email(email: str) -> bool:
    """Return True if the email looks like a real personal address."""
    e = email.lower()
    if any(bad in e for bad in BAD_EMAIL_PARTS):
        return False
    local = e.split("@")[0]
    if len(local) < 3:
        return False
    # Must have a proper TLD
    domain_part = e.split("@")[-1]
    if "." not in domain_part:
        return False
    return True


def _extract_email_from_html(html: str) -> str:
    """
    Pull the first valid personal email from raw HTML.
    Priority 1 → mailto: links (user intentionally put it there).
    Priority 2 → bare email pattern anywhere in the page.
    """
    # Priority 1: mailto links
    for email in re.findall(
        r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})',
        html, re.IGNORECASE
    ):
        if _is_valid_email(email):
            return email

    # Priority 2: bare email anywhere
    for email in re.findall(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        html
    ):
        if _is_valid_email(email):
            return email

    return ""


# ── Public API ───────────────────────────────────────────────────────

def extract_links_from_html(html: str, base_domain: str = "") -> tuple:
    """
    Parse a profile page's HTML and return:
        (website_url, github_username, twitter_handle)

    Skips known social/platform links so 'website' is their personal site.
    """
    website = ""
    github_user = ""
    twitter_handle = ""

    # Personal website — first external link that isn't a known platform
    for link in re.findall(r'href="(https?://[^"]+)"', html):
        link_clean = link.split('"')[0].split("'")[0].strip().rstrip("/")
        if not link_clean.startswith("http"):
            continue
        try:
            domain = link_clean.split("/")[2].lower().replace("www.", "")
        except IndexError:
            continue
        if base_domain and base_domain in domain:
            continue
        if any(skip in domain for skip in SKIP_DOMAINS):
            continue
        website = link_clean
        break

    # GitHub username
    gh = re.search(r'github\.com/([a-zA-Z0-9_\-]+)', html)
    if gh:
        u = gh.group(1)
        if u not in ("sponsors", "login", "signup", "about", "features", "pricing",
                     "marketplace", "topics", "explore", "orgs"):
            github_user = u

    # Twitter / X handle
    tw = re.search(r'(?:twitter|x)\.com/(?:intent/follow\?screen_name=)?([a-zA-Z0-9_]+)', html)
    if tw:
        h = tw.group(1)
        if h.lower() not in (
            "share", "intent", "home", "login", "i", "search", "hashtag",
            "indiehackers", "peerlist", "hashnode", "devto", "github",
            "twitter", "x", "google", "facebook", "instagram", "youtube"
        ):
            twitter_handle = h

    return website, github_user, twitter_handle


def scan_website_for_email(website_url: str, max_pages: int = 6) -> str:
    """
    Step 1 — Visit the personal website + common contact pages.
    Uses plain requests (fast, no browser needed for most static sites).
    Returns the first valid email found, or "".
    """
    if not website_url:
        return ""

    base = website_url.rstrip("/")
    tried = 0

    for suffix in CONTACT_PAGES:
        if tried >= max_pages:
            break
        url = base + suffix
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": UA},
                timeout=7,
                allow_redirects=True,
            )
            tried += 1
            if resp.status_code != 200:
                continue
            email = _extract_email_from_html(resp.text)
            if email:
                return email
            time.sleep(0.4)
        except Exception:
            continue

    return ""


def email_from_github_commits(github_username: str) -> str:
    """
    Step 2 — Scan a user's non-fork repos' commit history for git author email.
    Authenticated requests → 5000 req/hr limit.
    Hit rate: ~60-70%.
    """
    if not github_username:
        return ""

    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        repos_resp = requests.get(
            f"https://api.github.com/users/{github_username}/repos",
            headers=headers,
            params={"sort": "pushed", "per_page": 10},
            timeout=10,
        )
        if repos_resp.status_code != 200:
            return ""

        repos = repos_resp.json()
        if not isinstance(repos, list):
            return ""

        for repo in repos:
            if not isinstance(repo, dict) or repo.get("fork"):
                continue  # skip forks — email may not be this person's

            rname = repo.get("name")
            c_resp = requests.get(
                f"https://api.github.com/repos/{github_username}/{rname}/commits",
                headers=headers,
                params={"per_page": 15},
                timeout=10,
            )
            if c_resp.status_code != 200:
                continue

            commits = c_resp.json()
            if not isinstance(commits, list):
                continue

            for commit in commits:
                email = commit.get("commit", {}).get("author", {}).get("email", "")
                if email and _is_valid_email(email) and "noreply" not in email.lower():
                    return email

    except Exception:
        pass

    return ""


def email_from_twitter_serper(twitter_handle: str) -> str:
    """
    Step 3 — Search Twitter/X via Serper for the person's email.
    Costs 1 Serper credit.
    """
    if not twitter_handle:
        return ""

    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return ""

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={
                "q": f'"{twitter_handle}" email contact site:twitter.com OR site:x.com',
                "num": 3,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return ""

        for result in resp.json().get("organic", []):
            snippet = result.get("snippet", "")
            m = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', snippet)
            if m and _is_valid_email(m.group(0)):
                return m.group(0)

    except Exception:
        pass

    return ""


def find_email_waterfall(
    website_url: str = "",
    github_username: str = "",
    twitter_handle: str = "",
    name: str = "",
) -> tuple:
    """
    Master waterfall — tries each method in order, returns first hit.

    Returns:
        (email: str, method: str)
        method is one of: "website", "github_commits", "twitter_serper", "none"
    """
    label = name or website_url or github_username or "unknown"
    print(f"    🔍 Email hunt: {label}")

    # Step 1 — Personal website
    if website_url:
        print(f"      → Scanning website: {website_url[:60]}")
        email = scan_website_for_email(website_url)
        if email:
            print(f"      ✅ website → {email}")
            return email, "website"

    # Step 2 — GitHub commits
    if github_username:
        print(f"      → GitHub commits: @{github_username}")
        email = email_from_github_commits(github_username)
        if email:
            print(f"      ✅ github_commits → {email}")
            return email, "github_commits"

    # Step 3 — Twitter/X Serper search (DISABLED TO PROTECT SERPER BUDGET)
    # if not email and twitter_handle:
    #     print(f"      → Twitter search: @{twitter_handle}")
    #     email = email_from_twitter_serper(twitter_handle)
    #     if email:
    #         print(f"      ✅ twitter_serper → {email}")
    #         return email, "twitter_serper"

    print(f"      ✗ no email found")
    return "", "none"
