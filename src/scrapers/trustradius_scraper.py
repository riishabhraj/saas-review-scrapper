"""TrustRadius scraper implementation (heuristic selectors).

NOTE: TrustRadius DOM / selectors can change. Inspect the live site and
update selectors if extraction stops working. This scraper follows the
same interface pattern as G2 & Capterra scrapers.
"""
from urllib.parse import quote
from typing import List, Dict, Optional
import logging

from src.scrapers.async_base_scraper import AsyncBaseScraper

log = logging.getLogger("trustradiusscraper")


class TrustRadiusScraper(AsyncBaseScraper):
    BASE_SEARCH = "https://www.trustradius.com/search?query={q}"

    async def find_product_page(self, page) -> Optional[str]:
        # If direct URL provided, trust it.
        if self.product_url:
            return self.product_url

        q = quote(self.company)
        search_url = self.BASE_SEARCH.format(q=q)
        log.info("Searching TrustRadius: %s", search_url)
        await page.goto(search_url, wait_until="networkidle")

        # Heuristic: pick first product link containing /products/
        candidates = await page.query_selector_all("a[href*='/products/']")
        href = None
        for c in candidates:
            try:
                h = c.get_attribute("href")
                if h and "/products/" in h:
                    href = h
                    break
            except Exception:
                continue
        if not href:
            log.warning("No product link found in search results. Consider --product-url")
            return None
        if href.startswith("/"):
            href = "https://www.trustradius.com" + href

        # Ensure we are on the reviews tab if needed
        if not href.endswith("/reviews"):
            if href.rstrip("/").endswith("reviews"):
                pass
            elif "/reviews" not in href:
                href = href.rstrip("/") + "/reviews"

        log.info("Found product page: %s", href)
        return href

    async def extract_reviews_from_page(self, page) -> List[Dict]:
        reviews: List[Dict] = []
        # Candidate selectors for review container. Adjust after inspection.
        container_selectors = [
            "div.review-card",              # hypothetical
            "div.tr-review-card",           # variant
            "div[data-test='review-card']", # test id style
            "article",                      # fallback
            "div.review"                    # generic fallback
        ]
        elements = []
        for sel in container_selectors:
            try:
                found = await page.query_selector_all(sel)
            except Exception:
                found = []
            if found:
                elements = found
                break
        if not elements:
            elements = await page.query_selector_all("article, div[class*='review']")

        import re
        for el in elements:
            try:
                full_text = (await el.inner_text()).strip()
            except Exception:
                full_text = ""

            title = None
            date = None
            rating = None
            reviewer_name = None
            reviewer_role = None
            reviewer_company = None
            source_url = None

            # Title heuristics
            for tsel in ["h3", "h2", "div.review-title", "header h3"]:
                try:
                    t = await el.query_selector(tsel)
                    if t:
                        title = (await t.inner_text()).strip()
                        if title:
                            break
                except Exception:
                    continue

            # Date heuristics
            for dsel in [
                "time",
                "span[itemprop='datePublished']",
                "span.review-date",
                "div.review-date",
                "span:has-text('202')",  # crude year pattern
            ]:
                try:
                    dnode = await el.query_selector(dsel)
                    if dnode:
                        dtxt = (await dnode.inner_text()).strip()
                        if dtxt:
                            date = dtxt
                            break
                except Exception:
                    continue

            # Rating heuristics (aria-label or text like '4.5/10' or '4/5')
            try:
                rnode = await el.query_selector("[aria-label*='out of 10'], [aria-label*='out of 5'], span[class*='rating'], div[class*='rating']")
                if rnode:
                    al = rnode.get_attribute("aria-label") or (await rnode.inner_text())
                    if al:
                        m = re.search(r"(\d+(?:\.\d+)?)", al)
                        if m:
                            rating = float(m.group(1))
                            if "/10" in al and rating is not None:
                                rating = round((rating / 10.0) * 5.0, 2)
            except Exception:
                pass

            # Reviewer name / role / company heuristics
            try:
                rn = await el.query_selector("span.reviewer-name, div.reviewer-name, a.author, span[class*='user']")
                if rn:
                    reviewer_name = (await rn.inner_text()).strip()
            except Exception:
                pass
            try:
                rr = await el.query_selector("span.reviewer-role, div.reviewer-role, span[class*='title']")
                if rr:
                    reviewer_role = (await rr.inner_text()).strip()
            except Exception:
                pass
            try:
                rc = await el.query_selector("span.company, div.company, span[class*='company']")
                if rc:
                    reviewer_company = (await rc.inner_text()).strip()
            except Exception:
                pass

            # Source URL if anchor to full review exists
            try:
                a = await el.query_selector("a[href*='/reviews/']")
                if a:
                    href = a.get_attribute("href")
                    if href:
                        source_url = href if href.startswith("http") else "https://www.trustradius.com" + href
            except Exception:
                pass

            reviews.append({
                "title": title,
                "review": full_text,
                "date": date,
                "rating": rating,
                "reviewer_name": reviewer_name,
                "reviewer_role": reviewer_role,
                "reviewer_company": reviewer_company,
                "source_url": source_url,
                "raw_html_snippet": None,
            })
        return reviews

    # Optionally override go_to_next_page if TrustRadius uses explicit numbered pagination
    async def go_to_next_page(self, page) -> bool:
        selectors = [
            "a[rel='next']",
            "button:has-text('Next')",
            "a:has-text('Next')",
            "button:has-text('Load more')",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    try:
                        await page.wait_for_load_state("networkidle")
                    except Exception:
                        await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return await super().go_to_next_page(page)
