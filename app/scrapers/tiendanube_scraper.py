"""
Scraper para tiendas en plataforma Tienda Nube (mitiendanube.com).

ESTRATEGIA:
- Si la URL tiene ?mpage=N: Tienda Nube renderiza el contenido con JS.
  requests+BS4 solo ve los primeros 12 productos del HTML estático.
  → Ir directo a Selenium, que itera mpage=1..N abriendo cada página.
- Si no tiene ?mpage: intentar requests primero, detectar si hay mpage,
  y si no, usar paginación normal ?page=N.
"""
import json
import logging
from typing import Optional
from bs4 import BeautifulSoup
from app.scrapers.base_scraper import BaseScraper
from app.core.config import settings

logger = logging.getLogger(__name__)


def _split_title_color(title: str) -> tuple[str, str | None]:
    if "//" in title:
        parts = title.split("//", 1)
        return parts[0].strip(), parts[1].strip()
    return title, None


class TiendaNubeScraper(BaseScraper):

    def scrape_store(self, catalog_url: str, max_pages: int = None) -> list[dict]:
        max_pages = max_pages or settings.MAX_PAGES_PER_STORE
        base_url = self._get_base_url(catalog_url)

        pagination = self._parse_pagination_from_url(catalog_url)
        logger.info(f"Paginación parseada: {pagination}")
        base_catalog_url = pagination["base_url"]
        page_param = pagination["param"]
        last_page = pagination["last_page"]

        # ── Caso 1: URL tiene ?mpage=N → Selenium directo ────────────────────
        # requests+BS4 solo ve 12 productos del HTML estático.
        # Selenium ejecuta el JS y ve los productos reales de cada página.
        if page_param == "mpage" and last_page:
            logger.info(f"mpage={last_page} detectado — usando Selenium (JS requerido)")
            products = self._scrape_with_selenium(base_catalog_url, last_mpage=last_page)
            logger.info(f"Total productos scrapeados: {len(products)}")
            return products

        # ── Caso 2: Sin parámetro → detectar tipo de paginación ──────────────
        elif page_param is None:
            soup = self.get_page(base_catalog_url)
            if not soup:
                logger.warning(f"No se pudo obtener: {base_catalog_url}")
                return []

            initial = self._extract_products(soup, base_url)
            logger.info(f"Primera página con requests: {len(initial)} productos")

            # Verificar si hay mpage=2 con productos distintos
            last_mpage = self._detect_last_mpage(base_catalog_url, base_url, initial)

            if last_mpage and last_mpage > 1:
                logger.info(f"mpage detectado — usando Selenium para {last_mpage} páginas")
                products = self._scrape_with_selenium(base_catalog_url, last_mpage=last_mpage)
                logger.info(f"Total productos scrapeados: {len(products)}")
                return products
            else:
                # Paginación normal ?page=N con requests
                products = list(initial)
                seen_ids = {p.get("external_id") or p.get("product_url") for p in initial}

                for page_num in range(2, max_pages + 1):
                    url = f"{base_catalog_url}?page={page_num}"
                    soup = self.get_page(url)
                    if not soup:
                        break
                    page_products = self._extract_products(soup, base_url)
                    if not page_products:
                        break
                    new_ps = [
                        p for p in page_products
                        if (p.get("external_id") or p.get("product_url")) not in seen_ids
                    ]
                    if not new_ps:
                        break
                    for p in new_ps:
                        seen_ids.add(p.get("external_id") or p.get("product_url"))
                    products.extend(new_ps)
                    logger.info(f"  → {len(new_ps)} nuevos (total: {len(products)})")
                    if not self._has_next_page(soup):
                        break

                logger.info(f"Total productos scrapeados: {len(products)}")
                return products

        # ── Caso 3: ?page=N normal con requests ──────────────────────────────
        else:
            products = []
            seen_ids = set()

            for page_num in range(1, max_pages + 1):
                url = base_catalog_url if page_num == 1 else f"{base_catalog_url}?{page_param}={page_num}"
                logger.info(f"Scrapeando página {page_num}: {url}")
                soup = self.get_page(url)
                if not soup:
                    break
                page_products = self._extract_products(soup, base_url)
                if not page_products:
                    break
                new_products = []
                for p in page_products:
                    key = p.get("external_id") or p.get("product_url")
                    if key not in seen_ids:
                        seen_ids.add(key)
                        new_products.append(p)
                if not new_products:
                    break
                products.extend(new_products)
                logger.info(f"  → {len(new_products)} nuevos (total: {len(products)})")
                if not self._has_next_page(soup):
                    break

            logger.info(f"Total productos scrapeados: {len(products)}")
            return products

    def _detect_last_mpage(self, catalog_url: str, base_url: str, initial_products: list) -> Optional[int]:
        """Detecta si la tienda usa mpage y encuentra la última página."""
        try:
            soup2 = self.get_page(f"{catalog_url}?mpage=2")
            if not soup2:
                return None
            page2 = self._extract_products(soup2, base_url)
            if not page2:
                return None

            known = {p.get("external_id") or p.get("product_url") for p in initial_products}
            new_in_page2 = [
                p for p in page2
                if (p.get("external_id") or p.get("product_url")) not in known
            ]
            if not new_in_page2:
                return None

            logger.info(f"mpage=2 tiene {len(new_in_page2)} productos nuevos → buscando última página")

            last_valid = 2
            for page in range(3, 101):
                soup = self.get_page(f"{catalog_url}?mpage={page}")
                if not soup or not soup.select(".js-item-product"):
                    break
                last_valid = page

            logger.info(f"Última mpage encontrada: {last_valid}")
            return last_valid

        except Exception as e:
            logger.debug(f"Error en _detect_last_mpage: {e}")
            return None

    def _parse_pagination_from_url(self, catalog_url: str) -> dict:
        import re
        base = catalog_url.split("?")[0].rstrip("/") + "/"
        for param in ["mpage", "page"]:
            match = re.search(r"[?&]" + param + r"=(\d+)", catalog_url)
            if match:
                return {"base_url": base, "param": param, "last_page": int(match.group(1))}
        return {"base_url": base, "param": None, "last_page": None}

    def _scrape_with_selenium(self, catalog_url: str, last_mpage: int = None) -> list[dict]:
        try:
            from app.scrapers.tiendanube_selenium_scraper import scrape_with_selenium
            return scrape_with_selenium(catalog_url, last_mpage=last_mpage)
        except ImportError:
            logger.error("selenium no instalado — ejecutá: pip install selenium webdriver-manager")
            return []
        except Exception as e:
            logger.error(f"Error en Selenium scraper: {e}")
            return []

    def _extract_products(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        items = soup.select(".js-item-product")
        if not items:
            return []
        products = []
        for item in items:
            product = self._extract_single(item, base_url)
            if product:
                products.append(product)
        return products

    def _extract_single(self, item, base_url: str) -> Optional[dict]:
        try:
            external_id = item.get("data-product-id")
            title_el = item.select_one(".js-item-name, .item-name")
            if not title_el:
                return None
            title = title_el.get_text(strip=True)
            if not title:
                return None

            product_url = None
            for link_sel in [
                "a.product-item-link", "a.item-link",
                "a.js-product-item-image-link-private",
                "a[href*='/productos/']", "a[href*='/product']",
                "h2 a", "h3 a"
            ]:
                link_el = item.select_one(link_sel)
                if link_el and link_el.get("href"):
                    href = link_el["href"]
                    product_url = href if href.startswith("http") else base_url + href
                    break
            if not product_url:
                return None

            price = self._extract_price_from_variants(item)
            if price is None:
                price_el = item.select_one(".item-price")
                if price_el:
                    price = self._parse_price(price_el.get_text(strip=True))

            image_url = self._extract_image_from_variants(item)
            if not image_url:
                for img in item.find_all("img"):
                    src = img.get("src") or ""
                    if not src or src.startswith("data:") or "placeholder" in src:
                        srcset = img.get("srcset") or img.get("data-srcset") or ""
                        if srcset:
                            entries = [s.strip().split()[0] for s in srcset.split(",") if s.strip()]
                            src = entries[-1] if entries else ""
                        else:
                            continue
                    if not src or src.startswith("data:"):
                        continue
                    if src.startswith("//"):
                        src = "https:" + src
                    elif not src.startswith("http"):
                        src = base_url + src
                    image_url = src
                    break

            base_title, color_variant = _split_title_color(title)

            return {
                "external_id": str(external_id) if external_id else None,
                "title": base_title,
                "color_variant": color_variant,
                "description": None,
                "price": price,
                "image_url": image_url,
                "product_url": product_url,
            }

        except Exception as e:
            logger.debug(f"Error extrayendo producto: {e}")
            return None

    def _extract_image_from_variants(self, item) -> Optional[str]:
        container = item if item.get("data-variants") else item.select_one("[data-variants]")
        if not container:
            return None
        try:
            variants = json.loads(container["data-variants"])
            if variants and isinstance(variants, list):
                img = variants[0].get("image_url", "")
                if img:
                    return "https:" + img if img.startswith("//") else img
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    def _extract_price_from_variants(self, item) -> Optional[float]:
        container = item if item.get("data-variants") else item.select_one("[data-variants]")
        if not container:
            return None
        try:
            variants = json.loads(container["data-variants"])
            if variants and isinstance(variants, list):
                price = variants[0].get("price_number", 0)
                return float(price) if price else None
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        return bool(soup.select_one("a[rel='next'], .pagination .next, li.next a"))

    def _get_base_url(self, url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _parse_price(self, text: str) -> Optional[float]:
        import re
        cleaned = re.sub(r"[^\d,.]", "", text.strip())
        if not cleaned:
            return None
        try:
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        except ValueError:
            return None