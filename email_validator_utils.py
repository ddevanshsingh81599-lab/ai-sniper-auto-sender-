"""
email_validator_utils.py
════════════════════════
Shared email validation used by:
  - clean_emails.py   (one-time sheet cleanup)
  - main.py           (gate before Sheets push)
  - email_sender.py   (safety net before send)

Multi-layer validation pipeline:
  1. Syntax fix  — strips "Email" prefix from scraped garbage
  2. Blocklist   — gov, social, support, fake domains
  3. Regex       — basic format check
  4. DNS/MX      — verifies domain can actually receive mail

Usage:
    from email_validator_utils import validate_email_address
    is_valid, cleaned, reason = validate_email_address("Emailfoo@bar.com")
    # is_valid=True, cleaned="foo@bar.com", reason=""
    # is_valid=False, cleaned="", reason="no MX records"
"""

import re

# Try importing email_validator for DNS/MX checks
# Falls back gracefully if not installed (syntax-only validation)
try:
    from email_validator import validate_email, EmailNotValidError
    HAS_DNS_CHECK = True
except ImportError:
    HAS_DNS_CHECK = False
    print("  ⚠️  email-validator not installed — DNS/MX checks disabled.")
    print("      Run: pip install email-validator")


# ── Blocklists ───────────────────────────────────────────────────────────────

# Domains that are NEVER valid personal email destinations
BLOCKED_DOMAINS = {
    # Fake / placeholder
    "company.com", "example.com", "example.org", "example.net",
    "test.com", "domain.com", "email.com", "mail.com",
    "yourcompany.com", "yourdomain.com", "sample.com",
    # Social media (not email providers)
    "instagram.com", "facebook.com", "twitter.com", "x.com",
    "tiktok.com", "linkedin.com", "pinterest.com", "snapchat.com",
    "reddit.com", "tumblr.com", "youtube.com",
    # Platform / system
    "readymag.com", "squarespace.com", "wix.com", "wixpress.com",
    "cloudflare.com", "amazonaws.com", "sentry.io",
    "jitter.video",
    # Known non-personal
    "pahouse.net",
}

# Government TLDs — not freelancers
GOV_TLDS = {".gov", ".gov.uk", ".gov.au", ".gov.in", ".gov.ca", ".mil"}

# Prefixes that are NEVER personal (hard block)
HARD_BLOCKED_PREFIXES = (
    "info@", "sales@", "support@", "team@", "admin@", "help@",
    "office@", "marketing@", "press@", "careers@", "jobs@",
    "enquiries@", "noreply@", "no-reply@", "webmaster@",
    "postmaster@", "mailer-daemon@", "abuse@",
    "billing@", "security@", "compliance@",
)

# Soft block — only rejected if domain looks like a company
SOFT_BLOCKED_PREFIXES = ("hello@", "hi@", "contact@",)
AGENCY_DOMAIN_KEYWORDS = (
    "studio", "agency", "design", "creative", "labs", "media",
    "solutions", "group", "digital", "works", "brand", "collective",
)

# Email regex for basic format validation
EMAIL_REGEX = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


# ── Core Validation Function ─────────────────────────────────────────────────

def validate_email_address(
    raw_email: str,
    check_dns: bool = True,
    check_business: bool = True,
) -> tuple:
    """
    Validate an email address through multiple layers.

    Args:
        raw_email:      The raw email string (may have "Email" prefix junk)
        check_dns:      If True, verify domain has MX records (requires network)
        check_business: If True, block business/agency email prefixes

    Returns:
        (is_valid: bool, cleaned_email: str, rejection_reason: str)

    Examples:
        validate_email_address("Emailfoo@bar.com")
        → (True/False, "foo@bar.com", "")

        validate_email_address("support@jitter.video")
        → (False, "", "blocked prefix: support@")

        validate_email_address("freelancewitherica@instagram.com")
        → (False, "", "blocked domain: instagram.com")
    """
    if not raw_email or not raw_email.strip():
        return False, "", "empty email"

    email = raw_email.strip()

    # ── Layer 1: Fix "Email" prefix from scraper bugs ────────────────────
    # Handles: "Emailinfo@creativecode.com.tr" → "info@creativecode.com.tr"
    #          "Emailcontact@johanlorck.fr" → "contact@johanlorck.fr"
    #          "Emailsean.briar1011@gmail.com" → "sean.briar1011@gmail.com"
    if email.startswith("Email") and "@" in email:
        fixed = email[5:]  # strip "Email" prefix (5 chars)
        if EMAIL_REGEX.match(fixed):
            email = fixed

    # Also handle other common scraping artifacts
    # "mailto:" prefix, whitespace, angle brackets
    email = email.replace("mailto:", "").strip().strip("<>").strip()

    # ── Layer 2: Basic format check ──────────────────────────────────────
    if not EMAIL_REGEX.match(email):
        return False, "", f"invalid format: {email}"

    email_lower = email.lower()
    local_part = email_lower.split("@")[0]
    domain = email_lower.split("@")[-1]

    # ── Layer 3: Blocked domains ─────────────────────────────────────────
    if domain in BLOCKED_DOMAINS:
        return False, "", f"blocked domain: {domain}"

    # Government TLDs
    for tld in GOV_TLDS:
        if domain.endswith(tld):
            return False, "", f"government domain: {domain}"

    # ── Layer 4: Blocked prefixes ────────────────────────────────────────
    if check_business:
        if email_lower.startswith(HARD_BLOCKED_PREFIXES):
            matched = next(p for p in HARD_BLOCKED_PREFIXES if email_lower.startswith(p))
            return False, "", f"blocked prefix: {matched}"

        if email_lower.startswith(SOFT_BLOCKED_PREFIXES):
            domain_name = domain.split(".")[0]
            if any(kw in domain_name for kw in AGENCY_DOMAIN_KEYWORDS):
                return False, "", f"agency email: {email_lower}"

    # ── Layer 5: Suspicious patterns ─────────────────────────────────────
    # Local part too short (likely fake)
    if len(local_part) < 2:
        return False, "", f"local part too short: {local_part}"

    # Random junk emails (like aryi0vkgbd-0@outlook.com)
    # Allow through — some people use random usernames

    # ── Layer 6: DNS/MX verification ─────────────────────────────────────
    if check_dns and HAS_DNS_CHECK:
        try:
            result = validate_email(
                email,
                check_deliverability=True,
                dns_resolver=None,  # use system resolver
            )
            # Use the normalized form from the library
            email = result.normalized
        except EmailNotValidError as e:
            return False, "", f"DNS/MX failed: {str(e)}"

    return True, email, ""


def validate_email_quick(raw_email: str) -> tuple:
    """
    Quick validation WITHOUT DNS check.
    Use in hot loops where speed matters (e.g., scraper output filtering).

    Returns:
        (is_valid: bool, cleaned_email: str, rejection_reason: str)
    """
    return validate_email_address(raw_email, check_dns=False, check_business=True)


def fix_email_prefix(raw_email: str) -> str:
    """
    Fix common scraping artifacts in email strings.
    Returns cleaned email or original if no fix needed.

    Handles:
        "Emailfoo@bar.com" → "foo@bar.com"
        "mailto:foo@bar.com" → "foo@bar.com"
    """
    if not raw_email:
        return raw_email

    email = raw_email.strip()

    if email.startswith("Email") and "@" in email:
        fixed = email[5:]
        if EMAIL_REGEX.match(fixed):
            return fixed

    if email.startswith("mailto:"):
        return email[7:].strip()

    return email
