# src/scrapers/capterra_scraper.py
from src.scrapers.async_base_scraper import AsyncBaseScraper
from typing import List, Dict, Optional
from urllib.parse import quote, urlparse
import logging, re
from datetime import date

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore
from src.utils import parse_date_fuzzy

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


# --- API-first helpers ---
def discover_capterra_product_id(product_url: str) -> Optional[str]:
    """Attempt to discover the Capterra productId from the product page HTML."""
    # 1) Try to parse directly from canonical /p/<id>/ path in the URL (no network needed)
    m = re.search(r"/p/(\d+)/", product_url)
    if m:
        return m.group(1)

    # 2) If not present, optionally fetch page HTML as fallback (may be blocked by bot protections)
    if requests is None:
        raise RuntimeError("'requests' package not installed. Add it to requirements.txt and install.")
    parsed = urlparse(product_url)
    if not parsed.scheme:
        product_url = "https://" + product_url.lstrip("/")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.capterra.com/",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }
    r = requests.get(product_url, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Capterra product page fetch failed {r.status_code}")
    html = r.text
    for pattern in [r'"productId"\s*:\s*"?(\d+)"?', r'data-product-id=\"(\d+)\"']:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def fetch_capterra_reviews_api(
    product_id: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    limit: Optional[int] = None,
    debug: bool = False,
) -> List[Dict]:
    """
    Calls Capterra's reviews JSON endpoint used by the product page XHR.
    Endpoint varies by region; we'll use .com by default.
    """
    if requests is None:
        raise RuntimeError("'requests' package not installed. Add it to requirements.txt and install.")
    base = "https://www.capterra.com/spotlight/rest/reviews"
    headers = {
        "Accept": "application/json",
        "User-Agent": "saas-review-scraper/0.1",
        "Referer": "https://www.capterra.com/",
    }
    collected: List[Dict] = []
    page = 1
    per_page = 50
    while True:
        params = {
            "productId": product_id,
            "page": page,
            "pageSize": per_page,
            # Other filters can be added if known (ratings, sort, etc.)
        }
        r = requests.get(base, headers=headers, params=params, timeout=30)
        if debug:
            log.debug("Capterra API GET %s | status=%s", r.url, r.status_code)
        if r.status_code >= 400:
            raise RuntimeError(f"Capterra API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        items = data.get("reviews") or data.get("data") or []
        if not items:
            break
        for it in items:
            title = it.get("title") or it.get("headline")
            body = it.get("review") or it.get("text") or it.get("body")
            dt = it.get("date") or it.get("createdAt") or it.get("created_at")
            rating = it.get("rating") or it.get("overallRating")
            reviewer = None
            user = it.get("user") or {}
            if isinstance(user, dict):
                reviewer = user.get("name") or user.get("displayName")
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
                }
            )
            if limit is not None and len(collected) >= limit:
                return collected[:limit]
        if len(items) < per_page:
            break
        page += 1
    return collected
