# src/scrapers/g2_scraper.py
from src.scrapers.async_base_scraper import AsyncBaseScraper
from typing import List, Dict, Optional
from urllib.parse import quote
import logging
from datetime import date
import json
import re
import html as htmllib
import random
from src.utils import parse_date_fuzzy

# API-first dependencies
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - optional import, validated at runtime
    requests = None  # type: ignore

log = logging.getLogger("g2scraper")

class G2Scraper(AsyncBaseScraper):
    BASE_SEARCH = "https://www.g2.com/search?utf8=%E2%9C%93&query={q}&source=search"

    _UA_POOL = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    ]
    _BLOCK_MARKERS = [
        "access blocked",
        "unusual activity",
        "verify you are a human",
        "bot detected",
        "perimeterx",
        "captcha",
        "recaptcha",
    ]

    async def _harden_page(self, page):
        try:
            vw = random.choice([(1366,768),(1440,900),(1536,864),(1280,800),(1920,1080)])
            await page.set_viewport_size({"width": vw[0] + random.randint(-25,15), "height": vw[1] + random.randint(-30,30)})
        except Exception:
            pass
        try:
            await page.add_init_script(
                """
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
                window.chrome = { runtime: {} };
                const pr = window.devicePixelRatio || 1;
                Object.defineProperty(window,'devicePixelRatio',{get:()=>pr + (Math.random()*0.00001)});
                Object.defineProperty(Notification,'permission',{get:()=> 'default'});
                """
            )
        except Exception:
            pass

    async def _human_mouse(self, page, moves=4):
        try:
            for _ in range(moves):
                await page.mouse.move(random.randint(40,900), random.randint(50,700), steps=random.randint(5,12))
                await page.wait_for_timeout(random.randint(120,340))
        except Exception:
            pass

    async def _scroll_slow(self, page, segments=3):
        try:
            for _ in range(segments):
                await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * (0.25 + Math.random()*0.45)))")
                await page.wait_for_timeout(random.randint(400,950))
        except Exception:
            pass

    async def _is_blocked(self, page) -> bool:
        try:
            snippet = (await page.content())[:15000].lower()
            return any(m in snippet for m in self._BLOCK_MARKERS)
        except Exception:
            return False

    def _slugify(self, name: str) -> str:
        s = name.lower().strip()
        s = re.sub(r"[^a-z0-9]+","-", s)
        s = re.sub(r"-+","-", s).strip('-')
        return s or name.lower()

    async def _type_like_user(self, page, selector: str, text: str):
        try:
            el = await page.wait_for_selector(selector, timeout=5000)
            await el.click()
            for ch in text:
                await page.keyboard.type(ch, delay=random.randint(70,160))
            await page.wait_for_timeout(random.randint(650,1150))
        except Exception:
            pass

    async def _user_flow_search_to_reviews(self, page, slug: str) -> Optional[str]:
        try:
            await self._harden_page(page)
            await page.goto("https://www.g2.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(random.randint(1200,2100))
            await self._human_mouse(page)
            await self._type_like_user(page, "input[name='query'], input[type='search']", self.company)
            await page.wait_for_timeout(random.randint(700,1300))
            selectors = [
                f"a[href*='/products/{slug}']",
                "a.result-card__name",
                "a.search-result__title",
                "a[href*='/products/']"
            ]
            candidate = None
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        href = await el.get_attribute("href")
                        if href:
                            if href.startswith('/'):
                                href = "https://www.g2.com" + href
                            candidate = href
                            await el.click()
                            await page.wait_for_timeout(random.randint(1600,2600))
                            break
                except Exception:
                    continue
            if not candidate:
                return None
            if await self._is_blocked(page):
                return None
            await self._scroll_slow(page, segments=random.randint(2,4))
            try:
                link = await page.query_selector("a[href$='/reviews'], a[href*='/reviews?']")
                if link:
                    await self._human_mouse(page, moves=2)
                    await link.click()
                    await page.wait_for_timeout(random.randint(1400,2300))
            except Exception:
                pass
            if "/reviews" not in page.url:
                await page.wait_for_timeout(random.randint(900,1500))
                await page.goto(f"https://www.g2.com/products/{slug}/reviews", wait_until="domcontentloaded")
                await page.wait_for_timeout(random.randint(900,1500))
            if await self._is_blocked(page):
                return None
            current = page.url.split('?')[0].rstrip('/')
            if current.endswith(f"/{slug}/reviews"):
                return current
        except Exception:
            return None
        return None

    async def find_product_page(self, page) -> Optional[str]:
        if self.product_url:
            return self.product_url.rstrip('/')
        slug = self._slugify(self.company)
        direct = f"https://www.g2.com/products/{slug}/reviews"

        attempts = [
            ("direct_fast", direct),
            ("human_path", None),
            ("direct_retry", direct),
        ]

        for name, url in attempts:
            try:
                await self._harden_page(page)
                if name.startswith("direct"):
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(random.randint(900,1700))
                    await self._human_mouse(page, moves=3)
                    if await self._is_blocked(page):
                        logging.warning(f"G2 blocked on {name}")
                        await page.wait_for_timeout(random.randint(850,1400))
                        continue
                    if "/reviews" in page.url:
                        logging.info(f"G2 success via {name}: {page.url}")
                        return page.url.split('?')[0].rstrip('/')
                else:
                    logging.info("G2 attempting human_path flow")
                    dest = await self._user_flow_search_to_reviews(page, slug)
                    if dest:
                        logging.info("G2 success via human_path")
                        return dest
                    else:
                        logging.warning("G2 human_path failed or blocked")
                await page.wait_for_timeout(random.randint(1200,2100))
            except Exception:
                continue

        # Fallback: search manual
        from urllib.parse import quote
        q = quote(self.company)
        search_url = self.BASE_SEARCH.format(q=q)
        log.info(f"Searching G2: {search_url}")
        try:
            await self._harden_page(page)
            await page.goto(search_url, wait_until="domcontentloaded")
            await self._human_mouse(page, moves=2)
        except Exception:
            return None

        try:
            await page.wait_for_timeout(600)
        except Exception:
            pass

        possible_selectors = [
            f"a[href*='/products/{slug}/reviews']",
            f"a[href*='/products/{slug}?']",
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
                href = await el.get_attribute("href")  # FIX: await
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.g2.com" + href
                if "/products/" in href and "/reviews" not in href:
                    href = href.rstrip("/") + "/reviews"
                candidate = href
                break
            except Exception:
                continue
        if candidate:
            log.info("Found product page via search: %s", candidate)
        else:
            log.warning("G2: Could not auto-find product page; provide product_url.")
        return candidate

    async def extract_reviews_from_page(self, page) -> List[Dict]:
        reviews: List[Dict] = []

        # Wait for reviews frame/section to appear
        try:
            await page.wait_for_selector("turbo-frame#reviews-and-filters, section#reviews", timeout=10000)
        except Exception:
            await page.wait_for_timeout(800)

        # Early block detection (page may show an interstitial)
        try:
            snippet = (await page.content())[:6000].lower()
            if "access blocked" in snippet or "unusual activity" in snippet:
                return []  # Signal to higher level that the page is blocked
        except Exception:
            pass

        # Light scroll to trigger lazy loading
        try:
            await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight*0.8))")
            await page.wait_for_timeout(600)
        except Exception:
            pass

        # Candidate selectors
        candidate_selectors = [
            "turbo-frame#reviews-and-filters article",
            "turbo-frame#reviews-and-filters [data-review-id]",
            "section#reviews article",
            "article[itemtype*='Review']",
            "article",
        ]

        els = []
        for sel in candidate_selectors:
            try:
                found = await page.query_selector_all(sel)
                if found:
                    els = found
                    break
            except Exception:
                continue

        # JSON-LD fallback
        if not els:
            try:
                scripts = await page.query_selector_all("script[type='application/ld+json']")
                for s in scripts:
                    try:
                        raw = await s.inner_text()
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
                                    rv = rr.get("ratingValue")
                                    try:
                                        rating = float(rv) if rv is not None else None
                                    except Exception:
                                        pass
                                author = node.get("author")
                                reviewer = author.get("name") if isinstance(author, dict) else author
                                d = parse_date_fuzzy(dt)
                                reviews.append({
                                    "title": title,
                                    "review": body,
                                    "date": d.isoformat() if d else dt,
                                    "rating": rating,
                                    "reviewer_name": reviewer
                                })
                    except Exception:
                        continue
                if reviews:
                    return reviews
            except Exception:
                pass

        for el in els:
            try:
                text = (await el.inner_text()).strip()
            except Exception:
                text = ""

            async def first_text(selectors):
                for s in selectors:
                    try:
                        node = await el.query_selector(s)
                        if node:
                            t = (await node.inner_text()).strip()
                            if t:
                                return t
                    except Exception:
                        continue
                return None

            title = await first_text(["[data-testid='review-title']", "h3", "h2"])  # FIX: await
            dt_text = None
            try:
                t = await el.query_selector("time[datetime], time")
                if t:
                    iso = await t.get_attribute("datetime")
                    if iso:
                        dt_text = iso
                    else:
                        dt_text = (await t.inner_text()).strip()
            except Exception:
                pass

            rating = None
            try:
                r = await el.query_selector("[aria-label*='out of 5']")
                if r:
                    ar = await r.get_attribute("aria-label")
                    if ar:
                        m = re.search(r"([0-9]+(?:\.[0-9]+)?)", ar)
                        if m:
                            rating = float(m.group(1))
                if rating is None:
                    # Fallback: count stars
                    full = await el.query_selector_all("svg.icon-star")
                    half = await el.query_selector_all("svg.icon-star-half, svg.icon-star-half-empty")
                    if full or half:
                        rating = len(full) + 0.5 * len(half)
            except Exception:
                pass

            reviewer_name = await first_text(["[data-testid='reviewer-name']", "a[href*='/users/']", "span[class*='user']"])
            body = await first_text(["[data-testid='review-body']", "div.review-body", "section[aria-label*='review']", "p"]) or text

            src = None
            try:
                a = await el.query_selector("a[href*='/review/'], a[href*='#review']")
                if a:
                    href = await a.get_attribute("href")
                    if href:
                        src = href if href.startswith("http") else "https://www.g2.com" + href
            except Exception:
                pass

            reviews.append({
                "title": title,
                "review": body,
                "date": dt_text,
                "rating": rating,
                "reviewer_name": reviewer_name,
                "source_url": src
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

def parse_g2_reviews_from_html(html_text: str) -> List[Dict]:
    """
    Parse G2 reviews from a saved HTML file using JSON-LD Review objects.
    This avoids live browsing when the site shows a challenge.
    """
    reviews: List[Dict] = []
    # Find all JSON-LD script blocks
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, re.S|re.I):
        raw = m.group(1).strip()
        if not raw:
            continue
        # Unescape HTML entities
        raw = htmllib.unescape(raw)
        # Try to load as JSON; skip invalid blocks
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") != "Review":
                continue
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
            author = node.get("author")
            reviewer = author.get("name") if isinstance(author, dict) else author
            d = parse_date_fuzzy(dt)
            reviews.append({
                "title": title,
                "review": body,
                "date": d.isoformat() if d else dt,
                "rating": rating,
                "reviewer_name": reviewer
            })
    return reviews
