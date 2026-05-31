from __future__ import annotations

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, field_validator

# Valores válidos para scraper_type
ScraperType = Literal["auto", "tiendanube", "woocommerce", "shopnatural", "generic"]


# ── Stores ──────────────────────────────────────────────────────────────────

class StoreCreate(BaseModel):
    name: str
    url: str
    catalog_url: str
    country: str = "AR"
    location: Optional[str] = None
    active: bool = True
    scraper_type: ScraperType = "auto"


class StoreUpdate(BaseModel):
    """Campos actualizables de una tienda (todos opcionales)."""
    name: Optional[str] = None
    url: Optional[str] = None
    catalog_url: Optional[str] = None
    country: Optional[str] = None
    location: Optional[str] = None
    active: Optional[bool] = None
    scraper_type: Optional[ScraperType] = None


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
    scraper_type: ScraperType

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
    description: Optional[str] = None
    price: Optional[float] = None
    currency: str
    image_url: Optional[str] = None
    product_url: str
    store_id: int
    store_name: Optional[str] = None

    materials_raw: Optional[str] = None
    sizes: Optional[list[str]] = None

    category: Optional[str] = None
    subcategory: Optional[str] = None
    colors: Optional[list[str]] = None
    style_tags: Optional[list[str]] = None
    gender: Optional[str] = None

    cut: Optional[str] = None
    leg_cut:  Optional[str] = None
    rise:     Optional[str] = None
    length:   Optional[str] = None
    materials: Optional[list[str]] = None
    texture: Optional[str] = None
    thickness: Optional[str] = None
    stretch: Optional[bool] = None
    colors_secondary: Optional[list[str]] = None
    pattern: Optional[str] = None
    design_details: Optional[list[str]] = None
    neck_type: Optional[str] = None
    sleeve_type: Optional[str] = None
    hem_finish: Optional[str] = None

    condition: str = "new"
    available: bool = True
    enriched: bool = False
    ai_classified: bool = False

    variants: list[VariantRead] = []

    model_config = {"from_attributes": True}


class ProductSummary(BaseModel):
    id: int
    title: str
    price: Optional[float] = None
    currency: str = "ARS"
    image_url: Optional[str] = None
    product_url: str
    store_id: int
    store_name: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    colors: Optional[list[str]] = None
    colors_secondary: Optional[list[str]] = None
    pattern: Optional[str] = None
    gender: Optional[str] = None
    cut: Optional[str] = None
    leg_cut:  Optional[str] = None
    rise:     Optional[str] = None
    length:   Optional[str] = None
    style_tags: Optional[list[str]] = None
    texture: Optional[str] = None
    materials: Optional[list[str]] = None
    design_details: Optional[list[str]] = None
    neck_type: Optional[str] = None
    sleeve_type: Optional[str] = None
    hem_finish: Optional[str] = None
    description: Optional[str] = None
    sizes: Optional[list[str]] = None
    available: bool = True
    ai_classified: bool = False
    variants: list[VariantRead] = []

    model_config = {"from_attributes": True}


# ── Search ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = ""
    limit: int = 50
    offset: int = 0

    category: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    store_id: Optional[int] = None
    color: Optional[str] = None
    gender: Optional[str] = None
    location: Optional[str] = None

    cut: Optional[str] = None
    pattern: Optional[str] = None
    style_tag: Optional[str] = None
    neck_type: Optional[str] = None
    sleeve_type: Optional[str] = None
    stretch: Optional[bool] = None

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
    results: list[ProductSummary]


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
    errors: list[str] = []


# ── Scrape logs ───────────────────────────────────────────────────────────────

class ScrapeLogRead(BaseModel):
    id: int
    store_id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    products_found: int = 0
    products_new: int = 0
    products_updated: int = 0
    error_message: Optional[str] = None
    success: bool = True

    model_config = {"from_attributes": True}


# ── Enrich status ─────────────────────────────────────────────────────────────

class StoreEnrichStatus(BaseModel):
    store_id: int
    store_name: str
    total: int
    enriched: int
    classified: int
    pending: int
    percent: float
    last_scrape: Optional[ScrapeLogRead] = None