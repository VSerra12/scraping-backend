"""
Servicio de scraping que coordina:
1. Descargar productos de la tienda
2. Detectar productos nuevos vs existentes
3. Agrupar variantes de color (ej: remera emily // negro, blanco, gris)
4. Guardar productos nuevos con enriched=False
5. Actualizar precio/disponibilidad de productos existentes
6. Registrar un ScrapeLog por cada ejecución (éxito o error)
"""
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.models import Store, Product, ProductVariant, ScrapeLog
from app.models.schemas import ScrapeResponse
from app.scrapers.base_scraper import GenericScraper, ScraperError
from app.scrapers.tiendanube_scraper import TiendaNubeScraper
from app.core.config import settings

logger = logging.getLogger(__name__)

TIENDANUBE_DOMAINS = ["mitiendanube.com", "tiendanube.com"]
LOW_YIELD_THRESHOLD = 0.20  # si scraped < 20% del histórico → no marcar ausentes


def _get_scraper(store):
    url      = store.url or ""
    catalog  = store.catalog_url or ""
    combined = url + catalog
    if any(d in combined for d in TIENDANUBE_DOMAINS):
        return TiendaNubeScraper()
    if "mpage" in catalog:
        return TiendaNubeScraper()
    if any(p in catalog for p in ["/productos", "/prendas", "/catalogo", "/coleccion", "/categoria"]):
        return TiendaNubeScraper()
    if any(p in catalog for p in ["/shop", "/tienda", "/store"]):
        from app.scrapers.woocommerce_scraper import WooCommerceScraper
        return WooCommerceScraper()
    return GenericScraper()


