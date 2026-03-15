"""
Scraper base con funcionalidades comunes:
- Rate limiting
- Headers realistas
- Retry con backoff exponencial
- Detección de bloqueos
"""
import time
import random
import logging
from typing import Optional
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from fake_useragent import UserAgent
from app.core.config import settings

logger = logging.getLogger(__name__)

ua = UserAgent()


class ScraperError(Exception):
    pass


class BlockedError(ScraperError):
    pass


class BaseScraper:
    """Scraper base con rate limiting y manejo de errores."""

    def __init__(self):
        self.session = requests.Session()
        self._update_headers()

    def _update_headers(self):
        self.session.headers.update({
            "User-Agent": ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",  # Sin "br" — requests no soporta brotli
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })

    def _rate_limit(self):
        """Pausa aleatoria entre requests para no sobrecargar el servidor."""
        delay = random.uniform(settings.SCRAPING_DELAY_MIN, settings.SCRAPING_DELAY_MAX)
        time.sleep(delay)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def get_page(self, url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
        """
        Descarga una página y retorna BeautifulSoup.
        Maneja bloqueos (403, 429) y errores de red.
        """
        self._rate_limit()
        self._update_headers()  # Rotar user-agent en cada request

        try:
            response = self.session.get(url, timeout=timeout)

            if response.status_code == 403:
                logger.warning(f"403 Forbidden en {url}")
                raise BlockedError(f"Bloqueado por 403 en {url}")

            if response.status_code == 429:
                logger.warning(f"429 Too Many Requests en {url}, esperando 30s...")
                time.sleep(30)
                raise BlockedError(f"Rate limited (429) en {url}")

            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} en {url}")
                return None

            # Usar response.content (bytes) para que BeautifulSoup maneje
            # la descompresión y el encoding correctamente
            return BeautifulSoup(response.content, "html.parser")

        except BlockedError:
            raise
        except requests.RequestException as e:
            logger.error(f"Error de red en {url}: {e}")
            raise

    def scrape_store(self, catalog_url: str, max_pages: int = None) -> list[dict]:
        """
        Método a implementar en subclases.
        Retorna lista de dicts con info de productos.
        """
        raise NotImplementedError("Cada scraper debe implementar scrape_store()")


