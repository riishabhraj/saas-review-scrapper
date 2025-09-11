# src/cli.py
import asyncio
import logging
from datetime import datetime
from typing import Optional, List

import typer

from src.scrapers.g2_scraper import G2Scraper
from src.scrapers.capterra_scraper import CapterraScraper
from src.scrapers.trustradius_scraper import TrustRadiusScraper
from src.output import write_result
from src.utils import iso_now
from src.models import ScrapeResult, Review

app = typer.Typer()
logging.basicConfig(level=logging.INFO)

SCRAPER_MAP = {
    "g2": G2Scraper,
    "capterra": CapterraScraper,
    "trustradius": TrustRadiusScraper,
}

@app.command()
def scrape(
    company: str = typer.Option(..., help="Company or product name"),
    start: str = typer.Option(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., help="End date YYYY-MM-DD"),
    source: str = typer.Option("g2", help="g2 | capterra | trustradius"),
    product_url: Optional[str] = typer.Option(None, help="Product reviews URL to skip search"),
    headless: bool = typer.Option(True, help="Run browser headless (use --no-headless to disable)"),
    limit: Optional[int] = typer.Option(None, help="Max reviews to keep (debug)"),
    verbose: bool = typer.Option(False, help="Print first validated review"),
):
    try:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
    except Exception:
        typer.echo("Invalid date format. Use YYYY-MM-DD.")
        raise typer.Exit(code=1)
    if source not in SCRAPER_MAP:
        typer.echo(f"Unknown source: {source}. Supported: {list(SCRAPER_MAP.keys())}")
        raise typer.Exit(code=1)

    Scraper = SCRAPER_MAP[source]
    scraper = Scraper(
        company=company,
        start_date=start_date,
        end_date=end_date,
        product_url=product_url,
        headless=headless
    )
    typer.echo(f"Scraping {company} from {source} between {start} and {end} ...")
    try:
        maybe_coro = scraper.scrape()
        if asyncio.iscoroutine(maybe_coro):
            reviews = asyncio.run(maybe_coro)
        else:
            reviews = maybe_coro
    except Exception as e:
        typer.echo(f"Error during scraping: {e}")
        raise typer.Exit(code=2)

    if limit is not None:
        reviews = reviews[:limit]

    # Validation layer
    try:
        review_models = []
        for r in reviews:
            try:
                review_models.append(Review(**r))
            except Exception as er:
                logging.warning(f"Skipping invalid review: {er}; data={r}")
        result_model = ScrapeResult(
            company=company,
            source=source,
            start_date=start_date,
            end_date=end_date,
            scraped_at=iso_now(),
            reviews=review_models,
            meta={
                "reviews_found": len(review_models),
                "raw_reviews_count": len(reviews),
            },
        )
        result = result_model.model_dump()
    except Exception as e:
        typer.echo(f"Validation error: {e}")
        raise typer.Exit(code=3)

    outpath = write_result(result, company, source, start, end)
    if verbose and result.get("reviews"):
        first = result["reviews"][0]
        logging.info(f"First review: {first}")
    typer.echo(f"Wrote {len(result['reviews'])} validated reviews to {outpath}")

if __name__ == "__main__":
    app()
