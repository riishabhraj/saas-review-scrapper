# src/scrapers/capterra_scraper.py
from src.scrapers.async_base_scraper import AsyncBaseScraper
from typing import List, Dict, Optional
from urllib.parse import quote, urlparse
import logging, re
from datetime import date
import random, asyncio

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

    async def _humanize(self, page):
        """Light human-like signals to reduce bot blocks."""
        try:
            await page.wait_for_timeout(random.randint(300, 650))
            await page.mouse.move(random.randint(50, 400), random.randint(50, 300))
            await page.keyboard.press("PageDown")
            await page.wait_for_timeout(random.randint(200, 400))
        except Exception:
            pass

    async def _harden_page(self, page):
        """Mask basic automation fingerprints."""
        js_patches = [
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});",
            "window.chrome = { runtime: {} };",
            "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});",
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});",
        ]
        for js in js_patches:
            try:
                await page.add_init_script(js)
            except Exception:
                pass

    async def find_product_page(self, page) -> Optional[str]:
        await self._harden_page(page)
        if self.product_url:
            url = self.product_url.strip()
            # Ensure absolute URL
            if url.startswith("//"):
                url = "https:" + url
            elif not re.match(r"^https?://", url):
                url = "https://" + url.lstrip("/")
            # Normalize to reviews path if not already there
            try:
                parsed = urlparse(url)
                host = parsed.netloc or ""
                path = parsed.path or ""
                # If it's already a /reviews path, keep as-is
                if "/reviews/" in path:
                    return url
                # On capterra.in, convert /software/<id>/<slug> -> /reviews/<id>/<slug>
                if host.endswith("capterra.in"):
                    m = re.match(r"^/software/(\d+)/(.*)$", path)
                    if m:
                        url = f"https://{host}/reviews/{m.group(1)}/{m.group(2).strip('/')}"
                        return url
                # Generic fallback: append /reviews
                if "/reviews" not in url:
                    url = url.rstrip("/") + "/reviews"
            except Exception:
                if "/reviews" not in url:
                    url = url.rstrip("/") + "/reviews"
            return url

        # Shortcut: if company looks like it already contains an id pattern (e.g., "Smartsheet (79104)") extract id
        explicit_id = None
        try:
            m_id = re.search(r"(\d{4,8})", self.company)
            if m_id:
                explicit_id = m_id.group(1)
        except Exception:
            pass
        # If we have explicit id we can build a direct reviews URL without search
        if explicit_id:
            slug = re.sub(r"[^a-z0-9]+", "-", self.company.lower()).strip("-")
            direct = f"https://www.capterra.com/reviews/{explicit_id}/{slug}" if explicit_id else None
            if direct:
                return direct

        # --- Auto-discover via search (fallback) ---
        q = quote(self.company)
        tried: List[str] = []
        candidate: Optional[str] = None  # final normalized URL
        origin: Optional[str] = None
        import re as _re
        company_slug = _re.sub(r"[^a-z0-9]+", "-", self.company.lower()).strip("-")

        # Try .com first then .in to reduce early regional blocks
        for base in [self.BASE_SEARCH_COM, self.BASE_SEARCH_IN]:
            search_url = base.format(q=q)
            tried.append(search_url)
            log.info(f"Searching Capterra: {search_url}")
            try:
                await page.goto(search_url, wait_until="load")
            except Exception:
                continue

            # Early block detection (simple heuristic keywords)
            try:
                body_txt = (await page.inner_text("body")).lower()
                if any(k in body_txt for k in ["access denied", "request blocked", "unusual traffic", "captcha"]):
                    if self.debug:
                        log.warning(f"Blocked at search page {search_url}; dumping debug and retrying next domain")
                        try:
                            await self.debug_dump(page, "blocked_search")
                        except Exception:
                            pass
                    await page.wait_for_timeout(random.randint(1200, 2200))
                    continue
            except Exception:
                pass

            current_url = page.url
            if current_url.startswith("https://www.capterra."):
                parts = current_url.split("/search")[0].split("/")
                if len(parts) >= 3:
                    origin = parts[0] + "//" + parts[2]
            if not origin:
                origin = "https://www.capterra.com"

            # Poll for anchors (sometimes lazy scripts hydrate slowly)
            anchors = []
            for attempt in range(6):  # up to ~6 * 2s = 12s
                try:
                    await page.wait_for_selector(
                        "a.entry[href^='/software/'], a.product-card__title, a.product-name, a[href*='/software/'], a[href*='/p/']",
                        timeout=2500
                    )
                except Exception:
                    pass
                try:
                    anchors = await page.evaluate("""
                        () => Array.from(document.querySelectorAll(
                            "a.entry[href^='/software/'], a.product-card__title, a.product-name, a[href*='/software/'], a[href*='/p/'], a[href^='/reviews/']"
                        )).map(a => ({
                            href: a.getAttribute('href') || '',
                            text: (a.textContent || '').trim()
                        }))
                    """) or []
                except Exception:
                    anchors = []
                if anchors:
                    break
                await page.wait_for_timeout(1500)

            if self.debug:
                log.debug("Capterra search anchors found: %s", len(anchors))
                try:
                    await self.debug_dump(page, f"search_attempt_{len(anchors)}")
                except Exception:
                    pass

            # Priority: first explicit search result anchor a.entry[href^='/software/']
            primary = None
            for a in anchors:
                href = a.get('href') or ''
                if href.startswith('/software/'):
                    primary = href
                    break

            chosen_abs = None
            if primary:
                chosen_abs = origin + primary if primary.startswith('/') else primary
            else:
                # Fallback to scoring if no primary
                ranked: List[tuple] = []
                for a in anchors:
                    href = a.get("href") or ""
                    if not href:
                        continue
                    if not any(x in href for x in ["/software/", "/reviews/", "/p/"]):
                        continue
                    abs_href = origin + href if href.startswith("/") and origin else href
                    score = 0
                    if "/software/" in href:
                        score += 50
                    if "/p/" in href:
                        score += 40
                    if "/reviews/" in href:
                        score += 30
                    if company_slug and company_slug in href.lower():
                        score += 35
                    try:
                        tail = urlparse(abs_href).path.rstrip("/").split("/")[-1].lower()
                        if tail == company_slug:
                            score += 25
                    except Exception:
                        pass
                    ranked.append((score, abs_href, href))
                ranked.sort(reverse=True, key=lambda t: t[0])
                if self.debug and ranked:
                    log.debug("Capterra ranked fallback candidates: %s", [r[:2] for r in ranked[:6]])
                if ranked:
                    chosen_abs = ranked[0][1]

            if not chosen_abs:
                continue

            # Humanized delay before interaction
            await page.wait_for_timeout(random.randint(400, 900))

            # Try clicking relative anchor for chosen
            clicked = False
            rel_part = urlparse(chosen_abs).path
            for target in [rel_part, chosen_abs]:
                try:
                    el = await page.query_selector(f"a[href='{target}']")
                    if el:
                        await el.click()
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
                        clicked = True
                        break
                except Exception:
                    continue
            nav_url = page.url if clicked else chosen_abs
            # Second-stage block detection on product page (pre-normalization)
            try:
                body_txt = (await page.inner_text("body")).lower()
                if any(k in body_txt for k in ["access denied", "request blocked", "unusual traffic", "captcha"]):
                    if self.debug:
                        log.warning("Blocked after clicking product link; attempting next domain if available")
                        try:
                            await self.debug_dump(page, "blocked_product")
                        except Exception:
                            pass
                    candidate = None
                    # Go to next base domain
                    continue
            except Exception:
                pass
            try:
                parsed = urlparse(nav_url)
                path = parsed.path or ""
                host = parsed.netloc or "www.capterra.com"
                if "/reviews/" not in path:
                    m = re.search(r"/software/(\d+)/(.*)", path) or re.search(r"/p/(\d+)/(.*)", path)
                    if m:
                        nav_url = f"https://{host}/reviews/{m.group(1)}/{m.group(2).strip('/')}"
                    else:
                        nav_url = nav_url.rstrip("/") + "/reviews"
            except Exception:
                if "/reviews" not in nav_url:
                    nav_url = nav_url.rstrip("/") + "/reviews"
            candidate = nav_url
            log.info(f"Found product page (via search): {candidate}")
            break

        if not candidate:
            log.warning(f"Could not auto-find Capterra product page after trying: {tried}. Provide --product-url")
            return None
        return candidate

    async def extract_reviews_from_page(self, page) -> List[Dict]:
        await self._harden_page(page)
        await self._humanize(page)
        reviews: List[Dict] = []
        # Light scroll to trigger lazy load
        try:
            await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight*0.7))")
            await page.wait_for_timeout(600)
        except Exception:
            pass

        # Try to accept cookies if present
        try:
            from src.scrapers.async_base_scraper import AsyncBaseScraper as _B
            await _B.accept_cookies(self, page)  # reuse helper
        except Exception:
            pass

        # Wait for actual review cards present on Capterra reviews page
        try:
            await page.wait_for_selector("#reviews .review-card[data-entity='review'], #reviews [data-container-view='ca-review']", timeout=10000)
        except Exception:
            await page.wait_for_timeout(500)

        # Select review cards precisely
        els = await page.query_selector_all("#reviews .review-card[data-entity='review'], #reviews [data-container-view='ca-review']")

        import re as _re
        for el in els:
            try:
                text = (await el.inner_text()).strip()
            except Exception:
                text = ""

            # Title: h3.fs-3.fw-bold ...
            title = None
            try:
                t = await el.query_selector("h3.fs-3.fw-bold")
                if t:
                    tt = (await t.inner_text()).strip()
                    if tt:
                        title = tt
            except Exception:
                pass

            # Date: the block right after the title
            date_txt = None
            date_obj = None
            try:
                dnode = await el.query_selector("h3.fs-3.fw-bold + .fs-5.text-neutral-90")
                if not dnode:
                    # fallback to the first matching fs-5 date within the header row
                    dnode = await el.query_selector(".d-lg-flex.align-items-top.justify-content-between.mb-2 .fs-5.text-neutral-90")
                if dnode:
                    dtxt = (await dnode.inner_text()).strip()
                    if dtxt:
                        date_txt = dtxt
                        try:
                            d = parse_date_fuzzy(dtxt)
                            if d:
                                date_obj = d
                        except Exception:
                            pass
            except Exception:
                pass

            # Rating: number shown next to stars inside .star-rating-component .ms-1
            rating = None
            try:
                # Prefer the rating text next to the filled stars
                rnode = await el.query_selector(".text-neutral-90.fs-5 .star-rating-component .ms-1, .star-rating-component .ms-1")
                if rnode:
                    rtxt = (await rnode.inner_text()).strip()
                    m = _re.search(r"([0-9]+(?:\.[0-9]+)?)", rtxt)
                    if m:
                        rating = float(m.group(1))
                if rating is None:
                    # Fallback: count star icons within stars-wrapper
                    full = len(await el.query_selector_all(".star-rating-component .stars-wrapper .icon.icon-star"))
                    half = len(await el.query_selector_all(".star-rating-component .stars-wrapper .icon.icon-star-half-empty"))
                    empty = len(await el.query_selector_all(".star-rating-component .stars-wrapper .icon.icon-star-o"))
                    if full or half:
                        rating = float(min(5.0, full + (0.5 if half > 0 else 0.0)))
            except Exception:
                pass

            # Reviewer name
            reviewer_name = None
            try:
                r = await el.query_selector(".fw-600.mb-1")
                if r:
                    rn = (await r.inner_text()).strip()
                    if rn:
                        reviewer_name = rn
            except Exception:
                pass

            # Body: first main paragraph block inside the card
            body_text = None
            try:
                # Prefer the first overall review paragraph (fs-4 lh-2 text-neutral-99)
                b = await el.query_selector(".fs-4.lh-2.text-neutral-99")
                if b:
                    bt = (await b.inner_text()).strip()
                    if bt:
                        body_text = bt
            except Exception:
                pass

            reviews.append({
                "title": title,
                "review": body_text or text,
                # Provide a real date object when possible; avoid unparseable strings
                "date": date_obj,
                "rating": rating,
                "reviewer_name": reviewer_name,
            })
            # Obey limit if provided on the scraper
            try:
                if self.limit is not None and len(reviews) >= int(self.limit):
                    break
            except Exception:
                pass
        return reviews


# --- API-first helpers ---
def discover_capterra_product_id(product_url: str) -> Optional[str]:
    """Attempt to discover the Capterra productId from the product page HTML."""
    # 1) Try to parse directly from canonical /p/<id>/ path in the URL (no network needed)
    m = re.search(r"/p/(\d+)/", product_url)
    if m:
        return m.group(1)
    # Handle regional /reviews/<id>/ paths
    m = re.search(r"/reviews/(\d+)/", product_url)
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
    per_page = 50
    page = 1  # FIX: initialize page counter
    while True:
        params = {
            "productId": product_id,
            "page": page,
            "pageSize": per_page,
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
            # Date filtering
            if isinstance(d, date):
                if start and d < start:
                    continue
                if end and d > end:
                    continue
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
