# src/models.py
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import date as Date


class Review(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    title: Optional[str] = None
    review: Optional[str] = None
    date: Optional[Date] = None  # unified field name used by scrapers
    rating: Optional[float] = Field(None, ge=0, le=5)
    reviewer_name: Optional[str] = None
    reviewer_role: Optional[str] = None
    reviewer_company: Optional[str] = None
    location: Optional[str] = None
    source_url: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class ScrapeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: str
    source: str
    start_date: Date
    end_date: Date
    scraped_at: str
    reviews: List[Review] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
