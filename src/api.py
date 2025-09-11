# src/api.py
from fastapi import FastAPI, HTTPException
import asyncio, sys, inspect
from pydantic import BaseModel
from datetime import date as Date
from typing import Optional, List
from src.scrapers import G2Scraper, CapterraScraper, TrustRadiusScraper
from src.models import Review, ScrapeResult
from src.utils import iso_now

SCRAPER_MAP = {
    "g2": G2Scraper,
    "capterra": CapterraScraper,
    "trustradius": TrustRadiusScraper,
}

if sys.platform.startswith("win"):
    # Keep default Proactor loop (needed for subprocess support used by Playwright).
    # Only downgrade to Selector on 3.13+ if explicitly necessary (not the case now with async usage).
    import platform
    py_ver = tuple(int(x) for x in platform.python_version().split(".")[:2])
    if py_ver >= (3, 13):
        # (Optional) could force selector for legacy sync code; currently avoided.
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

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "async", "sources": list(SCRAPER_MAP.keys())}

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    if req.source not in SCRAPER_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported source {req.source}")
    Scraper = SCRAPER_MAP[req.source]
    scraper = Scraper(
        company=req.company,
        start_date=req.start,
        end_date=req.end,
        product_url=req.product_url,
        headless=req.headless,
    )
    import time, traceback
    started = time.time()
    try:
        result = scraper.scrape()
        # Support both async and legacy sync implementations
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
