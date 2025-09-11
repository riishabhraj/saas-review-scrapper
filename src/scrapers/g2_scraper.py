# src/scrapers/g2_scraper.py
from src.scrapers.async_base_scraper import AsyncBaseScraper
from typing import List, Dict, Optional
from urllib.parse import quote
import logging

log = logging.getLogger("g2scraper")

class G2Scraper(AsyncBaseScraper):
    # Realistic G2 search URL pattern observed, includes utf8 checkmark + source param.
    BASE_SEARCH = "https://www.g2.com/search?utf8=%E2%9C%93&query={q}&source=search"
    # If user passes product_url, we will use it directly.

    async def find_product_page(self, page) -> Optional[str]:
        # If user provided direct product_url, use it
        if self.product_url:
            return self.product_url

        # otherwise try to use G2 search
        q = quote(self.company)
        search_url = self.BASE_SEARCH.format(q=q)
        log.info(f"Searching G2: {search_url}")
        await page.goto(search_url, wait_until="networkidle")

        # Attempt: wait briefly for any product links to render.
        try:
            await page.wait_for_timeout(500)  # small delay; adjust if needed
        except Exception:
            pass

        # Slug heuristic (company name to product slug) e.g. "HubSpot" -> "hubspot"
        slug = self.company.lower().strip().replace(" ", "-")
        possible_selectors = [
            f"a[href*='/products/{slug}/reviews']",
            f"a[href*='/products/{slug}']",
            "a.result-card__name",
            "a.search-result__title",
            "a[href*='/products/'][href$='/reviews']",
            "a[href*='/products/']"
        ]
        candidate: Optional[str] = None
        for sel in possible_selectors:
            try:
                el = await page.query_selector(sel)
                if not el:
                    continue
                href = el.get_attribute("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.g2.com" + href
                # Normalize to reviews page if not already there.
                if "/products/" in href and not href.rstrip("/").endswith("reviews"):
                    if "/reviews" not in href:
                        href = href.rstrip("/") + "/reviews"
                candidate = href
                break
            except Exception:
                continue
        if candidate:
            log.info("Found product page: %s", candidate)
        else:
            log.warning("Could not auto-find product page: consider passing --product-url")
        return candidate

    async def extract_reviews_from_page(self, page) -> List[Dict]:
        '''
        Extracts review elements on the current page and returns a list of dicts.
        Because site HTML may change, we try several selectors and fallback strategies.
        '''
        reviews = []
        # candidate selectors - update them by inspecting real G2 page
        candidate_selectors = [
            'div[data-testid="review-card"]',  # hypothetical test id
            'div.g2-review',                   # hypothetical class
            'article',                         # fallback: many sites wrap review in <article>
            'div.review'                       # fallback
        ]
        els = []
        for sel in candidate_selectors:
            try:
                found = await page.query_selector_all(sel)
            except Exception:
                found = []
            if found:
                els = found
                break

        if not els:
            # fallback: try to find by role=article
            els = await page.query_selector_all('article, div[class*="review"]')
        for el in els:
            try:
                text = (await el.inner_text()).strip()
            except Exception:
                text = ""
            # Try to get title, date, rating, reviewer, etc, using a few heuristics:
            title = None
            date = None
            rating = None
            reviewer_name = None
            source_url = None

            # title heuristics
            try:
                h = await el.query_selector("h3")
                if h:
                    title = (await h.inner_text()).strip()
            except Exception:
                pass

            # date heuristics
            # look for elements that look like a date (time tag, small tag, span with date text)
            for ds in ["time", "span.date", "span[class*='date']", "div[class*='date']", "span:has-text(',')"]:
                try:
                    dd = await el.query_selector(ds)
                    if dd:
                        dtext = (await dd.inner_text()).strip()
                        if dtext:
                            date = dtext
                            break
                except Exception:
                    continue

            # rating heuristic - look for aria-label like "5 out of 5 stars" or svg count
            try:
                r_el = await el.query_selector('[aria-label*="out of 5"]')
                if r_el:
                    rating_text = r_el.get_attribute("aria-label")
                    # parse first number
                    import re
                    m = re.search(r'([0-9](?:\.[0-9])?)', rating_text)
                    if m:
                        rating = float(m.group(1))
            except Exception:
                pass

            # reviewer name heuristic
            try:
                rn = await el.query_selector("div.reviewer-name, span.reviewer, span[class*='user']")
                if rn:
                    reviewer_name = (await rn.inner_text()).strip()
            except Exception:
                pass

            # source url: if individual review has anchor
            try:
                a = await el.query_selector("a[href*='/review/'], a[href*='#review']")
                if a:
                    href = a.get_attribute("href")
                    source_url = href if href.startswith("http") else "https://www.g2.com" + href
            except Exception:
                pass

            reviews.append({
                "title": title,
                "review": text,
                "date": date,
                "rating": rating,
                "reviewer_name": reviewer_name,
                "source_url": source_url,
                "raw_html_snippet": None
            })
        return reviews
