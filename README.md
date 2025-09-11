# SaaS Review Scraper

Scrape product reviews from multiple SaaS review platforms (initial targets: G2, Capterra; TrustRadius scaffold) and export them to structured JSON or CSV.

## Features (Initial Scope)
- Unified CLI to run one or multiple scrapers
- Pluggable scraper architecture with a common abstract base
- Typed Pydantic models for Product and Review
- Concurrency-friendly async Playwright-powered scraping (future optimization)
- Rate limiting / polite crawling helpers
- Output writers (JSON lines & CSV)

## Install
```
pip install -r requirements.txt
python -m playwright install
```

## Quick Start
```
python -m src.cli g2 --product "Notion" --url https://www.g2.com/products/notion/reviews --limit 50 --out outputs/notion_g2.jsonl
```
Multiple sources:
```
python -m src.cli multi --config scrape_config.json
```

## CLI (Planned)
| Command | Description |
|---------|-------------|
| g2 | Run G2 scraper directly |
| capterra | Run Capterra scraper directly |
| trustradius | (Scaffold) TrustRadius scraper |
| multi | Run multiple scrapers from a JSON config |

## Config File Example (multi)
```json
{
  "jobs": [
    {"site": "g2", "product": "Notion", "url": "https://www.g2.com/products/notion/reviews", "limit": 100, "out": "outputs/notion_g2.jsonl"},
    {"site": "capterra", "product": "Notion", "url": "https://www.capterra.com/p/12345/Notion/reviews/", "limit": 80, "out": "outputs/notion_capterra.jsonl"}
  ]
}
```

## Data Models
Review fields (baseline):
- id
- product_name
- source (g2|capterra|trustradius)
- author
- rating (float)
- title
- body
- pros
- cons
- likes
- dislikes
- created_at (datetime)
- collected_at (datetime UTC)
- url (review permalink)

## Output Formats
- JSON Lines: each line a serialized Review
- CSV: tabular subset of fields

## Extending
Create a new file in `src/scrapers/your_site_scraper.py` implementing `BaseScraper`.

## Roadmap
- Async concurrency and headless browser pooling
- Retry + backoff
- More platforms
- Sentiment & keyword enrichment (post-processing)

## Disclaimer
For educational/research use only. Respect each platform's Terms of Service and robots.txt. Do not overload servers.
