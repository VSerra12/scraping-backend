"""
Scraper para shopnatural.ar — WooCommerce con tema Flatsome.

Diferencias con WooCommerceScraper estándar:
1. Contenedor: div.product-small  (NO li.product — Flatsome usa divs)
2. Imágenes:   data-src en el primer <img> (lazy loading)
3. Precios:    <del> = original, <ins> = actual → tomar siempre <ins>
4. ID externo: data-prod en el botón "Vista Rápida"
5. Título:     p.name.product-title > a  o  p.woocommerce-loop-product__title > a
6. URL:        a.woocommerce-LoopProduct-link
7. Paginación: /shop/page/N/  (ya soportada por WooCommerceScraper base)
8. Stock:      clase out-of-stock en el div contenedor
"""
import logging
from typing import Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from app.scrapers.woocommerce_scraper import WooCommerceScraper

logger = logging.getLogger(__name__)


class ShopNaturalScraper(WooCommerceScraper):
    """
    Scraper para shopnatural.ar (tema Flatsome).
    Sobreescribe _extract_products y _extract_single del padre.
    """

    def _extract_products(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        # Flatsome usa div.product-small, no li.product
        items = soup.select("div.product-small")
        logger.info(f"  → div.product-small encontrados: {len(items)}")
        return [p for p in (self._extract_single(item, base_url) for item in items) if p]

    def _extract_single(self, item, base_url: str) -> Optional[dict]:
        try:
            # ── ID externo ────────────────────────────────────────────────────
            # En Flatsome el ID está en data-prod del botón "Vista Rápida"
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
            # Flatsome: <p class="name product-title woocommerce-loop-product__title">
            #             <a href="...">Nombre del producto</a>
            #           </p>
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

            # ── Precio — tomar siempre el precio actual (<ins>) ───────────────
            # Con descuento: <del>$26.000</del> <ins>$20.000</ins>
            # Sin descuento: <span class="woocommerce-Price-amount">$27.000</span>
            price = None

            ins_el = item.select_one("ins .woocommerce-Price-amount")
            if ins_el:
                price = self._parse_price(ins_el.get_text(strip=True))

            if price is None:
                # Sin descuento: precio único — clonar el item para no mutar el árbol
                from copy import copy
                item_copy = copy(item)
                for del_el in item_copy.select("del"):
                    del_el.decompose()
                price_el = item_copy.select_one(".woocommerce-Price-amount")
                if price_el:
                    price = self._parse_price(price_el.get_text(strip=True))

            # ── Imagen — Flatsome usa lazy loading con data-src ───────────────
            # Estructura típica:
            #   <img src="real.jpg" ...>              ← primera imagen (principal)
            #   <img src="data:svg" data-src="hover.jpg" class="lazy-load show-on-hover ...">
            #
            # Saltar siempre las imágenes de hover (show-on-hover / back-image).
            image_url = None
            for img in item.select("img"):
                classes = img.get("class", [])
                if "show-on-hover" in classes or "back-image" in classes:
                    continue

                src = None

                # data-src tiene la URL real con lazy load
                for attr in ["data-src", "data-lazy-src"]:
                    val = img.get(attr, "")
                    if val and not val.startswith("data:"):
                        src = val
                        break

                # src directo (primer producto de la página, cargado sin lazy)
                if not src:
                    raw = img.get("src", "")
                    if raw and not raw.startswith("data:"):
                        src = raw

                # srcset como último recurso
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