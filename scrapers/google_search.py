import os
import requests
import re
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

def get_google_search_results(query, limit=10):
    """
    Calls the Google Custom Search API.
    """
    api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
    cx = os.getenv("GOOGLE_SEARCH_ENGINE_ID")
    
    if not api_key or not cx:
        print("Missing Google Search API credentials.")
        return []
        
    url = "https://customsearch.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": limit
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("items", [])
    except Exception as e:
        print(f"Google Search API error for query '{query}': {e}")
        return []

async def fetch_page_details(url):
    """
    Visits a URL to extract email and bio using Playwright.
    """
    email = ""
    bio = ""
    title = ""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            # Set a timeout so we don't hang on bad sites
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            
            title = await page.title()
            content = await page.content()
            
            # Basic email regex
            email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content)
            if email_match:
                email = email_match.group(0)
                
            # Try to grab a meta description for bio
            try:
                bio = await page.locator("meta[name='description']").get_attribute("content")
            except:
                pass
                
        except Exception as e:
            print(f"Failed to fetch page details for {url}: {e}")
        finally:
            await browser.close()
            
    return title, email, bio

def scrape_google_search(limit_per_query=5):
    """
    Runs specific Google searches and extracts contacts.
    """
    print("Starting Google Search Scraper...")
    contacts = []
    
    queries = [
        # --- USA targets ---
        '"available for freelance" designer USA "contact me"',
        '"hire me" developer USA portfolio email 2024 2025',
        '"open to projects" freelancer USA portfolio',
        '"freelance designer" "New York" email contact',
        '"freelance developer" "San Francisco" email contact',
        '"freelance" "Austin" OR "Chicago" developer email portfolio',

        # --- European targets ---
        '"available for freelance" designer Europe "contact me"',
        '"hire me" developer Europe portfolio email 2024 2025',
        '"open to projects" freelancer Europe portfolio',
        '"freelance designer" "London" email contact',
        '"freelance developer" "Berlin" email contact',
        '"freelance" "Amsterdam" OR "Paris" developer designer email',
    ]
    
    for query in queries:
        print(f"Running query: {query}")
        results = get_google_search_results(query, limit=limit_per_query)
        
        for item in results:
            url = item.get("link")
            snippet = item.get("snippet", "")
            
            if not url:
                continue
                
            print(f"Visiting {url}...")
            # Run the async fetcher
            page_title, page_email, page_bio = asyncio.run(fetch_page_details(url))
            
            # Clean up title for name
            name = page_title.split('|')[0].strip() if page_title else "Freelancer"
            
            # Try to find email in snippet if not on page
            if not page_email:
                email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', snippet)
                if email_match:
                    page_email = email_match.group(0)
                    
            contact = {
                "name": name,
                "role": "Freelancer",
                "bio": (page_bio or snippet)[:150],
                "email": page_email,
                "url": url,
                "source": "Google Search"
            }
            contacts.append(contact)
            
    print(f"Google Search Scraper finished. Found {len(contacts)} contacts.")
    return contacts

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    results = scrape_google_search(limit_per_query=2)
    print(results)
