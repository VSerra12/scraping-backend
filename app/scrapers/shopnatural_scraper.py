"""
Scraper para shopnatural.ar — WooCommerce con tema Flatsome.

Diferencias con WooCommerceScraper estándar:
1. Contenedor: div.product-small  (NO li.product — Flatsome usa divs)
2. scrape_store propio para evitar el chequeo li.product del padre
3. Imágenes:   data-src en el primer <img> (lazy loading)
4. Precios:    <del> = original, <ins> = actual → tomar siempre <ins>
5. ID externo: data-prod en el botón "Vista Rápida"
6. Título:     p.name.product-title > a
7. URL:        a.woocommerce-LoopProduct-link
8. Paginación: /shop/page/N/
9. Stock:      clase outofstock en el div contenedor
"""
import copy
import logging
from typing import Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from app.scrapers.woocommerce_scraper import WooCommerceScraper
from app.core.config import settings

logger = logging.getLogger(__name__)


class ShopNaturalScraper(WooCommerceScraper):
    """
    Scraper para shopnatural.ar (tema Flatsome).
    """

    def scrape_store(self, catalog_url: str, max_pages: int = None) -> list[dict]:
        max_pages = max_pages or settings.MAX_PAGES_PER_STORE
        products = []
        base_url = self._get_base_url(catalog_url)
        clean_url = catalog_url.split("?")[0].rstrip("/")

        for page_num in range(1, max_pages + 1):
            url = clean_url if page_num == 1 else f"{clean_url}/page/{page_num}/"
            logger.info(f"Scrapeando página {page_num}: {url}")

            soup = self.get_page(url)
            if not soup:
                logger.warning(f"No se pudo obtener página {page_num}")
                break

            page_products = self._extract_products(soup, base_url)
            logger.info(f"  → {len(page_products)} productos extraídos")

            if not page_products:
                logger.info(f"Sin productos en página {page_num}, deteniendo.")
                break

            products.extend(page_products)

            if not self._has_next_page(soup):
                logger.info("No hay página siguiente, deteniendo.")
                break

        logger.info(f"Total productos scrapeados: {len(products)}")
        return products

    def _extract_products(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        items = soup.select("div.product-small")
        logger.info(f"  → div.product-small encontrados: {len(items)}")
        return [p for p in (self._extract_single(item, base_url) for item in items) if p]

    def _extract_single(self, item, base_url: str) -> Optional[dict]:
        try:
            # ── ID externo ────────────────────────────────────────────────────
            external_id = None
            qv_btn = item.select_one("[data-prod]")
            if qv_btn:
                external_id = qv_btn.get("data-prod")

            # ── URL del producto ──────────────────────────────────────────────
            link = (
                item.select_one("a.woocommerce-LoopProduct-link") or
                item.select_one("a[href*='/producto/']") or
                item.find("a", href=True)
            )
            if not link:
                return None
            product_url = link.get("href", "")
            if not product_url or product_url == "#":
                return None
            if not product_url.startswith("http"):
                product_url = urljoin(base_url, product_url)

            # ── Título ────────────────────────────────────────────────────────
            title = None
            for sel in [
                "p.name.product-title a",
                "p.woocommerce-loop-product__title a",
                ".product-title a",
                "h3 a", "h2 a",
            ]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

            # Fallback: alt de la imagen principal (no la de hover)
            if not title:
                for img in item.select("img[alt]"):
                    alt = img.get("alt", "").strip()
                    if alt and not alt.startswith("Alternative view"):
                        title = alt
                        break

            if not title:
                return None

            # ── Precio ────────────────────────────────────────────────────────
            # Con descuento: <del>precio original</del> <ins>precio actual</ins>
            # Sin descuento: <span class="woocommerce-Price-amount">precio</span>
            price = None

            ins_el = item.select_one("ins .woocommerce-Price-amount")
            if ins_el:
                price = self._parse_price(ins_el.get_text(strip=True))

            if price is None:
                price_wrapper = item.select_one(".price-wrapper, .price")
                if price_wrapper:
                    wrapper_copy = copy.copy(price_wrapper)
                    for del_el in wrapper_copy.select("del"):
                        del_el.decompose()
                    price_el = wrapper_copy.select_one(".woocommerce-Price-amount")
                    if price_el:
                        price = self._parse_price(price_el.get_text(strip=True))

            # ── Imagen ────────────────────────────────────────────────────────
            # Flatsome lazy load: imagen principal tiene data-src o src real.
            # La imagen de hover tiene clase show-on-hover — saltarla siempre.
            image_url = None
            for img in item.select("img"):
                classes = img.get("class", [])
                if "show-on-hover" in classes or "back-image" in classes:
                    continue

                src = None

                for attr in ["data-src", "data-lazy-src"]:
                    val = img.get(attr, "")
                    if val and not val.startswith("data:"):
                        src = val
                        break

                if not src:
                    raw = img.get("src", "")
                    if raw and not raw.startswith("data:"):
                        src = raw

                if not src:
                    srcset = img.get("srcset") or img.get("data-srcset", "")
                    if srcset:
                        entries = [s.strip().split()[0] for s in srcset.split(",") if s.strip()]
                        if entries:
                            src = entries[0]

                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif not src.startswith("http"):
                        src = urljoin(base_url, src)
                    image_url = src
                    break

            # ── Stock ─────────────────────────────────────────────────────────
            classes = item.get("class", [])
            available = "outofstock" not in classes and "out-of-stock" not in classes

            return {
                "external_id": str(external_id) if external_id else None,
                "title":       title,
                "description": None,
                "price":       price,
                "image_url":   image_url,
                "product_url": product_url,
                "available":   available,
            }

        except Exception as e:
            logger.debug(f"Error extrayendo producto ShopNatural: {e}")
            return None

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        return bool(
            soup.select_one("a.next.page-numbers") or
            soup.select_one(".woocommerce-pagination .next") or
            soup.select_one("a[rel='next']")
        )