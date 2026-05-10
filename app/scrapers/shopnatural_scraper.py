"""
Scraper para shopnatural.ar — WooCommerce con tema Flatsome.

Problemas específicos del sitio:
1. Imágenes con lazy loading: el src inicial es un SVG placeholder.
   La imagen real está en data-src o en el segundo <img> (hover).
2. Precios con descuento: hay precio tachado (original) y precio actual,
   el selector .woocommerce-Price-amount matchea ambos.
3. Links en español: /producto/ en vez de /product/
4. Paginación: /shop/page/2/

Estrategia: subclasear WooCommerceScraper y pisar los métodos problemáticos.
"""
import re
import logging
from typing import Optional
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from app.scrapers.woocommerce_scraper import WooCommerceScraper

logger = logging.getLogger(__name__)


class ShopNaturalScraper(WooCommerceScraper):
    """
    Scraper para shopnatural.ar.
    Extiende WooCommerceScraper corrigiendo lazy-loading de imágenes
    y extracción de precio actual (ignora el tachado).
    """

    def _extract_single(self, item, base_url: str) -> Optional[dict]:
        try:
            # ── ID externo ────────────────────────────────────────────────────
            external_id = None
            btn = item.select_one("[data-product_id]")
            if btn:
                external_id = btn.get("data-product_id") or btn.get("data-product-id")

            # ── URL del producto ──────────────────────────────────────────────
            # Flatsome usa a.woocommerce-LoopProduct-link o directamente el primer <a>
            link = (
                item.select_one("a.woocommerce-LoopProduct-link") or
                item.select_one("a[href*='/producto/']") or
                item.select_one("a.product-item-link") or
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
            # Flatsome: h3.product-title > a   o   .woocommerce-loop-product__title
            title = None
            for sel in [
                "h3.product-title a",
                "h3.product-title",
                "h2.woocommerce-loop-product__title",
                ".product-title",
                "h3", "h2",
            ]:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

            # Fallback: aria-label del botón
            if not title:
                add_btn = item.select_one("a.add_to_cart_button, a.product_type_variable")
                if add_btn:
                    aria = add_btn.get("aria-label", "")
                    match = re.search(r'"([^"]+)"', aria)
                    if match:
                        title = match.group(1).strip()

            if not title:
                img = item.select_one("img")
                if img:
                    title = img.get("alt", "").strip()

            if not title:
                return None

            # ── Precio — tomar el precio ACTUAL (no el tachado) ───────────────
            # Flatsome con descuento: <del>$26.000</del> <ins>$20.000</ins>
            # Sin descuento: simplemente .woocommerce-Price-amount
            price = None

            # Primero intentar precio <ins> (precio con descuento = precio actual)
            ins_el = item.select_one("ins .woocommerce-Price-amount, ins .amount")
            if ins_el:
                price = self._parse_price(ins_el.get_text(strip=True))

            # Si no hay <ins>, tomar el único precio disponible
            if price is None:
                # Excluir el <del> (precio original tachado)
                for el in item.select("del"):
                    el.decompose()  # sacar del árbol temporalmente
                price_el = item.select_one(".woocommerce-Price-amount, .amount")
                if price_el:
                    price = self._parse_price(price_el.get_text(strip=True))

            # ── Imagen — manejar lazy loading de Flatsome ─────────────────────
            # Flatsome carga imágenes con data-src o pone un SVG placeholder en src.
            # El <img> real puede tener: data-src, data-lazy-src, o srcset.
            # También hay dos <img>: la principal y la de hover (segunda).
            image_url = None
            imgs = item.select("img")

            for img in imgs:
                src = None

                # Prioridad: data-src > data-lazy-src > srcset > src
                for attr in ["data-src", "data-lazy-src", "data-original"]:
                    val = img.get(attr, "")
                    if val and not val.startswith("data:"):
                        src = val
                        break

                # Intentar srcset si todavía no tenemos nada
                if not src:
                    srcset = img.get("srcset") or img.get("data-srcset", "")
                    if srcset:
                        # Tomar la URL más grande del srcset
                        entries = [s.strip().split()[0] for s in srcset.split(",") if s.strip()]
                        if entries:
                            src = entries[-1]

                # Fallback: src directo si no es placeholder SVG
                if not src:
                    raw_src = img.get("src", "")
                    if raw_src and not raw_src.startswith("data:"):
                        src = raw_src

                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif not src.startswith("http"):
                        src = urljoin(base_url, src)
                    image_url = src
                    break  # usar la primera imagen válida encontrada

            return {
                "external_id": str(external_id) if external_id else None,
                "title": title,
                "description": None,
                "price": price,
                "image_url": image_url,
                "product_url": product_url,
                "available": "outofstock" not in item.get("class", []),
            }

        except Exception as e:
            logger.debug(f"Error extrayendo producto ShopNatural: {e}")
            return None

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        # Flatsome usa los mismos selectores estándar de WooCommerce
        return bool(
            soup.select_one("a.next.page-numbers") or
            soup.select_one(".woocommerce-pagination .next") or
            soup.select_one("a[rel='next']")
        )
