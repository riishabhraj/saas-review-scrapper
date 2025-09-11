# src/api.py
from fastapi import FastAPI, HTTPException
import asyncio, sys, inspect
from pydantic import BaseModel
from datetime import date as Date
from typing import Optional, List
from src.scrapers import G2Scraper, CapterraScraper, TrustRadiusScraper
from src.scrapers.g2_scraper import fetch_g2_reviews_api
from src.scrapers.capterra_scraper import fetch_capterra_reviews_api, discover_capterra_product_id
from src.scrapers.trustradius_scraper import fetch_trustradius_via_apify
from src.models import Review, ScrapeResult
from src.utils import iso_now, parse_date_fuzzy

SCRAPER_MAP = {
    "g2": G2Scraper,
    "capterra": CapterraScraper,
    "trustradius": TrustRadiusScraper,
}

if sys.platform.startswith("win"):
    # Use SelectorEventLoopPolicy on Windows so asyncio subprocess works with Playwright.
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
    except Exception:
        pass

app = FastAPI(title="SaaS Review Scraper API", version="0.1.0")

class ScrapeRequest(BaseModel):
    company: str
    start: Date
    end: Date
    source: str = "g2"
    product_url: Optional[str] = None
    headless: bool = True
    limit: Optional[int] = None
    debug: bool = False
    # API-first flags/params
    use_api: bool = False
    g2_token: Optional[str] = None
    g2_product_uuid: Optional[str] = None
    capterra_product_id: Optional[str] = None
    tr_apify_token: Optional[str] = None

@app.get("/health")
async def health():
    import sys as _sys, platform as _platform, asyncio as _asyncio
    return {
        "status": "ok",
        "mode": "async",
        "sources": list(SCRAPER_MAP.keys()),
        "python_version": _platform.python_version(),
        "executable": _sys.executable,
        "loop_policy": type(_asyncio.get_event_loop_policy()).__name__,
        "platform": _platform.platform(),
    }

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    if req.source not in SCRAPER_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported source {req.source}")
    import time, traceback
    started = time.time()
    try:
        # API-first branch
        if req.use_api:
            if req.source == "g2":
                if not req.g2_token:
                    raise HTTPException(status_code=400, detail="Missing g2_token for API mode")
                product_uuid = req.g2_product_uuid
                if not product_uuid:
                    # allow passing product_url with UUID at the end
                    if req.product_url and "/products/" in req.product_url:
                        product_uuid = req.product_url.rstrip("/").split("/")[-1]
                if not product_uuid:
                    raise HTTPException(status_code=400, detail="Missing g2_product_uuid for API mode")
                raw_reviews = fetch_g2_reviews_api(
                    product_uuid=product_uuid,
                    token=req.g2_token,
                    start=req.start,
                    end=req.end,
                    limit=req.limit,
                    debug=req.debug,
                )
            elif req.source == "capterra":
                pid = req.capterra_product_id
                if not pid:
                    if req.product_url:
                        pid = discover_capterra_product_id(req.product_url)
                if not pid:
                    raise HTTPException(status_code=400, detail="Missing capterra_product_id or resolvable product_url for API mode")
                raw_reviews = fetch_capterra_reviews_api(
                    product_id=pid,
                    start=req.start,
                    end=req.end,
                    limit=req.limit,
                    debug=req.debug,
                )
            elif req.source == "trustradius":
                if not req.tr_apify_token:
                    raise HTTPException(status_code=400, detail="Missing tr_apify_token for API mode")
                if not req.product_url:
                    raise HTTPException(status_code=400, detail="Missing product_url for TrustRadius API mode")
                raw_reviews = fetch_trustradius_via_apify(
                    product_url=req.product_url,
                    apify_token=req.tr_apify_token,
                    start=req.start,
                    end=req.end,
                    limit=req.limit,
                    debug=req.debug,
                )
            else:
                raise HTTPException(status_code=400, detail=f"API mode not supported for source {req.source}")
        else:
            # Playwright branch
            Scraper = SCRAPER_MAP[req.source]
            scraper = Scraper(
                company=req.company,
                start_date=req.start,
                end_date=req.end,
                product_url=req.product_url,
                headless=req.headless,
                debug=req.debug,
            )
            result = scraper.scrape()
            if inspect.iscoroutine(result):
                raw_reviews = await result
            else:
                raw_reviews = result
    except Exception as e:
        tb = traceback.format_exc()
        hint = None
        msg = str(e)
        if "Playwright subprocess creation not supported" in msg:
            hint = "Python 3.13 + Windows asyncio lacks needed subprocess impl. Use Python 3.11/3.12, WSL2, or Linux container."
        if req.debug:
            detail = f"Scrape failed: {e}\n{tb}"
            if hint:
                detail += f"\nHINT: {hint}"
            raise HTTPException(status_code=500, detail=detail)
        raise HTTPException(status_code=500, detail=f"Scrape failed: {e}{' | ' + hint if hint else ''}")

    # Date-range filter (applies to both API and Playwright modes)
    def _in_range(dval):
        if dval is None:
            return True
        if isinstance(dval, str):
            dd = parse_date_fuzzy(dval)
        else:
            dd = dval
        if not dd:
            return True
        if req.start and dd < req.start:
            return False
        if req.end and dd > req.end:
            return False
        return True

    raw_reviews = [r for r in raw_reviews if _in_range(r.get("date"))]

    if req.limit is not None:
        raw_reviews = raw_reviews[: req.limit]

    review_models: List[Review] = []
    invalid = 0
    for r in raw_reviews:
        try:
            review_models.append(Review(**r))
        except Exception:
            invalid += 1

    duration = round(time.time() - started, 3)
    result = ScrapeResult(
        company=req.company,
        source=req.source,
        start_date=req.start,
        end_date=req.end,
        scraped_at=iso_now(),
        reviews=review_models,
        meta={
            "reviews_found": len(review_models),
            "raw_reviews_count": len(raw_reviews),
            "invalid_reviews": invalid,
            "duration_sec": duration,
            "debug": req.debug,
        },
    )
    return result.model_dump()
