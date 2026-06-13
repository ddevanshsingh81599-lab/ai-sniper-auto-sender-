"""
Peerlist Scraper — with Email Waterfall
========================================
Phase 1: Search Peerlist for USA/Europe freelancers, collect profile URLs.
Phase 2: Visit each profile page, extract website/GitHub/Twitter links.
Phase 3: Run email waterfall on those links.

Expected email hit rate: ~65%
"""
import asyncio
from playwright.async_api import async_playwright
from scrapers.email_hunter import extract_links_from_html, find_email_waterfall


async def scrape_peerlist_async(limit=20):
    contacts = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # ── Phase 1: Collect profile URLs ───────────────────────────
        search_queries = [
            "freelance designer USA",
            "freelance developer USA",
            "freelance designer Europe",
            "freelance developer Europe",
            "freelance designer London",
            "freelance developer Berlin",
            "freelance designer New York",
            "freelance developer San Francisco",
        ]
        seen = set()
        profile_cards = []

        for query in search_queries:
            if len(profile_cards) >= limit * 3:
                break
            try:
                url = f"https://peerlist.io/search?q={query.replace(' ', '+')}&type=people"
                print(f"  Peerlist search: {query}...")
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(4000)

                # Scroll to load more results
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1500)

                cards = await page.evaluate('''() => {
                    const results = [];
                    const links = document.querySelectorAll('a[href]');
                    for (const a of links) {
                        const href = a.getAttribute('href');
                        if (!href || !href.startsWith('/')) continue;
                        if (href.split('/').length !== 2 || href.length < 3) continue;
                        const parent = a.parentElement;
                        if (!parent) continue;
                        const cls = parent.className || '';
                        if (!cls.includes('cursor-pointer') && !cls.includes('group')) continue;
                        const text = a.innerText.trim();
                        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                        if (lines.length === 0) continue;
                        results.push({
                            username: href.substring(1),
                            name: lines[0],
                            bio: lines.slice(1).join(' '),
                            url: 'https://peerlist.io' + href,
                        });
                    }
                    return results;
                }''')

                for card in cards:
                    if card["username"] not in seen:
                        seen.add(card["username"])
                        profile_cards.append(card)

            except Exception as e:
                print(f"  Error on Peerlist search '{query}': {e}")

        print(f"\n  Collected {len(profile_cards)} unique profiles → processing {min(len(profile_cards), limit)}")

        # ── Phase 2 + 3: Visit profile → extract links → waterfall ──
        for profile in profile_cards[:limit]:
            try:
                print(f"\n  Profile: {profile['url']}")
                await page.goto(profile["url"], wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                html = await page.content()
                website, github_user, twitter_handle = extract_links_from_html(html, base_domain="peerlist.io")

                print(f"    Links → website={website[:40] if website else 'none'} | github={github_user} | twitter={twitter_handle}")

                email, method = find_email_waterfall(
                    website_url=website,
                    github_username=github_user,
                    twitter_handle=twitter_handle,
                    name=profile["name"],
                )

                contacts.append({
                    "name": profile["name"],
                    "role": "Freelancer",
                    "bio": profile.get("bio", "")[:150],
                    "email": email,
                    "url": website or profile["url"],
                    "source": "Peerlist",
                })

            except Exception as e:
                print(f"  Error visiting {profile['url']}: {e}")

        await browser.close()

    print(f"\n  Peerlist found {len(contacts)} contacts.")
    emails_found = sum(1 for c in contacts if c.get("email"))
    print(f"  → {emails_found} have emails ({int(emails_found/max(len(contacts),1)*100)}% hit rate)")
    return contacts


def scrape_peerlist(limit=20):
    return asyncio.run(scrape_peerlist_async(limit))


if __name__ == "__main__":
    results = scrape_peerlist(limit=5)
    for r in results:
        print(f"  {r['name']} | {r.get('email','NO EMAIL')} | {r['url']}")
