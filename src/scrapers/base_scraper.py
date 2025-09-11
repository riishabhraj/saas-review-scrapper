# src/scrapers/base_scraper.py
from playwright.sync_api import sync_playwright
from datetime import date
from src.utils import parse_date_fuzzy
from typing import List, Dict, Optional
import time
import logging

log = logging.getLogger("scraper")
logging.basicConfig(level=logging.INFO)

class BaseScraper:
    def __init__(self, company: str, start_date: date, end_date: date, product_url: Optional[str]=None, headless: bool=True):
        self.company = company
        self.start_date = start_date
        self.end_date = end_date
        self.product_url = product_url
        self.headless = headless

    def scrape(self) -> List[Dict]:
        '''
        Main entrypoint: launches browser, navigates, collects reviews.
        Child classes must implement:
          - find_product_page()
          - extract_reviews_from_page(page) -> list[dict]
        '''
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()
            try:
                product_page = self.find_product_page(page)
                if not product_page:
                    raise RuntimeError("Product page not found")
                page.goto(product_page, wait_until="load")
                all_reviews = []
                page_num = 0

                while True:
                    page_num += 1
                    log.info(f"[page {page_num}] extracting reviews...")
                    reviews = self.extract_reviews_from_page(page)
                    if not reviews:
                        log.info("No reviews extracted on this page. Stopping.")
                        break

                    # parse & filter by date
                    kept = []
                    for r in reviews:
                        d = None
                        if isinstance(r.get("date"), date):
                            d = r["date"]
                        elif isinstance(r.get("date"), str):
                            d = parse_date_fuzzy(r["date"])
                        if d is None:
                            # if we can't parse date, keep it (or skip based on your policy)
                            kept.append(r)
                        else:
                            if self.start_date <= d <= self.end_date:
                                r["date"] = d.isoformat()
                                kept.append(r)
                    all_reviews.extend(kept)

                    # stop paginating if page had no reviews in range or child says no more pages
                    if self.should_stop_paging(page, reviews):
                        break

                    next_found = self.go_to_next_page(page)
                    if not next_found:
                        break

                    # politeness
                    time.sleep(1.0)
                return all_reviews
            finally:
                context.close()
                browser.close()

    def find_product_page(self, page) -> Optional[str]:
        '''Implement in subclass. Use page to perform site search if necessary.'''
        raise NotImplementedError

    def extract_reviews_from_page(self, page) -> List[Dict]:
        '''Implement in subclass. Should return list of dicts with at least 'review' and 'date'.'''
        raise NotImplementedError

    def go_to_next_page(self, page) -> bool:
        '''
        Default behavior: try to click a "Next" button or a "Load more" button.
        Override if site uses custom pagination.
        '''
        # tries several common button labels/selectors
        selectors = [
            'button:has-text("Load more")',
            'button:has-text("Load More")',
            'a[rel="next"]',
            'button:has-text("Next")',
            'button:has-text("See more")'
        ]
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    el.click()
                    page.wait_for_load_state("networkidle", timeout=5000)
                    return True
            except Exception:
                continue
        return False

    def should_stop_paging(self, page, reviews_on_page) -> bool:
        '''
        Heuristic: if none of the parsed reviews on current page are within the date range,
        and the parsed dates are older than start_date, stop paging.
        Subclasses can override to access site-specific sorting (newest->oldest).
        '''
        from src.utils import parse_date_fuzzy
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
        # If all parsed dates are older than start_date, we can stop.
        if any_parsable and not any_in_range:
            older = all((parse_date_fuzzy(r.get("date")) and parse_date_fuzzy(r.get("date")) < self.start_date) for r in reviews_on_page if r.get("date"))
            if older:
                return True
        return False
