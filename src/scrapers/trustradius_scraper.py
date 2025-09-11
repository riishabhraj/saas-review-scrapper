"""TrustRadius scraper implementation (heuristic selectors).

NOTE: TrustRadius DOM / selectors can change. Inspect the live site and
update selectors if extraction stops working. This scraper follows the
same interface pattern as G2 & Capterra scrapers.
"""
from urllib.parse import quote
from typing import List, Dict, Optional
import logging
from datetime import date

from src.scrapers.async_base_scraper import AsyncBaseScraper
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore
from src.utils import parse_date_fuzzy

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
        # small scroll to trigger lazy content
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)
        except Exception:
            pass

        # JSON-LD first
        try:
            scripts = await page.query_selector_all("script[type='application/ld+json']")
            import json
            from src.utils import parse_date_fuzzy as _p
            for s in scripts:
                try:
                    raw = await s.inner_text()
                    if not raw:
                        continue
                    data = json.loads(raw)
                    nodes = data if isinstance(data, list) else [data]
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        if node.get("@type") == "Review":
                            title = node.get("name") or node.get("headline")
                            body = node.get("reviewBody") or node.get("description")
                            dt = node.get("datePublished") or node.get("dateCreated")
                            rating = None
                            rr = node.get("reviewRating")
                            if isinstance(rr, dict):
                                rv = rr.get("ratingValue")
                                try:
                                    rating = float(rv) if rv is not None else None
                                except Exception:
                                    rating = None
                            author = node.get("author")
                            reviewer = None
                            if isinstance(author, dict):
                                reviewer = author.get("name")
                            d = _p(dt) if isinstance(dt, str) else None
                            reviews.append({
                                "title": title,
                                "review": body,
                                "date": d.isoformat() if d else dt,
                                "rating": rating,
                                "reviewer_name": reviewer,
                            })
                except Exception:
                    continue
            if reviews:
                return reviews
        except Exception:
            pass
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


# --- API-first helper (via Apify TrustRadius actor) ---
def fetch_trustradius_via_apify(
    product_url: str,
    apify_token: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    limit: Optional[int] = None,
    debug: bool = False,
) -> List[Dict]:
    """
    Uses an Apify TrustRadius actor to fetch reviews without Playwright locally.
    You need an Apify account and an actor ID that returns TrustRadius reviews.
    The default public actor "apify/website-content-crawler" can be configured,
    but ideally use a dedicated TrustRadius actor if available.
    """
    if requests is None:
        raise RuntimeError("'requests' package not installed. Add it to requirements.txt and install.")

    # Example using generic Apify actor; replace ACTOR_ID with a TrustRadius-specific actor if you have one.
    ACTOR_ID = "apify/website-content-crawler"
    start_url = product_url
    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={apify_token}"
    payload = {
        "startUrls": [{"url": start_url}],
        # Narrow to the reviews area via link selectors or pseudo URL patterns if needed.
        "maxDepth": 1,
        "useRequestQueue": False,
        "crawlerType": "playwright:chromium",
        "maxRequestsPerCrawl": 50,
        "headless": True,
    }
    r = requests.post(run_url, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Apify run start failed {r.status_code}: {r.text[:300]}")
    run = r.json().get("data") or {}
    items_url = None
    dataset_id = (run.get("defaultDatasetId") if isinstance(run, dict) else None)
    if dataset_id:
        items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?clean=true&token={apify_token}"
    else:
        # Fallback: get from run resource
        items_url = (run.get("defaultDatasetUrl") if isinstance(run, dict) else None)
    if not items_url:
        raise RuntimeError("Apify run didn't return dataset info; cannot fetch items.")

    # Poll for dataset readiness (simple loop)
    import time
    for _ in range(30):
        ir = requests.get(items_url, timeout=30)
        if ir.status_code == 200 and ir.headers.get("content-type", "").startswith("application/json"):
            items = ir.json()
            if isinstance(items, list) and items:
                break
        time.sleep(2)
    else:
        items = []

    collected: List[Dict] = []
    for it in items:
        # Heuristically pull text content from the page section.
        text = it.get("text") or it.get("markdown") or it.get("body") or ""
        url = it.get("url")
        dt = it.get("date") or it.get("publishedAt")
        d = parse_date_fuzzy(dt) if isinstance(dt, str) else None
        collected.append(
            {
                "title": None,
                "review": text,
                "date": d.isoformat() if d else dt,
                "rating": None,
                "reviewer_name": None,
                "source_url": url,
            }
        )
        if limit is not None and len(collected) >= limit:
            return collected[:limit]

    return collected
