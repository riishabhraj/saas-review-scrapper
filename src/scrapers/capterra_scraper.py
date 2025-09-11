# src/scrapers/capterra_scraper.py
from src.scrapers.async_base_scraper import AsyncBaseScraper
from typing import List, Dict, Optional
from urllib.parse import quote
import logging

log = logging.getLogger("capterrascraper")

class CapterraScraper(AsyncBaseScraper):
    # We'll try regional .in first (as observed) then fallback to .com
    BASE_SEARCH_IN = "https://www.capterra.in/search/?q={q}"
    BASE_SEARCH_COM = "https://www.capterra.com/search/?q={q}"

    async def find_product_page(self, page) -> Optional[str]:
        if self.product_url:
            return self.product_url
        q = quote(self.company)
        tried = []
        candidate = None
        origin = None
        for base in [self.BASE_SEARCH_IN, self.BASE_SEARCH_COM]:
            search_url = base.format(q=q)
            tried.append(search_url)
            log.info(f"Searching Capterra: {search_url}")
            try:
                await page.goto(search_url, wait_until="load")
            except Exception:
                continue
            current_url = page.url
            if current_url.startswith("https://www.capterra."):
                # Derive origin (protocol + host)
                parts = current_url.split("/search")[0].split("/")
                origin = parts[0] + "//" + parts[2]
            else:
                origin = "https://www.capterra.com"

            selectors = [
                "a.product-card__title",
                "a.product-name",
                "a[href*='/software/']",
                "a[href*='/p/']",
            ]
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if not el:
                        continue
                    href = el.get_attribute("href")
                    if not href:
                        continue
                    if href.startswith("/") and origin:
                        href = origin + href
                    candidate = href
                    break
                except Exception:
                    continue
            if candidate:
                break

        if not candidate:
            log.warning(f"Could not auto-find Capterra product page after trying: {tried}. Provide --product-url")
            return None
        # Normalize to reviews page if needed (Capterra sometimes uses /reviews/ path segment)
        if "/reviews" not in candidate:
            # Heuristic: append /reviews if base product page
            if candidate.count("/") < 6:
                candidate = candidate.rstrip("/") + "/reviews"
        log.info(f"Found product page: {candidate}")
        return candidate

    async def extract_reviews_from_page(self, page) -> List[Dict]:
        # Similar structure to G2 but with Capterra-specific selectors.
        # Inspect page and implement exact selectors; return list of dicts like G2.
        reviews = []
        # TODO: implement exactly like G2 but with capterra selectors
        els = await page.query_selector_all("div.review, article, div[class*='ReviewCard']")
        for el in els:
            try:
                text = (await el.inner_text()).strip()
            except Exception:
                text = ""
            title = None
            date = None
            rating = None
            reviewer_name = None
            try:
                h = await el.query_selector("h3")
                if h:
                    title = (await h.inner_text()).strip()
            except Exception:
                pass
            reviews.append({
                "title": title,
                "review": text,
                "date": date,
                "rating": rating,
                "reviewer_name": reviewer_name
            })
        return reviews
