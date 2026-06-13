import asyncio
from playwright.async_api import async_playwright
import re


async def scrape_dribbble_async(limit=10):
    """
    Scrapes Dribbble for designers available for freelance work.
    Extracts data from the designers listing page and then
    visits individual about pages for email/bio.
    """
    contacts = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            print("  Navigating to Dribbble designers page...")
            await page.goto(
                "https://dribbble.com/designers?availability=true",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await page.wait_for_timeout(5000)

            # Scroll to load more
            for _ in range(2):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)

            # Extract the visible text to find designer names
            text = await page.evaluate("() => document.body.innerText")

            # Extract all links and find profile-style ones
            all_links = await page.locator("a").all()
            profiles = []
            skip_patterns = [
                "/designers", "/shots", "/tags", "/signup", "/session",
                "/about", "/contact", "/terms", "/privacy", "/pro",
                "/search", "/stories", "/jobs", "/hiring", "/marketplace",
                "/explore", "/resources", "/account", "/settings",
                "/notifications", "/buckets",
            ]

            for link in all_links:
                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue
                    # Profile links: /username (single segment, no query params)
                    if (
                        href.startswith("/")
                        and href.count("/") == 1
                        and len(href) > 2
                        and "?" not in href
                        and "#" not in href
                    ):
                        if not any(href.startswith(s) for s in skip_patterns):
                            if href not in profiles:
                                profiles.append(href)
                except:
                    continue

            print(f"  Found {len(profiles)} potential Dribbble profiles.")

            for profile_path in profiles[:limit]:
                try:
                    # Visit the about page
                    profile_url = f"https://dribbble.com{profile_path}/about"
                    await page.goto(
                        profile_url, wait_until="domcontentloaded", timeout=15000
                    )
                    await page.wait_for_timeout(3000)

                    title = await page.title()
                    if "Just a moment" in title:
                        print(f"  ⚠️ Cloudflare on {profile_path}, skipping.")
                        continue

                    name = title.split("-")[0].split("|")[0].strip() if title else "Designer"

                    page_html = await page.content()

                    # Extract email
                    email = ""
                    email_match = re.search(
                        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
                        page_html,
                    )
                    if email_match:
                        found = email_match.group(0)
                        if not any(
                            x in found.lower()
                            for x in ["dribbble", "example", ".png", ".svg", ".css", ".js"]
                        ):
                            email = found

                    # Bio from meta
                    bio = ""
                    try:
                        bio = (
                            await page.locator("meta[name='description']").get_attribute(
                                "content"
                            )
                            or ""
                        )
                    except:
                        pass

                    # External website
                    website = ""
                    ext_links = await page.locator("a[rel*='nofollow']").all()
                    for el in ext_links:
                        try:
                            href = await el.get_attribute("href")
                            if href and href.startswith("http") and "dribbble" not in href:
                                website = href
                                break
                        except:
                            continue

                    contact = {
                        "name": name,
                        "role": "Designer",
                        "bio": bio[:150],
                        "email": email,
                        "url": website or f"https://dribbble.com{profile_path}",
                        "source": "Dribbble",
                    }
                    contacts.append(contact)

                except Exception as e:
                    print(f"  Error on Dribbble profile {profile_path}: {e}")

        except Exception as e:
            print(f"  Error scraping Dribbble: {e}")
        finally:
            await browser.close()

    print(f"  Dribbble scraper found {len(contacts)} contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    print(f"  → {emails_found} contacts have emails.")
    return contacts


def scrape_dribbble(limit=10):
    return asyncio.run(scrape_dribbble_async(limit))


if __name__ == "__main__":
    results = scrape_dribbble(limit=3)
    for r in results:
        print(f"  {r['name']} | {r.get('email','NO EMAIL')} | {r['url']}")
