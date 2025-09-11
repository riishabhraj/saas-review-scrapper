# src/scrapers/async_base_scraper.py
import asyncio
import os, sys
from datetime import date
from typing import List, Dict, Optional
from playwright.async_api import async_playwright, Page
from src.utils import parse_date_fuzzy, ensure_outputs_dir
import logging
import pathlib

log = logging.getLogger("async_scraper")

class AsyncBaseScraper:
    def __init__(self, company: str, start_date: date, end_date: date, product_url: Optional[str]=None, headless: bool=True, debug: bool=False):
        self.company = company
        self.start_date = start_date
        self.end_date = end_date
        self.product_url = product_url
        self.headless = headless
        self.debug = debug

    async def scrape(self) -> List[Dict]:
        try:
            async with async_playwright() as p:
                try:
                    # Honor corporate proxies if set
                    launch_kwargs = {"headless": self.headless}
                    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
                        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") \
                        or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
                    if proxy:
                        launch_kwargs["proxy"] = {"server": proxy}

                    # On Windows, prefer system Edge channel to pick up OS networking
                    if sys.platform.startswith("win"):
                        try:
                            browser = await p.chromium.launch(channel="msedge", **launch_kwargs)
                        except Exception:
                            browser = await p.chromium.launch(**launch_kwargs)
                    else:
                        browser = await p.chromium.launch(**launch_kwargs)
                except NotImplementedError as ne:  # Python 3.13 Windows issue
                    raise RuntimeError(
                        "Playwright subprocess creation not supported in this Python runtime. "
                        "Workarounds: (1) Use Python 3.11 or 3.12; (2) Run under WSL2 Linux; (3) Try 'pip install playwright==1.44' then reinstall browsers; (4) Use Docker linux container."
                    ) from ne
                # Use a common desktop Chrome UA and sensible defaults
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    viewport={"width": 1400, "height": 900},
                )
                page = await context.new_page()
                try:
                    product_page = await self.find_product_page(page)
                    if not product_page:
                        raise RuntimeError("Product page not found")
                    await page.goto(product_page, wait_until="load")
                    # Try to accept cookie banners if present
                    try:
                        await self.accept_cookies(page)
                    except Exception:
                        pass
                    if self.debug:
                        await self.debug_dump(page, "loaded")
                    # Give the page a moment and wait for any review-related selector
                    try:
                        await page.wait_for_selector(
                            'script[type="application/ld+json"], article, div[class*="review"], [itemtype*="Review"]',
                            timeout=6000,
                        )
                    except Exception:
                        await asyncio.sleep(0.5)
                    all_reviews: List[Dict] = []
                    page_num = 0
                    while True:
                        page_num += 1
                        log.info(f"[page {page_num}] extracting reviews...")
                        reviews = await self.extract_reviews_from_page(page)
                        if not reviews:
                            log.info("No reviews extracted on this page. Stopping.")
                            if self.debug:
                                await self.debug_dump(page, f"empty_p{page_num}")
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

    async def accept_cookies(self, page: Page) -> bool:
        selectors = [
            "#onetrust-accept-btn-handler",
            "button#onetrust-accept-btn-handler",
            "button:has-text('Accept All')",
            "button:has-text('Accept all')",
            "button:has-text('Accept Cookies')",
            "button:has-text('I accept')",
            "button:has-text('I agree')",
            "button:has-text('Allow all')",
            "[data-testid='uc-accept-all-button']",
            "button[aria-label='Accept cookies']",
            "button:has-text('Got it')",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    await page.wait_for_timeout(300)
                    return True
            except Exception:
                continue
        return False

    async def debug_dump(self, page: Page, label: str) -> None:
        try:
            ensure_outputs_dir()
            name = self.__class__.__name__.lower().replace("scraper", "")
            base = pathlib.Path("outputs") / f"debug_{name}_{label}"
            html = await page.content()
            base.with_suffix(".html").write_text(html, encoding="utf-8")
            await page.screenshot(path=str(base.with_suffix(".png")))
        except Exception:
            pass
