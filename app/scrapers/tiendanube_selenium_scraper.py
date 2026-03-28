"""
Scraper Selenium para tiendas Tienda Nube con scroll infinito (JS-heavy).

ESTRATEGIA:
- Con last_mpage: abre mpage=1..N con un solo driver, extrae ~12 productos por página.
- Sin last_mpage: scroll incremental desde la URL base.

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
        title_el = item.select_one(".js-item-name, .item-name")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        product_url = None
        for sel in [
            "a.product-item-link", "a.item-link",
            "a.js-product-item-image-link-private",
            "a[href*='/productos/']", "a[href*='/product']"
        ]:
            link_el = item.select_one(sel)
            if link_el and link_el.get("href"):
                href = link_el["href"]
                product_url = href if href.startswith("http") else base_url + href
                break
        if not product_url:
            return None

        price = _extract_price_from_variants(item)
        if price is None:
            price_el = item.select_one(".js-price-display, .item-price, .price")
            if price_el:
                price = _parse_price(price_el.get_text(strip=True))

        image_url = None
        for img in item.find_all("img"):
            src = img.get("src") or ""
            if not src or src.startswith("data:") or "placeholder" in src:
                srcset = img.get("srcset") or img.get("data-srcset") or ""
                if srcset:
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


def scrape_with_selenium(catalog_url: str, last_mpage: int = None) -> list[dict]:
    """
    Scrapea una tienda Tienda Nube con Selenium.

    Con last_mpage: itera mpage=1..N con un solo driver (~12 productos por página).
    Sin last_mpage: scroll incremental desde la URL base.
    """
    base_url = _get_base_url(catalog_url)
    base_catalog = catalog_url.split("?")[0].rstrip("/") + "/"

    if last_mpage:
        logger.info(f"Iniciando Selenium iterando mpage=1..{last_mpage}: {base_catalog}")
        return _scrape_all_mpages_selenium(base_catalog, base_url, last_mpage)
    else:
        logger.info(f"Iniciando Selenium con scroll desde: {base_catalog}")
        return _scrape_with_scroll(base_catalog, base_url)


def _scrape_all_mpages_selenium(base_catalog: str, base_url: str, last_mpage: int) -> list[dict]:
    """
    Itera todas las mpage con un solo driver reutilizado.
    Cada página carga ~12 productos distintos con JS.
    """
    driver = _get_driver()
    all_products = []
    seen_ids = set()
    consecutive_empty = 0

    try:
        for page in range(1, last_mpage + 1):
            url = f"{base_catalog}?mpage={page}"
            logger.info(f"  Selenium mpage={page}/{last_mpage}")

            driver.get(url)

            # Esperar que carguen los productos de esta página
            try:
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".js-item-product"))
                )
            except Exception:
                logger.warning(f"  Timeout en mpage={page}, saltando")
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.warning("3 páginas seguidas sin respuesta, deteniendo")
                    break
                continue

            # Pequeña pausa para que termine el render
            time.sleep(1.5)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            items = soup.select(".js-item-product")

            if not items:
                consecutive_empty += 1
                logger.warning(f"  mpage={page}: sin items en el DOM")
                if consecutive_empty >= 3:
                    break
                continue

            consecutive_empty = 0
            new_count = 0
            for item in items:
                product = _extract_single(item, base_url)
                if not product:
                    continue
                key = product.get("external_id") or product.get("product_url")
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_products.append(product)
                    new_count += 1

            logger.info(f"  → {new_count} nuevos en mpage={page} (total: {len(all_products)})")

            # Si una página no aporta nada nuevo, probablemente llegamos al final
            if new_count == 0 and page > 1:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    logger.info(f"Sin productos nuevos en {consecutive_empty} páginas consecutivas, deteniendo")
                    break
            else:
                consecutive_empty = 0

    except Exception as e:
        logger.error(f"Error en Selenium mpage iteration: {e}")
    finally:
        driver.quit()

    logger.info(f"Total extraídos con Selenium: {len(all_products)}")
    return all_products


def _scrape_with_scroll(base_catalog: str, base_url: str) -> list[dict]:
    """Scroll incremental cuando no se conoce la cantidad de páginas."""
    driver = _get_driver()
    products = []

    try:
        driver.get(base_catalog)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".js-item-product"))
            )
        except Exception:
            logger.warning("Timeout esperando productos")

        # Scroll incremental
        last_count = 0
        no_change = 0
        while no_change < 4:
            items = driver.find_elements(By.CSS_SELECTOR, ".js-item-product")
            current = len(items)
            logger.info(f"  Scroll: {current} productos")
            if current > last_count:
                last_count = current
                no_change = 0
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", items[-1]
                    )
                    time.sleep(0.5)
                except Exception:
                    pass
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
            else:
                no_change += 1
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)

        logger.info(f"Scroll completo: {last_count} productos")

        soup = BeautifulSoup(driver.page_source, "html.parser")
        for item in soup.select(".js-item-product"):
            product = _extract_single(item, base_url)
            if product:
                products.append(product)

        logger.info(f"Total extraídos: {len(products)}")

    except Exception as e:
        logger.error(f"Error en Selenium scroll scraper: {e}")
    finally:
        driver.quit()

    return products