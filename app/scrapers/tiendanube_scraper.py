"""
Scraper para tiendas en plataforma Tienda Nube (mitiendanube.com).
Usada por muchas tiendas argentinas como Leur, etc.

Selectores basados en la estructura de Tienda Nube:
- Contenedor: .js-item-product
- Título: .item-name
- Precio: .item-price
- Imagen: img.product-item-image-featured
- Link: a.item-link
- ID externo: data-product-id
"""
import json
import logging
from typing import Optional
from bs4 import BeautifulSoup
from app.scrapers.base_scraper import BaseScraper
from app.core.config import settings

logger = logging.getLogger(__name__)


def _split_title_color(title: str) -> tuple[str, str | None]:
    """
    Detecta patrones de variante de color en el título.
    Ejemplos:
      "remera emily // negro" → ("remera emily", "negro")
      "top claire // choco"   → ("top claire", "choco")
      "jean recto azul"       → ("jean recto azul", None)
    Separadores soportados: //, -, |, colores al final
    """
    import re
    # Patrón principal: nombre // color
    if "//" in title:
        parts = title.split("//", 1)
        return parts[0].strip(), parts[1].strip()
    return title, None


class TiendaNubeScraper(BaseScraper):
    """Scraper específico para tiendas en plataforma Tienda Nube."""

    def scrape_store(self, catalog_url: str, max_pages: int = None) -> list[dict]:
        max_pages = max_pages or settings.MAX_PAGES_PER_STORE
        products = []
        seen_ids = set()
        base_url = self._get_base_url(catalog_url)

        # Analizar la URL para detectar el tipo de paginación y página máxima
        pagination = self._parse_pagination_from_url(catalog_url)
        logger.info(f"Paginación parseada: {pagination}")
        base_catalog_url = pagination["base_url"]
        page_param = pagination["param"]   # "mpage", "page", o None
        last_page = pagination["last_page"] # número si está en la URL, sino None

        if page_param == "mpage":
            # Scroll infinito JS — usar Selenium directamente
            logger.info("Scroll infinito (mpage) detectado — usando Selenium")
            products = self._scrape_with_selenium(base_catalog_url)
        elif page_param is None:
            # Sin parámetro — probar requests primero, si solo da 12 usar Selenium
            soup = self.get_page(base_catalog_url)
            if soup:
                initial = self._extract_products(soup, base_url)
                if len(initial) < 13 and self._detect_mpage(base_catalog_url, base_url):
                    logger.info("JS scroll infinito detectado — usando Selenium")
                    products = self._scrape_with_selenium(base_catalog_url)
                else:
                    products = initial
                    # Continuar con paginación normal si hay más páginas
                    for page_num in range(2, max_pages + 1):
                        url = f"{base_catalog_url}?page={page_num}"
                        soup = self.get_page(url)
                        if not soup:
                            break
                        page_products = self._extract_products(soup, base_url)
                        if not page_products:
                            break
                        new_ps = [p for p in page_products
                                  if (p.get("external_id") or p.get("product_url")) not in seen_ids]
                        if not new_ps:
                            break
                        for p in new_ps:
                            seen_ids.add(p.get("external_id") or p.get("product_url"))
                        products.extend(new_ps)
                        if not self._has_next_page(soup):
                            break
            else:
                page_param = "page"  # fallback a paginación normal
                for page_num in range(1, max_pages + 1):
                    url = base_catalog_url if page_num == 1 else f"{base_catalog_url}?page={page_num}"
                    logger.info(f"Scrapeando página {page_num}: {url}")
                    soup = self.get_page(url)
                    if not soup:
                        break
                    page_products = self._extract_products(soup, base_url)
                    if not page_products:
                        break
                    products.extend(page_products)
                    logger.info(f"  → {len(page_products)} productos (total: {len(products)})")
                    if not self._has_next_page(soup):
                        break
        else:
            # Paginación normal (?page=N): iterar página por página
            for page_num in range(1, max_pages + 1):
                if page_num == 1:
                    url = base_catalog_url
                else:
                    param = page_param or "page"
                    url = f"{base_catalog_url}?{param}={page_num}"
                logger.info(f"Scrapeando página {page_num}: {url}")

                soup = self.get_page(url)
                if not soup:
                    logger.warning(f"No se pudo obtener página {page_num}")
                    break

                items_count = len(soup.select(".js-item-product"))
                logger.info(f"  → {items_count} items encontrados en soup")

                page_products = self._extract_products(soup, base_url)
                if not page_products:
                    logger.info(f"Sin productos en página {page_num}, deteniendo.")
                    break

                new_products = []
                for p in page_products:
                    key = p.get("external_id") or p.get("product_url")
                    if key not in seen_ids:
                        seen_ids.add(key)
                        new_products.append(p)

                if not new_products:
                    logger.info(f"Página {page_num} sin productos nuevos, deteniendo.")
                    break

                products.extend(new_products)
                logger.info(f"  → {len(new_products)} productos nuevos (total: {len(products)})")

                if not self._has_next_page(soup):
                    break

        logger.info(f"Total productos scrapeados: {len(products)}")
        return products

    def _parse_pagination_from_url(self, catalog_url: str) -> dict:
        """
        Extrae info de paginación de la URL del catálogo via regex.
        Ejemplos:
          https://leur.com.ar/productos/?mpage=14  → {param: mpage, last_page: 14}
          https://tienda.com/shop/?page=3          → {param: page, last_page: 3}
          https://tienda.com/productos/            → {param: None, last_page: None}
        """
        import re
        base = catalog_url.split("?")[0].rstrip("/") + "/"

        for param in ["mpage", "page"]:
            match = re.search(r"[?&]" + param + r"=(\d+)", catalog_url)
            if match:
                return {"base_url": base, "param": param, "last_page": int(match.group(1))}

        return {"base_url": base, "param": None, "last_page": None}

    def _detect_mpage(self, catalog_url: str, base_url: str) -> bool:
        """Detecta si la tienda usa ?mpage= para paginación (scroll infinito)."""
        try:
            test_url = f"{catalog_url}?mpage=2"
            soup = self.get_page(test_url)
            if soup and len(soup.select(".js-item-product")) > 0:
                return True
        except Exception:
            pass
        return False

    def _find_last_mpage(self, catalog_url: str, base_url: str) -> int:
        """
        Encuentra la última página de scroll infinito buscando binariamente.
        En Tienda Nube con mpage, cada página acumula todos los anteriores,
        así que la última página tiene el catálogo completo.
        """
        # Buscar la última página válida entre 1 y 50
        last_valid = 1
        last_count = 0

        for page in range(1, 51):
            url = f"{catalog_url}?mpage={page}"
            try:
                soup = self.get_page(url)
                if not soup:
                    break
                count = len(soup.select(".js-item-product"))
                if count == 0 or count == last_count:
                    # Sin productos nuevos — página anterior era la última
                    break
                last_count = count
                last_valid = page
                logger.info(f"  mpage={page}: {count} items acumulados")
            except Exception:
                break

        logger.info(f"Última mpage encontrada: {last_valid} ({last_count} productos)")
        return last_valid

    def _scrape_with_selenium(self, catalog_url: str) -> list[dict]:
        """Delega al scraper Selenium para tiendas con scroll infinito JS."""
        try:
            from app.scrapers.tiendanube_selenium_scraper import scrape_with_selenium
            return scrape_with_selenium(catalog_url)
        except ImportError:
            logger.error("selenium no instalado — ejecutá: pip install selenium webdriver-manager")
            return []
        except Exception as e:
            logger.error(f"Error en Selenium scraper: {e}")
            return []

    def _extract_products(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        items = soup.select(".js-item-product")
        if not items:
            logger.debug("No se encontraron .js-item-product")
            return []

        products = []
        for item in items:
            product = self._extract_single(item, base_url)
            if product:
                products.append(product)
        return products

    def _extract_single(self, item, base_url: str) -> Optional[dict]:
        try:
            # ID externo
            external_id = item.get("data-product-id")

            # Título — .js-item-name (Hugs) o .item-name (Leur)
            title_el = item.select_one(".js-item-name, .item-name")
            if not title_el:
                return None
            title = title_el.get_text(strip=True)
            if not title:
                return None

            # URL del producto — múltiples fallbacks
            product_url = None
            for link_sel in ["a.product-item-link", "a.item-link", "a.js-product-item-image-link-private", "a[href*='/productos/']", "a[href*='/product']", "h2 a", "h3 a"]:
                link_el = item.select_one(link_sel)
                if link_el and link_el.get("href"):
                    href = link_el["href"]
                    product_url = href if href.startswith("http") else base_url + href
                    break
            if not product_url:
                return None

            # Precio — intentar desde data-variants primero (más confiable)
            price = self._extract_price_from_variants(item)
            if price is None:
                price_el = item.select_one(".item-price")
                if price_el:
                    price = self._parse_price(price_el.get_text(strip=True))

            # Imagen — intentar data-variants primero (tiene URL real),
            # luego srcset, luego src
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

            # Detectar patrón nombre // color
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
            logger.debug(f"Error extrayendo producto Tienda Nube: {e}")
            return None

    def _extract_image_from_variants(self, item) -> Optional[str]:
        """Extrae image_url del atributo data-variants (evita placeholders lazy)."""
        if item.get("data-variants"):
            container = item
        else:
            container = item.select_one("[data-variants]")
        if not container:
            return None
        try:
            variants = json.loads(container["data-variants"])
            if variants and isinstance(variants, list):
                img = variants[0].get("image_url", "")
                if img:
                    if img.startswith("//"):
                        img = "https:" + img
                    return img
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    def _extract_price_from_variants(self, item) -> Optional[float]:
        """Extrae precio del atributo data-variants (más confiable que el HTML).
        En algunas tiendas (ej: Hugs) el atributo está en el item mismo,
        en otras está en un hijo.
        """
        # Primero intentar en el item mismo
        if item.get("data-variants"):
            container = item
        else:
            container = item.select_one("[data-variants]")
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
        """Verifica si existe página siguiente."""
        next_link = soup.select_one("a[rel='next'], .pagination .next, li.next a")
        return next_link is not None

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