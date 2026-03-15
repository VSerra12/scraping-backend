"""
Scraper Selenium para tiendas Tienda Nube con scroll infinito (JS-heavy).
Úsalo cuando requests+BS4 solo devuelve 12 productos.

Requiere: pip install selenium webdriver-manager
"""
import json
import logging
import time
from typing import Optional
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


def _get_driver() -> webdriver.Chrome:
    """Inicializa Chrome headless."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--dns-prefetch-disable")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def _scroll_to_bottom(driver: webdriver.Chrome, pause: float = 2.5) -> int:
    """
    Hace scroll incremental para disparar el lazy loading de Tienda Nube.
    Cada 12 productos nuevos = 1 página cargada.
    Devuelve el total de productos encontrados.
    """
    last_count = 0
    no_change_streak = 0
    max_no_change = 5  # esperar más antes de rendirse

    while True:
        items = driver.find_elements(By.CSS_SELECTOR, ".js-item-product")
        current_count = len(items)
        logger.info(f"  Scroll: {current_count} productos cargados...")

        if current_count == last_count:
            no_change_streak += 1
            if no_change_streak >= max_no_change:
                break
            # Scroll más agresivo cuando no cambia
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(pause + 1)
        else:
            no_change_streak = 0
            last_count = current_count
            # Scroll incremental para triggear lazy load
            current_height = driver.execute_script("return document.body.scrollHeight")
            # Scroll a 80% del height para que el trigger de "casi al fondo" active la carga
            driver.execute_script(f"window.scrollTo(0, {int(current_height * 0.8)});")
            time.sleep(0.5)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(pause)

    return last_count


def _get_base_url(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_price(text: str) -> Optional[float]:
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


def _extract_price_from_variants(item) -> Optional[float]:
    """Extrae precio del atributo data-variants."""
    raw = item.get("data-variants") or ""
    if not raw:
        child = item.select_one("[data-variants]")
        raw = child.get("data-variants", "") if child else ""
    if not raw:
        return None
    try:
        variants = json.loads(raw)
        if variants and isinstance(variants, list):
            price = variants[0].get("price_number", 0)
            return float(price) if price else None
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def _extract_single(item, base_url: str) -> Optional[dict]:
    try:
        external_id = item.get("data-product-id")

        # Título
        title_el = item.select_one(".js-item-name, .item-name")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # URL
        product_url = None
        for sel in ["a.product-item-link", "a.item-link", "a.js-product-item-image-link-private",
                    "a[href*='/productos/']", "a[href*='/product']"]:
            link_el = item.select_one(sel)
            if link_el and link_el.get("href"):
                href = link_el["href"]
                product_url = href if href.startswith("http") else base_url + href
                break
        if not product_url:
            return None

        # Precio
        price = _extract_price_from_variants(item)
        if price is None:
            price_el = item.select_one(".js-price-display, .item-price, .price")
            if price_el:
                price = _parse_price(price_el.get_text(strip=True))

        # Imagen — buscar la primera img con src real (no base64/placeholder)
        image_url = None
        for img in item.find_all("img"):
            # Intentar src primero
            src = img.get("src") or ""
            # Si es base64/placeholder, intentar srcset o data-srcset
            if not src or src.startswith("data:") or "placeholder" in src:
                srcset = img.get("srcset") or img.get("data-srcset") or ""
                if srcset:
                    # Tomar la URL más grande del srcset (último entry)
                    entries = [s.strip().split()[0] for s in srcset.split(",") if s.strip()]
                    src = entries[-1] if entries else ""
            if not src or src.startswith("data:"):
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif not src.startswith("http"):
                src = base_url + src
            image_url = src
            break

        return {
            "external_id": str(external_id) if external_id else None,
            "title": title,
            "description": None,
            "price": price,
            "image_url": image_url,
            "product_url": product_url,
        }
    except Exception as e:
        logger.debug(f"Error extrayendo producto: {e}")
        return None


def scrape_with_selenium(catalog_url: str) -> list[dict]:
    """
    Scrapea una tienda Tienda Nube con scroll infinito usando Selenium.
    Úsalo como reemplazo de TiendaNubeScraper.scrape_store() para tiendas JS-heavy.
    """
    # Usar la URL base sin parámetros de paginación (el scroll carga todo)
    base_catalog = catalog_url.split("?")[0].rstrip("/") + "/"
    base_url = _get_base_url(catalog_url)

    logger.info(f"Iniciando Selenium para: {base_catalog}")
    driver = _get_driver()
    products = []

    try:
        driver.get(base_catalog)

        # Esperar a que carguen los primeros productos
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".js-item-product"))
            )
        except Exception:
            logger.warning("Timeout esperando productos — intentando igual")

        # Scroll hasta el fondo
        total = _scroll_to_bottom(driver, pause=2.0)
        logger.info(f"Scroll completo: {total} productos visibles")

        # Forzar carga de imágenes lazy — scroll suave desde arriba
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
        height = driver.execute_script("return document.body.scrollHeight")
        step = 400
        pos = 0
        while pos < height:
            driver.execute_script(f"window.scrollTo(0, {pos});")
            pos += step
            time.sleep(0.15)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # Parsear el HTML final
        soup = BeautifulSoup(driver.page_source, "html.parser")
        items = soup.select(".js-item-product")
        logger.info(f"Extrayendo {len(items)} productos...")

        for item in items:
            product = _extract_single(item, base_url)
            if product:
                products.append(product)

        logger.info(f"Total extraídos: {len(products)}")

    except Exception as e:
        logger.error(f"Error en Selenium scraper: {e}")
    finally:
        driver.quit()

    return products