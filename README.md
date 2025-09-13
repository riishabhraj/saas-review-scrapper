# SaaS Review Scraper (FastAPI Service)

Scrape SaaS product reviews from Capterra, TrustRadius, and (experimental) G2 through a single FastAPI endpoint with resilient anti-block tactics, layered extraction heuristics, and minimal required inputs.

## Current Status
Stable: Capterra, TrustRadius
Experimental / IP-sensitive: G2 (may still block without clean residential / rotating proxy)

## Key Features
- Single POST /scrape endpoint orchestrating multiple platforms
- Direct canonical URL strategies to reduce search exposure
- Layered fallback: direct slug/id → on-site search → heuristic inference
- Anti-block hardening: stealth JS patches, randomized viewport & user-agents, human-like typing, scrolling & mouse travel
- Robust review parsing with selector layering + JSON-LD + text inference (titles, dates, ratings)
- Region support for Capterra (.com / .in)
- Optional proxy via environment variables

## Install
```powershell
pip install -r requirements.txt
python -m playwright install
```

## Run the API
```powershell
uvicorn src.api:app --reload --port 8000
```
Then POST to http://localhost:8000/scrape

## Environment Variables
Optional (improve block avoidance):
- PLAYWRIGHT_PROXY / HTTP_PROXY / HTTPS_PROXY: Proxy URL (http(s)://user:pass@host:port)
- CAPTERRA_REGION: Override region (default: com). Accepts: com, in

## Request Payload
```json
{
  "sources": ["capterra", "trustradius", "g2"],
  "company": "Smartsheet",
  "limit": 20,
  "capterra_product_id": 79104,
  "capterra_region": "in",
  "start": "2024-01-01",
  "end": "2024-12-31",
  "debug": false
}
```
Notes:
- company: Human product/company name (used for slug & search)
- capterra_product_id: Strongly recommended for Capterra (ensures direct path)
- capterra_region: Optional (.com if omitted)
- limit: Per-source soft cap (first page may return fewer/more depending on platform pagination)
- start / end: Optional inclusive ISO date filters (reviews outside range dropped or counted invalid if debug)
- debug: If true, returns invalid_details (why reviews were skipped)

Extended examples: see `NOTION_EXAMPLES.md`.

## Minimal Payloads
Source-specific minimal viable examples:
```jsonc
// Capterra (needs product_id for reliability)
{ "sources": ["capterra"], "company": "Smartsheet", "capterra_product_id": 79104 }

// TrustRadius (slug derived from company)
{ "sources": ["trustradius"], "company": "Smartsheet" }

// G2 (experimental; slug derived; may block without proxy)
{ "sources": ["g2"], "company": "Smartsheet" }
```

## Example PowerShell curl (Invoke-RestMethod)
```powershell
$payload = @{ 
  sources = @('capterra','trustradius'); 
  company = 'Smartsheet'; 
  capterra_product_id = 79104; 
  limit = 15 
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/scrape -Body $payload -ContentType 'application/json'
```

## Plain curl Examples
```powershell
# Capterra + TrustRadius
curl -X POST http://localhost:8000/scrape -H "Content-Type: application/json" -d '{"sources":["capterra","trustradius"],"company":"Smartsheet","capterra_product_id":79104,"limit":20}'

# G2 only (may block)
curl -X POST http://localhost:8000/scrape -H "Content-Type: application/json" -d '{"sources":["g2"],"company":"Smartsheet","limit":10}'
```

## Response Shape (abridged)
```jsonc
{
  "reviews": [
    {
      "source": "capterra",
      "product": "Smartsheet",
      "title": "Great for collaboration",
      "rating": 4.5,
      "body": "...",
      "date": "2024-08-12",
      "author": "Operations Manager",
      "source_url": "https://www.capterra.in/reviews/79104/smartsheet"
    }
  ],
  "invalid_reviews": [],
  "meta": {
    "source_counts": {"capterra": 20, "trustradius": 18},
    "blocked_sources": [],
    "debug": false
  }
}
```

## How Each Source Works
### Capterra
1. If capterra_product_id provided → constructs direct region URL (e.g. https://www.capterra.in/reviews/79104/smartsheet)
2. Else: search → rank candidates → open product reviews
3. Extract cards via layered selectors; rating fallback parses aria-label or star svg count
4. Date normalized via fuzzy parser

### TrustRadius
1. Derive slug (lowercase, hyphen, strip punctuation) → try https://www.trustradius.com/products/<slug>/reviews
2. If 404 or mismatch → on-site search then pick top match
3. Parse via JSON-LD first; fallback to heuristic selectors
4. Missing fields (title/date/rating) inferred from raw block text patterns

### G2 (Experimental)
Attempt ladder:
1. Direct slug reviews URL
2. Warm-up human flow: homepage → typed search → product page → reviews
3. Retry direct
4. Fallback search results parsing
Block detection (captcha / access denied phrases) aborts early. Success highly dependent on proxy / IP reputation.

## Anti-Block Techniques Implemented
- navigator.webdriver patch & plugin / language spoofing
- Random user-agent pool & viewport per context
- Human-like: incremental typing, realistic delays, scrolling, mouse pathing
- Staggered waits for network idle & targeted selectors
- Phrase-based block detection (Access Denied, verify you are a human, etc.)

## Limitations
- G2 may still block despite tactics (need rotating residential or high-quality proxy)
- Pagination currently limited (focus on first page / initial batch)
- Pros/Cons segmentation not yet reliably extracted
- Some TrustRadius dates inferred may lack exact day precision

## Troubleshooting
Issue: Empty reviews
Fix: Provide capterra_product_id; verify proxy; enable debug flag.

Issue: Titles null (TrustRadius)
Fix: Already mitigated via inference; update code if structure changes again.

Issue: G2 blocked
Fix: Add high-reputation proxy (PLAYWRIGHT_PROXY), lower concurrency (single source), retry with fresh IP.

Issue: Wrong product page (Capterra)
Fix: Supply product_id or refine company name.

## Future Improvements
- Cookie/storage_state reuse to build trust over session reuse
- Deeper pagination (multi-page crawl with dedupe)
- Structured pros/cons extraction & sentiment
- Centralized/typed logging + HTML snapshot on block
- Retry ladder customization via request parameters

## Extending to New Sources
1. Create a new scraper inheriting AsyncBaseScraper
2. Implement: find_product_page(), extract_reviews_from_page(), optional go_to_next_page()
3. Add source key wiring in api router
4. Layer selectors → JSON-LD → heuristic inference

## Legal / Ethical
Use responsibly. Review each platform's Terms of Service. Provide caching & rate limiting if scaling. This project is for research & educational use.

## Quick Verification Checklist
- Run uvicorn and test one source at a time
- Use debug=true if results look sparse
- Add proxy before testing G2
- Confirm capterra_product_id for stable Capterra access

## Sample Debug Request
```powershell
curl -X POST http://localhost:8000/scrape -H "Content-Type: application/json" -d '{"sources":["capterra"],"company":"Smartsheet","capterra_product_id":79104,"debug":true,"limit":5}'
```

---
Maintainer Notes: README reflects current anti-block strategies and extraction logic as of latest patch set.