class GenericScraper(BaseScraper):
    """
    Scraper genérico que intenta extraer productos de cualquier tienda.
    Para tiendas específicas, crear una subclase con lógica propia.
    """

    COMMON_PRODUCT_SELECTORS = [
        # Patrones comunes en tiendas argentinas/WooCommerce/Shopify
        "article.product",
        ".product-item",
        ".product-card",
        "li.product",
        "[data-product-id]",
        ".item-product",
        ".product-thumb",
    ]

    TITLE_SELECTORS = [
        "h2.product-title", "h3.product-title",
        ".product-name", ".product-title",
        "h2 a", "h3 a", ".name",
    ]

    PRICE_SELECTORS = [
        ".price", ".product-price", "span.amount",
        "[class*='price']", "ins .amount",
    ]

    IMAGE_SELECTORS = [
        "img.product-image", "img.wp-post-image",
        ".product-image img", "img[src*='product']",
        "img[loading='lazy']", "img",
    ]

    LINK_SELECTORS = [
        "a.product-link", "a.woocommerce-loop-product__link",
        ".product-card a", "h2 a", "h3 a", "a[href*='/producto']",
        "a[href*='/product']", "a[href*='/tienda']",
    ]

    def scrape_store(self, catalog_url: str, max_pages: int = None) -> list[dict]:
        max_pages = max_pages or settings.MAX_PAGES_PER_STORE
        products = []
        current_url = catalog_url

        for page_num in range(1, max_pages + 1):
            logger.info(f"Scrapeando página {page_num}: {current_url}")
            soup = self.get_page(current_url)

            if not soup:
                logger.warning(f"No se pudo obtener página {page_num}")
                break

            page_products = self._extract_products(soup, catalog_url)
            if not page_products:
                logger.info(f"Sin productos en página {page_num}, deteniendo.")
                break

            products.extend(page_products)
            logger.info(f"  → {len(page_products)} productos encontrados")

            # Buscar link "siguiente página"
            next_url = self._get_next_page(soup, catalog_url, page_num)
            if not next_url:
                break
            current_url = next_url

        logger.info(f"Total productos scrapeados: {len(products)}")
        return products

    def _extract_products(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extrae productos de la página usando selectores comunes."""
        products = []

        # Encontrar contenedor de productos
        items = []
        for selector in self.COMMON_PRODUCT_SELECTORS:
            items = soup.select(selector)
            if items:
                break

        if not items:
            logger.debug("No se encontraron items con selectores estándar")
            return []

        for item in items:
            product = self._extract_single_product(item, base_url)
            if product:
                products.append(product)

        return products

    def _extract_single_product(self, item, base_url: str) -> Optional[dict]:
        """Extrae info de un producto individual."""
        try:
            # Título
            title = None
            for sel in self.TITLE_SELECTORS:
                el = item.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

            if not title:
                return None

            # Precio
            price = None
            for sel in self.PRICE_SELECTORS:
                el = item.select_one(sel)
                if el:
                    price_text = el.get_text(strip=True)
                    price = self._parse_price(price_text)
                    if price:
                        break

            # Imagen
            image_url = None
            for sel in self.IMAGE_SELECTORS:
                el = item.select_one(sel)
                if el:
                    src = el.get("src") or el.get("data-src") or el.get("data-lazy-src")
                    if src and not src.endswith(".svg") and "placeholder" not in src:
                        image_url = self._make_absolute(src, base_url)
                        break

            # URL del producto
            product_url = None
            for sel in self.LINK_SELECTORS:
                el = item.select_one(sel)
                if el and el.get("href"):
                    product_url = self._make_absolute(el["href"], base_url)
                    break

            if not product_url:
                # Último recurso: primer link del item
                a = item.find("a", href=True)
                if a:
                    product_url = self._make_absolute(a["href"], base_url)

            if not product_url:
                return None

            # Descripción (opcional, de meta o párrafos)
            description = None
            desc_el = item.select_one(".product-description, .short-description, p")
            if desc_el:
                description = desc_el.get_text(strip=True)[:500]

            # ID externo (si está disponible)
            external_id = (
                item.get("data-product-id") or
                item.get("data-id") or
                item.get("id")
            )

            return {
                "external_id": str(external_id) if external_id else None,
                "title": title,
                "description": description,
                "price": price,
                "image_url": image_url,
                "product_url": product_url,
            }

        except Exception as e:
            logger.debug(f"Error extrayendo producto: {e}")
            return None

    def _parse_price(self, text: str) -> Optional[float]:
        """Extrae número de un string de precio como '$12.500,00'."""
        import re
        # Remover $ y espacios, manejar formato argentino
        cleaned = re.sub(r"[^\d,.]", "", text.strip())
        if not cleaned:
            return None
        try:
            # Formato argentino: 12.500,00 → 12500.00
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        except ValueError:
            return None

    def _make_absolute(self, url: str, base_url: str) -> str:
        """Convierte URLs relativas en absolutas."""
        if url.startswith("http"):
            return url
        from urllib.parse import urljoin
        return urljoin(base_url, url)

    def _get_next_page(self, soup: BeautifulSoup, base_url: str, current_page: int) -> Optional[str]:
        """Busca el link a la siguiente página."""
        # Estrategia 1: link rel="next"
        next_link = soup.find("link", rel="next")
        if next_link and next_link.get("href"):
            return next_link["href"]

        # Estrategia 2: botón "siguiente" / "next"
        for sel in [
            "a.next", "a[aria-label='Next']", ".pagination a.next",
            "a:contains('Siguiente')", "a[rel='next']",
            ".woocommerce-pagination .next",
        ]:
            el = soup.select_one(sel)
            if el and el.get("href"):
                return self._make_absolute(el["href"], base_url)

        # Estrategia 3: paginación numérica — construir URL ?page=N
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(base_url)
        params = parse_qs(parsed.query)
        next_page = current_page + 1

        # Probar ?page=N o /page/N/
        if "page" in params or current_page > 1:
            params["page"] = [str(next_page)]
            new_query = urlencode({k: v[0] for k, v in params.items()})
            return urlunparse(parsed._replace(query=new_query))

        return None