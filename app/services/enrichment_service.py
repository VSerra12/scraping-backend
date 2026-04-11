"""
Servicio de enriquecimiento de productos.

Flujo:
1. Busca productos con enriched=False (o todos si force=True)
2. Visita la página individual de cada producto
3. Extrae descripción completa, materiales y talles
4. Clasifica con IA usando toda la info disponible
5. Marca enriched=True y ai_classified=True

Soporta Tienda Nube y WooCommerce.
"""
import json
import logging
import time
import random
from typing import Optional
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from app.models.models import Product, Store
from app.scrapers.base_scraper import BaseScraper
from app.services.ai_classifier import classify_product

logger = logging.getLogger(__name__)


class ProductEnricher(BaseScraper):
    """Visita páginas individuales de productos y extrae info completa."""

    def enrich_product(self, product: Product, store: Store) -> dict:
        """Visita la URL del producto y extrae descripción, materiales y talles."""
        soup = self.get_page(product.product_url)
        if not soup:
            logger.warning(f"No se pudo obtener página de {product.product_url}")
            return {}

        if self._is_tiendanube(store.url, soup):
            return self._extract_tiendanube(soup)
        elif self._is_woocommerce(store.url, soup):
            return self._extract_woocommerce(soup)
        else:
            return self._extract_generic(soup)

    def _is_tiendanube(self, store_url: str, soup: BeautifulSoup) -> bool:
        return (
            "mitiendanube.com" in store_url or
            "tiendanube.com" in store_url or
            bool(soup.select_one("[data-variants]")) or
            bool(soup.select_one(".js-product-container"))
        )

    def _is_woocommerce(self, store_url: str, soup: BeautifulSoup) -> bool:
        return (
            bool(soup.select_one(".woocommerce-product-details__short-description")) or
            bool(soup.select_one(".woocommerce")) or
            bool(soup.select_one("table.variations"))
        )

    def _extract_tiendanube(self, soup: BeautifulSoup) -> dict:
        """Extrae info de página de producto de Tienda Nube."""
        description = None
        for sel in [".product-description", ".description", ".js-product-description"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if text:
                    description = text[:1000]
                    break

        sizes = []
        colors = []
        variants_el = soup.select_one("[data-variants]")
        if variants_el:
            try:
                variants = json.loads(variants_el["data-variants"])
                talle_keywords = {"s", "m", "l", "xl", "xxl", "xs", "único", "unico"}

                for v in variants:
                    for opt_key in ["option0", "option1", "option2"]:
                        opt = v.get(opt_key, "")
                        if not opt:
                            continue
                        if opt.lower() in talle_keywords or opt.lower().startswith("talle"):
                            if opt not in sizes:
                                sizes.append(opt)
                        else:
                            col = opt.lower()
                            if col not in colors:
                                colors.append(col)
            except (json.JSONDecodeError, KeyError):
                pass

        materials_raw = self._extract_materials(soup, description)

        return {
            "description": description,
            "materials_raw": materials_raw,
            "sizes": sizes,
            "colors_hint": colors,
        }

    def _extract_woocommerce(self, soup: BeautifulSoup) -> dict:
        """Extrae info de página de producto de WooCommerce."""
        description = None
        for sel in [
            ".woocommerce-product-details__short-description",
            ".product-short-description",
            ".woocommerce-Tabs-panel--description",
            "#tab-description",
        ]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if text:
                    description = text[:1000]
                    break

        sizes = []
        for sel in [
            "select[name*='talle']",
            "select[name*='size']",
            "select[name*='talla']",
            "[data-attribute_name*='talle'] option",
        ]:
            select = soup.select_one(sel)
            if select:
                sizes = [
                    o.get_text(strip=True)
                    for o in select.select("option")
                    if o.get("value") and o.get("value") != ""
                ]
                break

        colors = []
        for sel in [
            "select[name*='color']",
            "select[name*='colour']",
            "[data-attribute_name*='color'] option",
        ]:
            select = soup.select_one(sel)
            if select:
                colors = [
                    o.get_text(strip=True).lower()
                    for o in select.select("option")
                    if o.get("value") and o.get("value") != ""
                ]
                break

        materials_raw = self._extract_materials(soup, description)

        return {
            "description": description,
            "materials_raw": materials_raw,
            "sizes": sizes,
            "colors_hint": colors,
        }

    def _extract_generic(self, soup: BeautifulSoup) -> dict:
        """Extrae info genérica para otras plataformas."""
        description = None
        for sel in [
            ".product-description", ".description",
            "[class*='description']", ".product-info", "article .content",
        ]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 20:
                    description = text[:1000]
                    break

        materials_raw = self._extract_materials(soup, description)
        return {"description": description, "materials_raw": materials_raw, "sizes": [], "colors_hint": []}

    def _extract_materials(self, soup: BeautifulSoup, description: Optional[str]) -> Optional[str]:
        """Busca composición/materiales en la página o en la descripción."""
        for sel in [".product-composition", ".composition", "[class*='material']", "[class*='fabric']"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text[:300]

        fabric_keywords = [
            "tela:", "tejido:", "composición:", "composicion:", "material:",
            "confeccionado en", "100%", "polyester", "algodón", "cotton",
            "morley", "jersey", "lycra", "spandex", "lino", "seda",
            "lanilla", "gabardina", "denim", "microfibra", "modal", "viscosa",
        ]
        if description:
            for kw in fabric_keywords:
                if kw in description.lower():
                    for sentence in description.replace("\n", ". ").split("."):
                        if kw in sentence.lower():
                            return sentence.strip()[:300]
        return None


def run_enrichment_job(
    db: Session,
    batch_size: int = 20,
    store_id: int = None,
    force: bool = False,
) -> dict:
    """
    Job de enriquecimiento.
    Procesa hasta batch_size productos pendientes por vez.

    Args:
        db: sesión de base de datos
        batch_size: máximo de productos a procesar
        store_id: opcional, filtra por tienda específica
        force: si True, re-clasifica productos ya enriquecidos (ignora enriched=True)
    """
    query = db.query(Product).filter(Product.available == True)  # noqa: E712

    if not force:
        query = query.filter(Product.enriched == False)  # noqa: E712

    if store_id:
        query = query.filter(Product.store_id == store_id)

    pending = query.limit(batch_size).all()

    if not pending:
        mode = "forzado" if force else "normal"
        logger.info(f"Enriquecimiento ({mode}): no hay productos pendientes.")
        return {"enriched": 0, "failed": 0}

    mode_label = "re-clasificación forzada" if force else "enriquecimiento"
    logger.info(f"{mode_label}: procesando {len(pending)} productos...")

    enricher = ProductEnricher()
    enriched_count = 0
    failed_count = 0

    store_ids = {p.store_id for p in pending}
    stores = {s.id: s for s in db.query(Store).filter(Store.id.in_(store_ids)).all()}

    for product in pending:
        store = stores.get(product.store_id)
        if not store:
            continue

        try:
            # En modo force, si ya tiene descripción guardada la reutilizamos
            # y solo re-visitamos la página si no tiene descripción
            if force and product.description:
                enriched_data = {
                    "description": product.description,
                    "materials_raw": product.materials_raw,
                    "sizes": product.sizes or [],
                    "colors_hint": [],
                }
            else:
                enriched_data = enricher.enrich_product(product, store)

            if enriched_data.get("description"):
                product.description = enriched_data["description"]
            if enriched_data.get("materials_raw"):
                product.materials_raw = enriched_data["materials_raw"]
            if enriched_data.get("sizes"):
                product.sizes = enriched_data["sizes"]

            colors_hint = enriched_data.get("colors_hint", [])

            classification = classify_product(
                title=product.title,
                description=product.description,
                image_url=product.image_url,
                store_name=store.name,
                colors_hint=colors_hint,
            )

            product.category         = classification["category"]
            product.subcategory      = classification["subcategory"]
            product.colors           = classification["colors"]
            product.style_tags       = classification["style_tags"]
            product.gender           = classification["gender"]
            product.condition        = classification.get("condition", "new")
            product.cut              = classification.get("cut")
            product.leg_cut          = classification.get("leg_cut")
            product.rise             = classification.get("rise")
            product.length           = classification.get("length")
            product.materials        = classification.get("materials", [])
            product.texture          = classification.get("texture")
            product.thickness        = classification.get("thickness")
            product.stretch          = classification.get("stretch")
            product.colors_secondary = classification.get("colors_secondary", [])
            product.pattern          = classification.get("pattern")
            product.design_details   = classification.get("design_details", [])
            product.neck_type        = classification.get("neck_type")
            product.sleeve_type      = classification.get("sleeve_type")
            product.hem_finish       = classification.get("hem_finish")
            product.ai_classified    = True
            product.enriched         = True

            db.flush()
            enriched_count += 1
            logger.info(
                f"  ✓ {product.title[:40]} → {product.category} / "
                f"{product.cut} / {product.colors}"
            )

        except Exception as e:
            logger.error(f"  ✗ Error en '{product.title[:40]}': {e}")
            product.enriched = True
            failed_count += 1
            try:
                db.flush()
            except Exception:
                db.rollback()
            continue

        time.sleep(random.uniform(1.0, 2.0))

    db.commit()
    logger.info(f"Enriquecimiento completo: {enriched_count} OK, {failed_count} fallidos")
    return {"enriched": enriched_count, "failed": failed_count}


def reset_enrichment(db: Session, store_id: int = None) -> dict:
    """
    Resetea los flags enriched y ai_classified para forzar re-clasificación.
    Si store_id se provee, solo resetea esa tienda.
    Retorna la cantidad de productos reseteados.
    """
    query = db.query(Product)
    if store_id:
        query = query.filter(Product.store_id == store_id)

    count = query.count()

    query.update(
        {Product.enriched: False, Product.ai_classified: False},
        synchronize_session=False,
    )
    db.commit()

    scope = f"tienda {store_id}" if store_id else "todos los productos"
    logger.info(f"Reset de enriquecimiento: {count} productos en {scope}")
    return {"reset_count": count}


def get_enrichment_status(db: Session) -> dict:
    """Retorna estadísticas del estado de enriquecimiento."""
    total = db.query(Product).count()
    enriched = db.query(Product).filter(Product.enriched == True).count()  # noqa: E712
    pending = total - enriched
    return {
        "total": total,
        "enriched": enriched,
        "pending": pending,
        "percent": round((enriched / total * 100) if total > 0 else 0, 1),
    }