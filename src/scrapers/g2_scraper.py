# src/scrapers/g2_scraper.py
from src.scrapers.async_base_scraper import AsyncBaseScraper
from typing import List, Dict, Optional
from urllib.parse import quote
import logging
from datetime import date

# API-first dependencies
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - optional import, validated at runtime
    requests = None  # type: ignore
from src.utils import parse_date_fuzzy

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
                reviews: List[Dict] = []

                # Try a small scroll to trigger lazy content
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(600)
                except Exception:
                    pass

                # 1) JSON-LD extraction
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


# --- API-first helper ---
def fetch_g2_reviews_api(
    product_uuid: str,
    token: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    limit: Optional[int] = None,
    debug: bool = False,
) -> List[Dict]:
    """
    Fetch reviews from G2's data API for a given product UUID.

    This function does not require Playwright and is suitable for Windows/Python 3.13.
    Note: The exact endpoint/params may vary depending on your G2 plan. Adjust as needed.
    """
    if requests is None:
        raise RuntimeError("'requests' package not installed. Add it to requirements.txt and install.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "saas-review-scraper/0.1",
    }

    # Commonly observed pattern; update if your contract differs
    base_url = f"https://data.g2.com/api/v1/products/{product_uuid}/reviews"

    collected: List[Dict] = []
    page = 1
    per_page = 50

    while True:
        params = {"page": page, "per_page": per_page}
        if start:
            params["start_date"] = start.isoformat()
        if end:
            params["end_date"] = end.isoformat()

        resp = requests.get(base_url, headers=headers, params=params, timeout=30)
        if debug:
            log.debug("G2 API GET %s | status=%s", resp.url, resp.status_code)
        if resp.status_code == 401:
            raise RuntimeError("G2 API unauthorized. Check token.")
        if resp.status_code >= 400:
            raise RuntimeError(f"G2 API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()

        # data may be a dict with items or a list directly depending on contract
        items = data.get("data") if isinstance(data, dict) else data
        if not items:
            break

        for it in items:
            # normalize fields
            title = it.get("title") or it.get("headline")
            body = it.get("review") or it.get("body") or it.get("text")
            dt = it.get("created_at") or it.get("date") or it.get("published_at")
            rating = it.get("rating") or it.get("stars")
            reviewer = (
                (it.get("user") or {}).get("name")
                if isinstance(it.get("user"), dict)
                else it.get("reviewer_name")
            )
            source_url = it.get("url") or it.get("source_url")

            d = None
            if isinstance(dt, str):
                d = parse_date_fuzzy(dt)

            collected.append(
                {
                    "title": title,
                    "review": body,
                    "date": d.isoformat() if d else dt,
                    "rating": float(rating) if rating is not None else None,
                    "reviewer_name": reviewer,
                    "source_url": source_url,
                }
            )
            if limit is not None and len(collected) >= limit:
                return collected[:limit]

        # pagination end condition
        if len(items) < per_page:
            break
        page += 1

    return collected