def scrape_and_save(store: Store, db: Session) -> ScrapeResponse:
    """
    Scrapea una tienda y guarda/actualiza productos.
    Registra siempre un ScrapeLog al finalizar, sea éxito o error.

    Guarda un warning en el log y omite _mark_missing_as_unavailable()
    si los productos scrapeados son menos del 20% del total histórico,
    para evitar desactivar el catálogo entero por un scraping parcial.
    """
    scraper       = _get_scraper(store)
    errors        = []
    new_count     = 0
    updated_count = 0
    warning       = None
    started_at    = datetime.utcnow()

    logger.info(f"Iniciando scraping de tienda: {store.name}")

    # ── 1. Obtener productos del scraper ──────────────────────────────────────
    try:
        raw_products = scraper.scrape_store(
            store.catalog_url,
            max_pages=settings.MAX_PAGES_PER_STORE,
        )
    except ScraperError as e:
        error_msg = f"Error de scraping en {store.name}: {e}"
        logger.error(error_msg)
        _write_scrape_log(
            db,
            store_id=store.id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            products_found=0,
            products_new=0,
            products_updated=0,
            error_message=error_msg,
            success=False,
        )
        db.commit()
        return ScrapeResponse(
            store_id=store.id,
            store_name=store.name,
            new_products=0,
            updated_products=0,
            classified_products=0,
            errors=[error_msg],
        )

    # ── 2. Procesar cada producto ─────────────────────────────────────────────
    for raw in raw_products:
        try:
            existing = _find_existing_product(db, store.id, raw)

            if existing:
                if raw.get("price") is not None:
                    existing.price = raw["price"]
                existing.available  = True
                existing.updated_at = datetime.utcnow()
                if raw.get("color_variant"):
                    _upsert_variant(db, existing, raw)
                updated_count += 1

            else:
                color_variant = raw.get("color_variant")
                parent = None
                if color_variant:
                    parent = _find_product_by_title(db, store.id, raw["title"])

                if parent:
                    _upsert_variant(db, parent, raw)
                    logger.debug(f"Variante '{color_variant}' agregada a '{raw['title']}'")
                    updated_count += 1
                else:
                    product = Product(
                        external_id=str(raw["external_id"]) if raw.get("external_id") else "",
                        store_id=store.id,
                        title=raw["title"],
                        description=raw.get("description"),
                        price=raw.get("price"),
                        image_url=raw.get("image_url"),
                        product_url=raw["product_url"],
                        available=raw.get("available", True),
                        enriched=False,
                        ai_classified=False,
                    )
                    db.add(product)
                    try:
                        db.flush()
                        new_count += 1
                        if color_variant:
                            _upsert_variant(db, product, raw)
                    except IntegrityError as e:
                        db.rollback()
                        logger.error(f"IntegrityError en '{raw.get('title')}': {e.orig}")
                        existing = _find_existing_product(db, store.id, raw)
                        if existing and raw.get("price") is not None:
                            existing.price     = raw["price"]
                            existing.available = True
                            updated_count += 1

        except Exception as e:
            error_msg = f"Error procesando '{raw.get('title', '?')}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)
            db.rollback()
            continue

    # ── 3. Marcar ausentes — con chequeo del 20% ─────────────────────────────
    historic_count = db.query(Product).filter(
        Product.store_id == store.id,
    ).count()

    scraped_count = len(raw_products)

    if historic_count > 0 and scraped_count < historic_count * LOW_YIELD_THRESHOLD:
        warning = (
            f"Scraping bajo rendimiento: se obtuvieron {scraped_count} productos "
            f"({scraped_count / historic_count:.0%} del histórico de {historic_count}). "
            f"Se omitió _mark_missing_as_unavailable() para evitar falsos negativos."
        )
        logger.warning(f"[{store.name}] {warning}")
    else:
        _mark_missing_as_unavailable(db, store.id, raw_products)

    store.last_scraped = datetime.utcnow()

    # ── 4. Guardar log ────────────────────────────────────────────────────────
    all_messages = [m for m in [warning] + errors if m]
    _write_scrape_log(
        db,
        store_id=store.id,
        started_at=started_at,
        finished_at=datetime.utcnow(),
        products_found=scraped_count,
        products_new=new_count,
        products_updated=updated_count,
        error_message="; ".join(all_messages) if all_messages else None,
        success=len(errors) == 0,  # warning no cuenta como fallo
    )

    db.commit()

    logger.info(
        f"Tienda {store.name}: {new_count} nuevos, {updated_count} actualizados. "
        f"Errores parciales: {len(errors)}"
    )

    return ScrapeResponse(
        store_id=store.id,
        store_name=store.name,
        new_products=new_count,
        updated_products=updated_count,
        classified_products=0,
        errors=errors,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_scrape_log(
    db: Session,
    store_id: int,
    started_at: datetime,
    finished_at: datetime,
    products_found: int,
    products_new: int,
    products_updated: int,
    error_message: Optional[str],
    success: bool,
) -> None:
    """
    Inserta un ScrapeLog en la sesión activa.
    No hace commit propio — se commitea junto con el resto.
    Silencia excepciones para no romper el flujo principal.
    """
    try:
        db.add(ScrapeLog(
            store_id=store_id,
            started_at=started_at,
            finished_at=finished_at,
            products_found=products_found,
            products_new=products_new,
            products_updated=products_updated,
            error_message=error_message,
            success=success,
        ))
    except Exception as e:
        logger.error(f"No se pudo guardar ScrapeLog para store_id={store_id}: {e}")


def _find_existing_product(db: Session, store_id: int, raw: dict) -> Optional[Product]:
    if raw.get("external_id"):
        product = db.query(Product).filter(
            Product.store_id  == store_id,
            Product.external_id == str(raw["external_id"]),
        ).first()
        if product:
            return product
    return db.query(Product).filter(
        Product.store_id  == store_id,
        Product.product_url == raw["product_url"],
    ).first()


def _find_product_by_title(db: Session, store_id: int, title: str) -> Optional[Product]:
    from sqlalchemy import func
    return db.query(Product).filter(
        Product.store_id == store_id,
        func.lower(Product.title) == title.lower(),
    ).first()


def _upsert_variant(db: Session, product: Product, raw: dict):
    color  = raw.get("color_variant")
    ext_id = str(raw["external_id"]) if raw.get("external_id") else None

    existing_variant = None
    if ext_id:
        existing_variant = db.query(ProductVariant).filter(
            ProductVariant.product_id  == product.id,
            ProductVariant.external_id == ext_id,
        ).first()
    if not existing_variant and color:
        existing_variant = db.query(ProductVariant).filter(
            ProductVariant.product_id == product.id,
            ProductVariant.color      == color,
        ).first()

    if existing_variant:
        existing_variant.available = True
        if raw.get("image_url"):
            existing_variant.image_url = raw["image_url"]
    else:
        db.add(ProductVariant(
            product_id=product.id,
            color=color,
            image_url=raw.get("image_url"),
            product_url=raw.get("product_url"),
            external_id=ext_id,
            available=True,
        ))


def _mark_missing_as_unavailable(db: Session, store_id: int, raw_products: list[dict]):
    scraped_urls    = {p["product_url"]      for p in raw_products if p.get("product_url")}
    scraped_ext_ids = {str(p["external_id"]) for p in raw_products if p.get("external_id")}

    for product in db.query(Product).filter(
        Product.store_id  == store_id,
        Product.available == True,  # noqa: E712
    ).all():
        still_available = (
            product.product_url in scraped_urls or
            (product.external_id and product.external_id in scraped_ext_ids)
        )
        if not still_available:
            product.available  = False
            product.updated_at = datetime.utcnow()