"""TrustRadius scraper implementation (heuristic selectors).

NOTE: TrustRadius DOM / selectors can change. Inspect the live site and
update selectors if extraction stops working. This scraper follows the
same interface pattern as G2 & Capterra scrapers.
"""
from urllib.parse import quote
from typing import List, Dict, Optional
import logging
from datetime import date
import re

from src.scrapers.async_base_scraper import AsyncBaseScraper
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore
from src.utils import parse_date_fuzzy

log = logging.getLogger("trustradiusscraper")


class TrustRadiusScraper(AsyncBaseScraper):
    BASE_SEARCH = "https://www.trustradius.com/search?query={q}"
    def _slugify(self, name: str) -> str:
        import re
        s = name.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = re.sub(r"-+", "-", s).strip("-")
        return s or name.lower()

    async def find_product_page(self, page) -> Optional[str]:
        # 0. User supplied direct URL
        if self.product_url:
            return self.product_url.rstrip('/')

        # 1. Direct slug attempt
        slug = self._slugify(self.company)
        direct = f"https://www.trustradius.com/products/{slug}/reviews"
        try:
            await page.goto(direct, wait_until="domcontentloaded")
            try:
                await page.wait_for_selector("article, div.review, div[class*='review-card']", timeout=4000)
                return direct
            except Exception:
                snippet = (await page.content())[:4000].lower()
                if "not found" not in snippet and "error" not in snippet:
                    return direct
        except Exception:
            pass

        # 2. Search fallback
        q = quote(self.company)
        search_url = self.BASE_SEARCH.format(q=q)
        log.info("Searching TrustRadius: %s", search_url)
        try:
            await page.goto(search_url, wait_until="networkidle")
        except Exception:
            return None

        candidates = await page.query_selector_all("a[href*='/products/']")
        href: Optional[str] = None
        for c in candidates:
            try:
                h = await c.get_attribute("href")
                if h and "/products/" in h:
                    href = h
                    break
            except Exception:
                continue
        if not href:
            log.warning("No product link found in search results. Consider --product-url")
            return None
        if href.startswith('/'):
            href = "https://www.trustradius.com" + href
        if not href.endswith('/reviews') and '/reviews' not in href:
            href = href.rstrip('/') + '/reviews'
        log.info("Found product page: %s", href)
        return href

    async def extract_reviews_from_page(self, page) -> List[Dict]:
        reviews: List[Dict] = []

        # Scroll to trigger lazy loading
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
        except Exception:
            pass
        try:
            for _ in range(2):
                await page.evaluate("window.scrollBy(0, window.innerHeight * 0.6)")
                await page.wait_for_timeout(350)
        except Exception:
            pass

        # JSON-LD first
        try:
            scripts = await page.query_selector_all("script[type='application/ld+json']")
            import json, re
            for s in scripts:
                try:
                    raw = await s.inner_text()
                    if not raw:
                        continue
                    data = json.loads(raw)
                    nodes = data if isinstance(data, list) else [data]
                    for node in nodes:
                        if isinstance(node, dict) and node.get("@type") == "Review":
                            title = node.get("name") or node.get("headline")
                            body = node.get("reviewBody") or node.get("description")
                            dt = node.get("datePublished") or node.get("dateCreated")
                            rr = node.get("reviewRating") or {}
                            rating = None
                            if isinstance(rr, dict):
                                try:
                                    rating = float(rr.get("ratingValue")) if rr.get("ratingValue") is not None else None
                                except Exception:
                                    rating = None
                            d = parse_date_fuzzy(dt) if isinstance(dt, str) else None
                            reviews.append({
                                "title": title,
                                "review": body,
                                "date": d.isoformat() if d else dt,
                                "rating": rating,
                                "reviewer_name": (node.get("author") or {}).get("name") if isinstance(node.get("author"), dict) else None,
                                "reviewer_role": None,
                                "reviewer_company": None,
                                "source_url": None,
                                "raw_html_snippet": None,
                            })
                except Exception:
                    continue
            if reviews:
                return reviews
        except Exception:
            pass

        # Container selectors
        container_selectors = [
            "div.tr-review-card",
            "div.review-card",
            "div[data-test='review-card']",
            "article[data-test*='review']",
            "article.review",
            "section.review",
            "article",
            "div.review"
        ]
        elements: List = []
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
            date_text = None
            rating = None
            reviewer_name = None
            reviewer_role = None
            reviewer_company = None
            source_url = None

            # Title
            for tsel in [
                "h3[data-test='review-title']",
                "h2[data-test='review-title']",
                "div.review-title",
                "header h3",
                "h3",
                "h2"
            ]:
                try:
                    t = await el.query_selector(tsel)
                    if t:
                        txt = (await t.inner_text()).strip()
                        if txt:
                            title = txt
                            break
                except Exception:
                    continue

            # Date
            for dsel in [
                "time[datetime]",
                "time",
                "span[itemprop='datePublished']",
                "span.review-date",
                "div.review-date",
                "span:has-text('202')",
            ]:
                try:
                    dn = await el.query_selector(dsel)
                    if dn:
                        dtxt = (await dn.inner_text()).strip()
                        if dtxt:
                            date_text = dtxt
                            break
                except Exception:
                    continue

            # Rating
            try:
                rnode = await el.query_selector(
                    "[aria-label*='out of 10'], [aria-label*='out of 5'], span[class*='rating'], div[class*='rating'], span[data-test='rating']"
                )
                if rnode:
                    al = await rnode.get_attribute("aria-label")
                    if not al:
                        try:
                            al = (await rnode.inner_text()).strip()
                        except Exception:
                            al = None
                    if al:
                        m = re.search(r"(\d+(?:\.\d+)?)", al)
                        if m:
                            rating = float(m.group(1))
                            if "/10" in al:
                                rating = round((rating / 10.0) * 5.0, 2)
            except Exception:
                pass

            # Reviewer name
            try:
                rn = await el.query_selector(
                    "span.reviewer-name, div.reviewer-name, a.author, span[class*='user'], span[data-test='reviewer-name']"
                )
                if rn:
                    reviewer_name = (await rn.inner_text()).strip()
            except Exception:
                pass
            # Reviewer role
            try:
                rr = await el.query_selector(
                    "span.reviewer-role, div.reviewer-role, span[class*='title'], span[data-test='reviewer-role']"
                )
                if rr:
                    reviewer_role = (await rr.inner_text()).strip()
            except Exception:
                pass
            # Reviewer company
            try:
                rc = await el.query_selector(
                    "span.company, div.company, span[class*='company'], span[data-test='reviewer-company']"
                )
                if rc:
                    reviewer_company = (await rc.inner_text()).strip()
            except Exception:
                pass

            # Source URL
            try:
                a = await el.query_selector("a[href*='/reviews/']")
                if a:
                    href = await a.get_attribute("href")
                    if href:
                        source_url = href if href.startswith("http") else "https://www.trustradius.com" + href
            except Exception:
                pass

            reviews.append({
                "title": title,
                "review": full_text,
                "date": date_text,
                "rating": rating,
                "reviewer_name": reviewer_name,
                "reviewer_role": reviewer_role,
                "reviewer_company": reviewer_company,
                "source_url": source_url,
                "raw_html_snippet": None,
            })
        # Fallback heuristic: if titles are all null but we have review blocks, attempt to derive
        if reviews and all(r.get("title") is None for r in reviews):
            inferred: List[Dict] = []
            for r in reviews:
                txt = r.get("review") or ""
                lines = [l.strip() for l in txt.splitlines() if l.strip()]
                inferred_title = None
                inferred_rating = r.get("rating")
                inferred_date = r.get("date")
                # Rating pattern e.g. 'Rating: 10 out of 10' or 'Rating: 4 out of 5'
                for ln in lines[:6]:
                    mrate = re.search(r"rating:\s*(\d+(?:\.\d+)?)\s*out\s*of\s*(10|5)", ln, re.I)
                    if mrate and inferred_rating is None:
                        val = float(mrate.group(1))
                        scale = float(mrate.group(2))
                        if scale == 10:
                            val = round((val/10.0)*5.0, 2)
                        inferred_rating = val
                # Date pattern e.g. 'June 30, 2025'
                if not inferred_date:
                    for ln in lines[:12]:
                        mdt = re.search(r"([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})", ln)
                        if mdt:
                            d = parse_date_fuzzy(mdt.group(1))
                            if d:
                                inferred_date = d.isoformat()
                                break
                # Title inference: first non-meta line before 'Rating:'
                meta_markers = ("rating:", "incentivized", "vetted review", "verified user", "use cases", "pros", "cons", "likelihood to recommend")
                for ln in lines:
                    low = ln.lower()
                    if any(m in low for m in meta_markers):
                        continue
                    # Avoid lines that look like names (two words capitalized) for title
                    if re.match(r"^[A-Z][a-z]+\s+[A-Z][a-z]+$", ln):
                        continue
                    inferred_title = ln[:140]
                    break
                if inferred_title or inferred_rating or inferred_date:
                    r["title"] = inferred_title
                    if inferred_rating is not None:
                        r["rating"] = inferred_rating
                    if inferred_date:
                        r["date"] = inferred_date
                inferred.append(r)
            reviews = inferred
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
