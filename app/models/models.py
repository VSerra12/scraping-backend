from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class Store(Base):
    __tablename__ = "stores"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(100),  unique=True, nullable=False)
    url          = Column(String(255),  unique=True, nullable=False)
    catalog_url  = Column(String(255),  nullable=False)
    country      = Column(String(10),   nullable=False, default="AR")
    active       = Column(Boolean,      nullable=False, default=True)
    last_scraped = Column(DateTime,     nullable=True)
    created_at   = Column(DateTime,     nullable=False, default=datetime.utcnow)
    location     = Column(String(255),  nullable=True)

    # Tipo de scraper a usar para esta tienda.
    # Valores: 'auto' | 'tiendanube' | 'woocommerce' | 'shopnatural' | 'generic'
    # 'auto' = detección automática por dominio/URL (comportamiento original).
    scraper_type = Column(String(20), nullable=False, default="auto")

    products    = relationship("Product",   back_populates="store", cascade="all, delete-orphan")
    scrape_logs = relationship("ScrapeLog", back_populates="store", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Store(name={self.name}, scraper_type={self.scraper_type})>"


class Product(Base):
    __tablename__ = "products"

    id          = Column(Integer,     primary_key=True, index=True)
    external_id = Column(String(100), nullable=False)
    store_id    = Column(Integer,     ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)

    title       = Column(String(500),  nullable=False)
    description = Column(Text,         nullable=True)
    price       = Column(Float,        nullable=True,  default=0.0)
    currency    = Column(String(10),   nullable=False, default="ARS")
    image_url   = Column(String(1000), nullable=True)
    product_url = Column(String(1000), nullable=False)

    category    = Column(String(100), nullable=True)
    subcategory = Column(String(100), nullable=True)
    colors      = Column(JSON,        nullable=True)
    style_tags  = Column(JSON,        nullable=True)
    gender      = Column(String(20),  nullable=True)
    condition   = Column(String(20),  nullable=False, default="new")

    available     = Column(Boolean, nullable=False, default=True)
    ai_classified = Column(Boolean, nullable=False, default=False)
    enriched      = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    materials_raw = Column(String(500), nullable=True)
    sizes         = Column(JSON,        nullable=True, default=list)

    cut              = Column(String(50), nullable=True)
    materials        = Column(JSON,       nullable=True, default=list)
    texture          = Column(String(50), nullable=True)
    thickness        = Column(String(50), nullable=True)
    stretch          = Column(Boolean,    nullable=True)
    colors_secondary = Column(JSON,       nullable=True, default=list)
    pattern          = Column(String(50), nullable=True)
    design_details   = Column(JSON,       nullable=True, default=list)
    neck_type        = Column(String(50), nullable=True)
    sleeve_type      = Column(String(50), nullable=True)
    hem_finish       = Column(String(50), nullable=True)
    leg_cut          = Column(String(50), nullable=True)
    rise             = Column(String(50), nullable=True)
    length           = Column(String(50), nullable=True)

    store    = relationship("Store",          back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "external_id", name="products_store_id_external_id_key"),
    )

    def __repr__(self):
        return f"<Product(title={self.title[:40]}, category={self.category})>"


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id          = Column(Integer,      primary_key=True, index=True)
    product_id  = Column(Integer,      ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    color       = Column(String(100),  nullable=True)
    image_url   = Column(String(1000), nullable=True)
    product_url = Column(String(1000), nullable=True)
    external_id = Column(String(255),  nullable=True)
    available   = Column(Boolean,      nullable=True, default=True)
    created_at  = Column(DateTime,     nullable=True, default=datetime.utcnow)

    product = relationship("Product", back_populates="variants")

    def __repr__(self):
        return f"<ProductVariant(color={self.color})>"


class SearchLog(Base):
    __tablename__ = "search_logs"

    id            = Column(Integer,     primary_key=True, index=True)
    query         = Column(String(500), nullable=False)
    results_count = Column(Integer,     nullable=False, default=0)
    filters       = Column(Text,        nullable=True)
    timestamp     = Column(DateTime,    nullable=False, default=datetime.utcnow)
    filters_used  = Column(JSON,        nullable=True)


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id               = Column(Integer,  primary_key=True, index=True)
    store_id         = Column(Integer,  ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    started_at       = Column(DateTime, nullable=False)
    finished_at      = Column(DateTime, nullable=True)
    products_found   = Column(Integer,  nullable=True, default=0)
    products_new     = Column(Integer,  nullable=True, default=0)
    products_updated = Column(Integer,  nullable=True, default=0)
    error_message    = Column(Text,     nullable=True)
    success          = Column(Boolean,  nullable=True, default=True)

    store = relationship("Store", back_populates="scrape_logs")

    def __repr__(self):
        return f"<ScrapeLog(store_id={self.store_id}, success={self.success}, started_at={self.started_at})>"