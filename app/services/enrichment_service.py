"""
Servicio de enriquecimiento de productos.

Flujo:
1. Busca productos con enriched=False en la DB (o todos si force=True)
2. Visita la página individual de cada producto
3. Extrae descripción completa, materiales, talles y colores de variantes
4. Limpia la descripción de texto de marketing antes de clasificar
5. Clasifica con IA usando toda la info disponible
6. Marca enriched=True y ai_classified=True

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
from app.services.ai_classifier import classify_product, _clean_description

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Extractor de páginas individuales
# ─────────────────────────────────────────────────────────────────────────────

class ProductEnricher(BaseScraper):
    """Visita páginas individuales de productos y extrae info completa."""

    def enrich_product(self, product: Product, store: Store) -> dict:
        """
        Visita la URL del producto y extrae toda la info disponible.
        Retorna dict con: description, materials_raw, sizes, colors_hint.
        Nunca lanza excepción — retorna {} si algo falla.
        """
        if not product.product_url:
            logger.warning(f"Producto {product.id} sin URL, saltando visita")
            return {}

        try:
            soup = self.get_page(product.product_url)
        except Exception as e:
            logger.warning(f"No se pudo obtener {product.product_url}: {e}")
            return {}

        if not soup:
            return {}

        platform = self._detect_platform(store.url, soup)
        logger.debug(f"  Plataforma detectada: {platform} para {store.name}")

        if platform == "tiendanube":
            return self._extract_tiendanube(soup)
        elif platform == "woocommerce":
            return self._extract_woocommerce(soup)
        else:
            return self._extract_generic(soup)

    def _detect_platform(self, store_url: str, soup: BeautifulSoup) -> str:
        url = store_url or ""
        if (
            "mitiendanube.com" in url or
            "tiendanube.com" in url or
            bool(soup.select_one("[data-variants]")) or
            bool(soup.select_one(".js-product-container")) or
            bool(soup.select_one(".js-product-quantity-input"))
        ):
            return "tiendanube"
        if (
            bool(soup.select_one(".woocommerce-product-details__short-description")) or
            bool(soup.select_one(".woocommerce")) or
            bool(soup.select_one("table.variations")) or
            bool(soup.select_one(".woocommerce-Price-amount"))
        ):
            return "woocommerce"
        return "generic"

    def _extract_tiendanube(self, soup: BeautifulSoup) -> dict:
        description = self._extract_description(soup, [
            ".product-description",
            ".js-product-description",
            ".description",
            "[class*='description']",
            ".product-detail-description",
        ])

        sizes = []
        colors = []

        variants_el = soup.select_one("[data-variants]")
        if variants_el:
            try:
                variants = json.loads(variants_el["data-variants"])
                talle_keywords = {"s", "m", "l", "xl", "xxl", "xs", "único", "unico", "u", "talle único"}

                for v in variants:
                    for opt_key in ["option0", "option1", "option2"]:
                        opt = str(v.get(opt_key) or "").strip()
                        if not opt:
                            continue
                        opt_lower = opt.lower()
                        is_size = (
                            opt_lower in talle_keywords or
                            opt_lower.startswith("talle") or
                            opt_lower.startswith("talla") or
                            (opt_lower.isdigit() and len(opt_lower) <= 3)
                        )
                        if is_size:
                            if opt not in sizes:
                                sizes.append(opt)
                        else:
                            if opt_lower not in colors:
                                colors.append(opt_lower)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.debug(f"Error parseando data-variants: {e}")

        materials_raw = self._extract_materials_text(soup, description)
        return {"description": description, "materials_raw": materials_raw, "sizes": sizes, "colors_hint": colors}

    def _extract_woocommerce(self, soup: BeautifulSoup) -> dict:
        description = self._extract_description(soup, [
            ".woocommerce-product-details__short-description",
            ".product-short-description",
            ".woocommerce-Tabs-panel--description",
            "#tab-description",
            ".product-description",
            ".entry-content",
        ])

        sizes = []
        for sel in ["select[name*='talle']", "select[name*='size']", "select[name*='talla']"]:
            el = soup.select_one(sel)
            if el:
                sizes = [o.get_text(strip=True) for o in el.select("option") if o.get("value") not in ("", "0", None)]
                if sizes:
                    break

        colors = []
        for sel in ["select[name*='color']", "select[name*='colour']"]:
            el = soup.select_one(sel)
            if el:
                colors = [o.get_text(strip=True).lower() for o in el.select("option") if o.get("value") not in ("", "0", None)]
                if colors:
                    break

        materials_raw = self._extract_materials_text(soup, description)
        return {"description": description, "materials_raw": materials_raw, "sizes": sizes, "colors_hint": colors}

    def _extract_generic(self, soup: BeautifulSoup) -> dict:
        description = self._extract_description(soup, [
            ".product-description", ".description",
            "[class*='description']", ".product-info",
            ".product-details", "article .content",
        ])
        materials_raw = self._extract_materials_text(soup, description)
        return {"description": description, "materials_raw": materials_raw, "sizes": [], "colors_hint": []}

    def _extract_description(self, soup: BeautifulSoup, selectors: list) -> Optional[str]:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(separator=" ", strip=True)
                    if text and len(text) > 10:
                        # Guardamos el texto crudo — la limpieza se hace
                        # en classify_product para no perder info de materiales
                        return text[:1200]
            except Exception:
                continue
        return None

    def _extract_materials_text(self, soup: BeautifulSoup, description: Optional[str]) -> Optional[str]:
        for sel in [".product-composition", ".composition", "[class*='material']", "[class*='fabric']"]:
            try:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    if text and len(text) > 3:
                        return text[:400]
            except Exception:
                continue

        if not description:
            return None

        fabric_keywords = [
            "tela:", "tejido:", "composición:", "composicion:", "material:",
            "confeccionado en", "100%", "polyester", "algodón", "algodon", "cotton",
            "morley", "jersey", "lycra", "spandex", "lino", "seda",
            "gabardina", "denim", "microfibra", "modal", "viscosa", "ribb",
        ]
        for kw in fabric_keywords:
            if kw in description.lower():
                for sentence in description.replace("\n", ". ").split("."):
                    if kw in sentence.lower() and len(sentence.strip()) > 5:
                        return sentence.strip()[:400]
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Job de enriquecimiento
# ─────────────────────────────────────────────────────────────────────────────

def run_enrichment_job(
    db: Session,
    batch_size: int = 20,
    store_id: int = None,
    force: bool = False,
) -> dict:
    """
    Procesa hasta batch_size productos.

    force=False (default): solo productos con enriched=False (comportamiento original).
    force=True: re-enriquece productos ya procesados, priorizando los con
                category='otro' o ai_classified=False para corregirlos primero.

    store_id: opcional, filtra por tienda específica.
    """
    query = db.query(Product).filter(Product.available == True)  # noqa: E712

    if store_id:
        query = query.filter(Product.store_id == store_id)

    if not force:
        query = query.filter(Product.enriched == False)  # noqa: E712
    else:
        from sqlalchemy import case
        priority = case(
            (Product.category == "otro", 0),
            (Product.ai_classified == False, 1),  # noqa: E712
            else_=2,
        )
        query = query.order_by(priority)
        logger.info(
            f"Modo FORCE — re-enriqueciendo "
            f"{'tienda ' + str(store_id) if store_id else 'todas las tiendas'} "
            f"(batch={batch_size}, priorizando category='otro')"
        )

    pending = query.limit(batch_size).all()

    if not pending:
        logger.info(f"Enriquecimiento: no hay productos {'disponibles' if force else 'pendientes'}.")
        return {"enriched": 0, "failed": 0}

    logger.info(f"Enriquecimiento {'[FORCE] ' if force else ''}procesando {len(pending)} productos...")

    if force:
        for product in pending:
            product.enriched = False
            product.ai_classified = False
        db.flush()

    enricher = ProductEnricher()
    enriched_count = 0
    failed_count = 0

    store_ids = {p.store_id for p in pending}
    stores = {s.id: s for s in db.query(Store).filter(Store.id.in_(store_ids)).all()}

    for product in pending:
        store = stores.get(product.store_id)
        if not store:
            logger.warning(f"Tienda {product.store_id} no encontrada para producto {product.id}")
            product.enriched = True
            failed_count += 1
            continue

        try:
            # 1. Visitar página individual
            enriched_data = enricher.enrich_product(product, store)

            # 2. Actualizar descripción con el texto crudo de la página
            #    (se guarda sin limpiar para no perder info de materiales en DB)
            page_description = enriched_data.get("description")
            if page_description:
                if not product.description or len(page_description) > len(product.description or ""):
                    product.description = page_description

            if enriched_data.get("materials_raw"):
                product.materials_raw = enriched_data["materials_raw"]

            if enriched_data.get("sizes"):
                product.sizes = enriched_data["sizes"]

            colors_hint = enriched_data.get("colors_hint") or []

            # 3. Construir descripción para IA (limpiada de marketing)
            full_description = _build_full_description(product, enriched_data)

            logger.info(
                f"  Clasificando: '{product.title[:45]}' "
                f"(hint_colors={colors_hint[:3]}, desc={len(full_description or '')}c)"
            )

            # 4. Clasificar con IA
            #    _clean_description se aplica dentro de classify_product
            classification = classify_product(
                title=product.title,
                description=full_description,
                image_url=product.image_url,
                store_name=store.name,
                colors_hint=colors_hint if colors_hint else None,
            )

            # 5. Guardar clasificación
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
                f"  ✓ '{product.title[:40]}' → "
                f"cat={product.category} | neck={product.neck_type} | colors={product.colors}"
            )

        except Exception as e:
            logger.error(f"  ✗ Error en '{product.title[:40]}': {e}", exc_info=True)
            product.enriched = True
            product.ai_classified = False
            failed_count += 1
            try:
                db.flush()
            except Exception:
                db.rollback()
            continue

        time.sleep(random.uniform(1.0, 2.5))

    db.commit()
    logger.info(
        f"Enriquecimiento {'[FORCE] ' if force else ''}completo: "
        f"{enriched_count} OK, {failed_count} fallidos (de {len(pending)})"
    )
    return {"enriched": enriched_count, "failed": failed_count}


def _build_full_description(product: Product, enriched_data: dict) -> Optional[str]:
    """
    Combina descripción de página + materiales + talles para el prompt de IA.
    La descripción se guarda cruda en DB pero se limpia en classify_product
    antes de mandarla al modelo.
    """
    parts = []

    page_desc = enriched_data.get("description")
    if page_desc:
        parts.append(page_desc)
    elif product.description:
        parts.append(product.description)

    materials_raw = enriched_data.get("materials_raw") or product.materials_raw
    if materials_raw and (not parts or materials_raw not in parts[0]):
        parts.append(f"Composición: {materials_raw}")

    sizes = enriched_data.get("sizes") or []
    if sizes:
        parts.append(f"Talles: {', '.join(str(s) for s in sizes[:10])}")

    return " | ".join(parts) if parts else None


# ─────────────────────────────────────────────────────────────────────────────
#  Status
# ─────────────────────────────────────────────────────────────────────────────

def get_enrichment_status(db: Session) -> dict:
    total    = db.query(Product).count()
    enriched = db.query(Product).filter(Product.enriched == True).count()  # noqa: E712
    pending  = total - enriched
    return {
        "total":    total,
        "enriched": enriched,
        "pending":  pending,
        "percent":  round((enriched / total * 100) if total > 0 else 0, 1),
    }