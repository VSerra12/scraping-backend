from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    url = Column(String(1000), unique=True, nullable=False)
    catalog_url = Column(String(1000), nullable=False)
    country = Column(String(10), default="AR")
    location = Column(String(255), nullable=True)
    active = Column(Boolean, default=True)
    last_scraped = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship("Product", back_populates="store", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Store(name={self.name})>"


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String(255), nullable=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)

    # Info básica
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    currency = Column(String(10), default="ARS")
    image_url = Column(String(1000), nullable=True)
    product_url = Column(String(1000), nullable=False)

    # Info enriquecida (extraída de la página individual del producto)
    materials = Column(String(500), nullable=True)   # ej: "NEO MORLEY BASTÓN FINO"
    sizes = Column(JSON, default=list)               # ej: ["S", "M", "L", "XL"]

    # Clasificación por IA
    category = Column(String(100), nullable=True, index=True)
    subcategory = Column(String(100), nullable=True)
    colors = Column(JSON, default=list)              # ["negro", "blanco"]
    style_tags = Column(JSON, default=list)          # ["urbano", "casual"]
    gender = Column(String(20), nullable=True)       # hombre/mujer/unisex

    # Estado
    condition = Column(String(20), default="new")
    available = Column(Boolean, default=True)
    ai_classified = Column(Boolean, default=False)
    enriched = Column(Boolean, default=False, index=True)  # si ya visitó su página individual

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    store = relationship("Store", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "external_id", name="products_store_id_external_id_key"),
        Index("ix_products_category_gender", "category", "gender"),
        Index("ix_products_store_available", "store_id", "available"),
        Index("ix_products_enriched", "enriched", "ai_classified"),
    )

    def __repr__(self):
        return f"<Product(title={self.title[:40]}, category={self.category})>"


class ProductVariant(Base):
    """
    Variantes de color de un producto (ej: remera emily // negro, blanco, gris).
    El producto principal guarda la primera variante; las demás se guardan aquí.
    """
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    color = Column(String(100), nullable=True)
    image_url = Column(String(1000), nullable=True)
    product_url = Column(String(1000), nullable=True)
    external_id = Column(String(255), nullable=True)
    available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="variants")

    def __repr__(self):
        return f"<ProductVariant(color={self.color})>"


class SearchLog(Base):
    __tablename__ = "search_logs"

    id = Column(Integer, primary_key=True, index=True)
    query = Column(String(500), nullable=True)
    results_count = Column(Integer, default=0)
    filters_used = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=datetime.utcnow)