# API Usage Examples (Notion Doc Style)

This document mirrors typical request + response bodies you can paste into Notion for internal documentation.

---
## 1. Basic Capterra Request (Direct product_id)
**Request**
```json
POST /scrape
{
  "sources": ["capterra"],
  "company": "Smartsheet",
  "capterra_product_id": 79104,
  "limit": 5
}
```
**Response (abridged)**
```json
{
  "reviews": [
    {
      "source": "capterra",
      "product": "Smartsheet",
      "title": "Great for collaboration",
      "rating": 4.5,
      "body": "Helps our distributed team manage projects...",
      "date": "2024-08-12",
      "author": "Operations Manager",
      "source_url": "https://www.capterra.in/reviews/79104/smartsheet"
    }
  ],
  "invalid_reviews": [],
  "meta": { "source_counts": {"capterra": 5}, "blocked_sources": [], "debug": false }
}
```

---
## 2. TrustRadius Request (Slug inferred)
**Request**
```json
POST /scrape
{
  "sources": ["trustradius"],
  "company": "Smartsheet",
  "limit": 5
}
```
**Response (abridged)**
```json
{
  "reviews": [
    {
      "source": "trustradius",
      "product": "Smartsheet",
      "title": "Smartsheet streamlines workflows",
      "rating": 9,
      "body": "We replaced spreadsheets with live task boards...",
      "date": "2024-07-03",
      "author": "Project Lead",
      "source_url": "https://www.trustradius.com/products/smartsheet/reviews"
    }
  ],
  "invalid_reviews": [],
  "meta": { "source_counts": {"trustradius": 5}, "blocked_sources": [], "debug": false }
}
```

---
## 3. G2 Request (Experimental) with Debug
**Request**
```json
POST /scrape
{
  "sources": ["g2"],
  "company": "Smartsheet",
  "limit": 3,
  "debug": true
}
```
**Possible Response (blocked example)**
```json
{
  "reviews": [],
  "invalid_reviews": [],
  "meta": {
    "source_counts": {"g2": 0},
    "blocked_sources": ["g2"],
    "debug": true,
    "notes": "G2 access denied - proxy or IP rotation required"
  }
}
```
**Possible Response (success abridged)**
```json
{
  "reviews": [
    { "source": "g2", "product": "Smartsheet", "title": "Flexible and powerful", "rating": 4.2, "body": "Dashboard views are customizable...", "date": "2024-06-30" }
  ],
  "invalid_reviews": [ { "reason": "missing_rating", "raw": "..." } ],
  "meta": { "source_counts": {"g2": 3}, "blocked_sources": [], "debug": true }
}
```

---
## 4. Multi-Source Request (Capterra + TrustRadius)
**Request**
```json
POST /scrape
{
  "sources": ["capterra", "trustradius"],
  "company": "Smartsheet",
  "capterra_product_id": 79104,
  "limit": 10
}
```
**Response (abridged)**
```json
{
  "reviews": [
    { "source": "capterra", "title": "Great for collaboration", "rating": 4.5, "date": "2024-08-12" },
    { "source": "trustradius", "title": "Smartsheet streamlines workflows", "rating": 9, "date": "2024-07-03" }
  ],
  "invalid_reviews": [],
  "meta": {
    "source_counts": {"capterra": 10, "trustradius": 9},
    "blocked_sources": [],
    "debug": false
  }
}
```

---
## 5. Date-Filtered Request
**Request**
```json
POST /scrape
{
  "sources": ["capterra"],
  "company": "Smartsheet",
  "capterra_product_id": 79104,
  "start": "2024-01-01",
  "end": "2024-12-31",
  "limit": 20
}
```
**Response Note**
- The service filters parsed review dates; reviews outside range omitted or counted as invalid if date missing.

---
## 6. Debug Mode with Invalid Review Diagnostics
**Request**
```json
POST /scrape
{
  "sources": ["capterra"],
  "company": "Smartsheet",
  "capterra_product_id": 79104,
  "limit": 5,
  "debug": true
}
```
**Response (illustrative)**
```json
{
  "reviews": [ { "source": "capterra", "title": "Great for collaboration", "rating": 4.5, "date": "2024-08-12" } ],
  "invalid_reviews": [
    { "reason": "missing_title", "raw_excerpt": "Helps our distributed team..." },
    { "reason": "missing_rating", "raw_excerpt": "Custom dashboards are helpful" }
  ],
  "meta": { "source_counts": {"capterra": 5}, "blocked_sources": [], "debug": true }
}
```

---
## 7. Field Reference
| Field | Meaning |
|-------|---------|
| source | Origin platform (capterra / trustradius / g2) |
| product | Product/company name used for the scrape |
| title | Normalized review headline (may be inferred) |
| rating | Numeric rating (float or integer; TrustRadius often 1–10) |
| body | Main review text (sanitized) |
| date | ISO date (YYYY-MM-DD) parsed or inferred |
| author | Display name / role if available |
| source_url | Canonical listing URL used |
| invalid_reviews | Diagnostics for skipped items when debug=true |
| blocked_sources | List of sources blocked by anti-bot defenses |

---
## 8. When G2 Blocks
Symptoms:
- Empty reviews array, g2 in blocked_sources
Fixes:
- Add residential proxy (PLAYWRIGHT_PROXY)
- Reduce sources to only g2
- Retry with new IP after cooldown

---
## 9. Recommended Minimum Inputs by Source
| Source | Required | Recommended |
|--------|----------|-------------|
| Capterra | company + capterra_product_id | + capterra_region if not .com |
| TrustRadius | company | n/a |
| G2 | company | quality proxy |

---
## 10. Copy/Paste Snippets (Raw curl)
```bash
curl -X POST http://localhost:8000/scrape -H 'Content-Type: application/json' -d '{"sources":["capterra"],"company":"Smartsheet","capterra_product_id":79104,"limit":5}'

curl -X POST http://localhost:8000/scrape -H 'Content-Type: application/json' -d '{"sources":["trustradius"],"company":"Smartsheet","limit":5}'

curl -X POST http://localhost:8000/scrape -H 'Content-Type: application/json' -d '{"sources":["g2"],"company":"Smartsheet","limit":3,"debug":true}'
```

---
_Last updated: 2025-09-13_
