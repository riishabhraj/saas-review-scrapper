# src/scrapers/async_base_scraper.py
import asyncio
from datetime import date
from typing import List, Dict, Optional
from playwright.async_api import async_playwright, Page
from src.utils import parse_date_fuzzy
import logging

log = logging.getLogger("async_scraper")

class AsyncBaseScraper:
    def __init__(self, company: str, start_date: date, end_date: date, product_url: Optional[str]=None, headless: bool=True):
        self.company = company
        self.start_date = start_date
        self.end_date = end_date
        self.product_url = product_url
        self.headless = headless

    async def scrape(self) -> List[Dict]:
        try:
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(headless=self.headless)
                except NotImplementedError as ne:  # Python 3.13 Windows issue
                    raise RuntimeError(
                        "Playwright subprocess creation not supported in this Python runtime. "
                        "Workarounds: (1) Use Python 3.11 or 3.12; (2) Run under WSL2 Linux; (3) Try 'pip install playwright==1.44' then reinstall browsers; (4) Use Docker linux container."
                    ) from ne
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    product_page = await self.find_product_page(page)
                    if not product_page:
                        raise RuntimeError("Product page not found")
                    await page.goto(product_page, wait_until="load")
                    all_reviews: List[Dict] = []
                    page_num = 0
                    while True:
                        page_num += 1
                        log.info(f"[page {page_num}] extracting reviews...")
                        reviews = await self.extract_reviews_from_page(page)
                        if not reviews:
                            log.info("No reviews extracted on this page. Stopping.")
                            break
                        kept = []
                        for r in reviews:
                            d = None
                            if isinstance(r.get("date"), date):
                                d = r["date"]
                            elif isinstance(r.get("date"), str):
                                d = parse_date_fuzzy(r["date"])
                            if d is None:
                                kept.append(r)
                            else:
                                if self.start_date <= d <= self.end_date:
                                    r["date"] = d.isoformat()
                                    kept.append(r)
                        all_reviews.extend(kept)
                        if await self.should_stop_paging(page, reviews):
                            break
                        next_found = await self.go_to_next_page(page)
                        if not next_found:
                            break
                        await asyncio.sleep(1.0)
                    return all_reviews
                finally:
                    await context.close()
                    await browser.close()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Unexpected scraper failure: {e}") from e

    async def find_product_page(self, page: Page) -> Optional[str]:
        raise NotImplementedError

    async def extract_reviews_from_page(self, page: Page) -> List[Dict]:
        raise NotImplementedError

    async def go_to_next_page(self, page: Page) -> bool:
        selectors = [
            'button:has-text("Load more")',
            'button:has-text("Load More")',
            'a[rel="next"]',
            'button:has-text("Next")',
            'button:has-text("See more")'
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    await page.wait_for_load_state("networkidle")
                    return True
            except Exception:
                continue
        return False

    async def should_stop_paging(self, page: Page, reviews_on_page: List[Dict]) -> bool:
        any_in_range = False
        any_parsable = False
        for r in reviews_on_page:
            d = None
            if isinstance(r.get("date"), str):
                d = parse_date_fuzzy(r["date"])
            elif r.get("date"):
                d = r.get("date")
            if d:
                any_parsable = True
                if self.start_date <= d <= self.end_date:
                    any_in_range = True
        if any_parsable and not any_in_range:
            older = True
            for r in reviews_on_page:
                if r.get("date"):
                    pd = parse_date_fuzzy(r.get("date"))
                    if pd and pd >= self.start_date:
                        older = False
                        break
            if older:
                return True
        return False
