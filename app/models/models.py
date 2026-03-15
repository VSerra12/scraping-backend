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
    materials_raw = Column(String(500), nullable=True)  # texto crudo de la página, ej: "NEO MORLEY BASTÓN FINO"
    sizes = Column(JSON, default=list)                  # ej: ["S", "M", "L", "XL"]

    # Clasificación por IA — campos base
    category = Column(String(100), nullable=True, index=True)
    subcategory = Column(String(200), nullable=True)
    colors = Column(JSON, default=list)              # colores principales: ["negro", "blanco"]
    style_tags = Column(JSON, default=list)          # ["urbano", "casual"]
    gender = Column(String(20), nullable=True)       # hombre | mujer | unisex

    # Clasificación por IA — campos expandidos
    cut = Column(String(50), nullable=True)
    leg_cut  = Column(String(50), nullable=True)  # skinny | slim | straight | wide_leg | etc.
    rise     = Column(String(50), nullable=True)  # low_rise | mid_rise | high_rise
    length   = Column(String(50), nullable=True)  # cropped | corto | midi | tobillero | largo# slim_fit | regular_fit | oversize | recto | a_line | cropped
    materials = Column(JSON, default=list)           # ["algodon", "elastano"] — normalizado por IA
    texture = Column(String(50), nullable=True)      # suave | rugosa | rigida | elastica
    thickness = Column(String(50), nullable=True)    # liviano | medio | grueso
    stretch = Column(Boolean, nullable=True)         # True si tiene elasticidad
    colors_secondary = Column(JSON, default=list)    # colores secundarios o de detalles
    pattern = Column(String(50), nullable=True)      # liso | rayado | floral | cuadros | animal_print | etc.
    design_details = Column(JSON, default=list)      # ["botones", "capucha", "bolsillos"]
    neck_type = Column(String(50), nullable=True)    # redondo | v | alto | camisa | bote | halter
    sleeve_type = Column(String(50), nullable=True)  # corta | larga | tres_cuartos | sin_mangas | globo | raglan
    hem_finish = Column(String(50), nullable=True)   # dobladillo_simple | elastizado | ribbed | raw_hem

    # Estado
    condition = Column(String(20), default="new")
    available = Column(Boolean, default=True)
    ai_classified = Column(Boolean, default=False)
    enriched = Column(Boolean, default=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    store = relationship("Store", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "external_id", name="products_store_id_external_id_key"),
        Index("ix_products_category_gender", "category", "gender"),
        Index("ix_products_store_available", "store_id", "available"),
        Index("ix_products_enriched", "enriched", "ai_classified"),
        Index("ix_products_cut", "cut"),
        Index("ix_products_pattern", "pattern"),
    )

    def __repr__(self):
        return f"<Product(title={self.title[:40]}, category={self.category})>"


class ProductVariant(Base):
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