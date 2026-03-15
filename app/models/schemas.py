from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, field_validator


# ── Stores ──────────────────────────────────────────────────────────────────

class StoreCreate(BaseModel):
    name: str
    url: str
    catalog_url: str
    country: str = "AR"
    location: Optional[str] = None
    active: bool = True


class StoreRead(BaseModel):
    id: int
    name: str
    url: str
    catalog_url: str
    country: str
    location: Optional[str] = None
    active: bool
    last_scraped: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Products ─────────────────────────────────────────────────────────────────

class VariantRead(BaseModel):
    id: int
    color: Optional[str] = None
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    available: bool = True

    model_config = {"from_attributes": True}


class ProductRead(BaseModel):
    id: int
    title: str
    description: Optional[str]
    price: Optional[float]
    currency: str
    image_url: Optional[str]
    product_url: str
    category: Optional[str]
    subcategory: Optional[str]
    colors: Optional[List[str]]
    style_tags: Optional[List[str]]
    gender: Optional[str]
    materials: Optional[str] = None
    sizes: Optional[List[str]] = None
    enriched: bool = False
    ai_classified: bool = False
    available: bool
    store_id: int
    store_name: Optional[str] = None
    variants: List[VariantRead] = []

    model_config = {"from_attributes": True}


# ── Search ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = ""
    limit: int = 50
    category: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    store_id: Optional[int] = None
    color: Optional[str] = None
    gender: Optional[str] = None
    location: Optional[str] = None
    offset: int = 0

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, v):
        return max(1, min(v, 50))

    @field_validator("query")
    @classmethod
    def strip_query(cls, v):
        return v.strip()


class SearchResponse(BaseModel):
    query: str
    total: int
    results: List[ProductRead]


# ── Stats ────────────────────────────────────────────────────────────────────

class StatsResponse(BaseModel):
    total_stores: int
    active_stores: int
    total_products: int
    classified_products: int
    enriched_products: int = 0
    pending_enrichment: int = 0
    total_searches: int


# ── Scraping ─────────────────────────────────────────────────────────────────

class ScrapeResponse(BaseModel):
    store_id: int
    store_name: str
    new_products: int
    updated_products: int
    classified_products: int
    errors: List[str] = []