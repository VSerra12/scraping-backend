"""
Scraper para tiendas WooCommerce.
Selectores verificados contra dynamicjeans.com.ar.

Estructura HTML de WooCommerce (tema Mixtas/Kitify):
- Contenedor: li.product
- ID externo:  data-product_id en el botón add_to_cart
- Título:      aria-label del botón (contiene el nombre entre comillas)
- URL:         a.product-item-link href
- Imagen:      img.attachment-woocommerce_thumbnail src
- Precio:      .price .woocommerce-Price-amount
- Paginación:  a.next.page-numbers

FIX: Se excluyen productos con clase CSS "hidden" en li.product.
     WooCommerce agrega esa clase cuando la visibilidad del producto
     es "Hidden" — aparecen en categorías pero NO en /shop/.
     Sin este filtro el scraper los levantaba al scrapear URLs de
     categorías específicas (ej: /product-category/shop/denim/).
"""
import re
import logging
from typing import Optional
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

from app.scrapers.base_scraper import BaseScraper
from app.core.config import settings

logger = logging.getLogger(__name__)


class WooCommerceScraper(BaseScraper):
    """Scraper para tiendas WooCommerce."""

    def scrape_store(self, catalog_url: str, max_pages: int = None) -> list[dict]:
        max_pages = max_pages or settings.MAX_PAGES_PER_STORE
        products = []
        base_url = self._get_base_url(catalog_url)

        # WooCommerce pagina con /page/N/ o ?paged=N
        clean_url = catalog_url.split("?")[0].rstrip("/")

        for page_num in range(1, max_pages + 1):
            url = clean_url if page_num == 1 else f"{clean_url}/page/{page_num}/"
            logger.info(f"Scrapeando página {page_num}: {url}")

            soup = self.get_page(url)
            if not soup:
                logger.warning(f"No se pudo obtener página {page_num}")
                break

            raw_count = len(soup.select("li.product"))
            logger.info(f"  → li.product encontrados (antes de filtrar): {raw_count}")

            if raw_count == 0:
                logger.info(f"Sin productos en página {page_num}, deteniendo.")
                break

            page_products = self._extract_products(soup, base_url)
            products.extend(page_products)
            logger.info(f"  → {len(page_products)} productos extraídos (después de filtrar hidden)")

            if not self._has_next_page(soup):
                logger.info("No hay página siguiente, deteniendo.")
                break

        logger.info(f"Total productos scrapeados: {len(products)}")
        return products

    def _extract_products(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        items = soup.select("li.product")

        # FIX: excluir productos con visibilidad "Hidden" en WooCommerce.
        # Cuando un producto está configurado como "Hidden" (oculto del catálogo),
        # WooCommerce le agrega la clase CSS "hidden" al li.product.
        # Esos productos NO aparecen en /shop/ pero sí en URLs de categorías,
        # por eso el scraper los levantaba de más (ej: los 46 extra en Dynamic Jeans).
        before = len(items)
        items = [item for item in items if "hidden" not in item.get("class", [])]
        after = len(items)
        if before != after:
            logger.info(f"  → Filtrados {before - after} productos hidden del catálogo")

        return [p for p in (self._extract_single(item, base_url) for item in items) if p]

    def _extract_single(self, item, base_url: str) -> Optional[dict]:
        try:
            # ID externo — data-product_id en el botón
            external_id = None
            btn = item.select_one("[data-product_id]")
            if btn:
                external_id = btn.get("data-product_id") or btn.get("data-product-id")

            # URL del producto
            link = item.select_one("a.product-item-link") or item.select_one("a[href*='/product']")
            if not link:
                return None
            product_url = link.get("href", "")
            if not product_url:
                return None
            if not product_url.startswith("http"):
                product_url = urljoin(base_url, product_url)

            # Título — extraer del aria-label del botón
            # (ej: 'Elige las opciones para "MICHIGAN RAYADO"')
            title = None
            add_btn = item.select_one("a.add_to_cart_button, a.product_type_variable")
            if add_btn:
                aria = add_btn.get("aria-label", "")
                # El aria-label tiene formato: 'Elige las opciones para "NOMBRE"'
                # o 'Añadir al carrito: "NOMBRE"'
                match = re.search(r'"([^"]+)"', aria)
                if match:
                    title = match.group(1).strip()

            # Fallback: h2 o h3
            if not title:
                for sel in ["h2.woocommerce-loop-product__title", "h2", "h3", ".product-title"]:
                    el = item.select_one(sel)
                    if el and el.get_text(strip=True):
                        title = el.get_text(strip=True)
                        break

            # Fallback: texto del alt de la imagen
            if not title:
                img = item.select_one("img")
                if img:
                    title = img.get("alt", "").strip()

            if not title:
                return None

            # Precio
            price = None
            price_el = item.select_one(
                ".price .woocommerce-Price-amount, "
                ".price ins .woocommerce-Price-amount, "
                ".price .amount"
            )
            if price_el:
                price = self._parse_price(price_el.get_text(strip=True))
            if price is None:
                # Fallback: cualquier elemento con clase price
                price_el = item.select_one(".price")
                if price_el:
                    price = self._parse_price(price_el.get_text(strip=True))

            # Imagen
            image_url = None
            img = item.select_one(
                "img.attachment-woocommerce_thumbnail, img.wp-post-image, img"
            )
            if img:
                src = img.get("src") or img.get("data-src") or ""
                if src and not src.startswith("data:"):
                    if not src.startswith("http"):
                        src = urljoin(base_url, src)
                    image_url = src

            # "outofstock" en las clases del li indica que está agotado
            # (distinto de "hidden" que indica que está oculto del catálogo)
            available = "outofstock" not in item.get("class", [])

            return {
                "external_id": str(external_id) if external_id else None,
                "title": title,
                "description": None,
                "price": price,
                "image_url": image_url,
                "product_url": product_url,
                "available": available,
            }

        except Exception as e:
            logger.debug(f"Error extrayendo producto WooCommerce: {e}")
            return None

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        return bool(
            soup.select_one("a.next.page-numbers, .woocommerce-pagination .next, a[rel='next']")
        )

    def _get_base_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _parse_price(self, text: str) -> Optional[float]:
        cleaned = re.sub(r"[^\d,.]", "", text.strip())
        logger.info(f"[PRICE DEBUG] raw='{text.strip()}' cleaned='{cleaned}'")
        if not cleaned:
            return None
        try:
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            elif "." in cleaned:
                parts = cleaned.split(".")
                if len(parts) == 2 and len(parts[1]) == 3:
                    cleaned = cleaned.replace(".", "")
            result = float(cleaned)
            return result
        except ValueError:
            return None