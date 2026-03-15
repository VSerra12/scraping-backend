# 🛍️ Fashion Search — Backend Python

Backend de búsqueda inteligente de ropa en tiendas argentinas.

## Arquitectura

```
backend/
├── main.py                          # Entrypoint FastAPI
├── requirements.txt
├── README.md
└── app/
    ├── api/
    │   ├── ai_routes.py             # Endpoints para IA (clasificación)
    │   ├── routes.py                # Endpoints principales REST
    │   └── scraping_router.py       # Endpoints para scraping
    ├── core/
    │   ├── base.py                  # Base para modelos
    │   ├── config.py                # Settings desde .env
    │   ├── database.py              # SQLAlchemy engine + sesiones
    │   └── scheduler.py             # APScheduler para scraping periódico
    ├── models/
    │   ├── models.py                # Tablas: Store, Product, etc.
    │   └── schemas.py               # Schemas Pydantic (request/response)
    ├── scrapers/
    │   ├── base_scraper.py          # Scraper genérico + utilidades
    │   ├── tiendanube_scraper.py    # Scraper específico para Tiendanube
    │   ├── tiendanube_selenium_scraper.py  # Scraper con Selenium para Tiendanube
    │   └── woocommerce_scraper.py   # Scraper para WooCommerce
    │   
    └── services/
        ├── ai_classifier.py         # Clasificación con Claude API
        ├── enrichment_service.py    # Servicio de enriquecimiento de datos
        ├── scraping_service.py      # Orquestador de scraping + guardado
        └── search_service.py        # Lógica de búsqueda en DB
```

## Setup

### 1. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env y poner tu ANTHROPIC_API_KEY
```

### 3. Arrancar el servidor

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

La API estará disponible en:
- **Docs interactivos**: http://localhost:8000/docs
- **API**: http://localhost:8000/api

---

## Flujo de uso

### Opción A: Con datos mock (para probar rápido)

```bash
python seed_mock_products.py
```

Luego buscá:
```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "remera negra oriental"}'
```

### Opción B: Con scraping real

```bash
# 1. Cargar tiendas
python seed_stores.py

# 2. Scrapear una tienda (reemplazar 1 con el ID)
curl -X POST http://localhost:8000/api/scrape/1

# 3. O scrapear todas
curl -X POST http://localhost:8000/api/scrape-all
```

---

## Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/stores` | Listar tiendas |
| `POST` | `/api/stores` | Agregar tienda |
| `DELETE` | `/api/stores/{id}` | Eliminar tienda |
| `PATCH` | `/api/stores/{id}/toggle` | Activar/desactivar tienda |
| `POST` | `/api/scrape/{store_id}` | Scrapear tienda específica |
| `POST` | `/api/scrape-all` | Scrapear todas las tiendas activas |
| `POST` | `/api/search` | Buscar productos |
| `GET` | `/api/stats` | Estadísticas del sistema |

### Ejemplo de búsqueda con filtros

```json
POST /api/search
{
  "query": "buzo negro deportivo",
  "limit": 20,
  "category": "buzo",
  "min_price": 10000,
  "max_price": 50000,
  "gender": "unisex",
  "color": "negro"
}
```

---

## Scraper personalizado para una tienda específica

Si el scraper genérico no funciona bien con alguna tienda, podés crear uno específico:

```python
# app/scrapers/mi_tienda_scraper.py
from app.scrapers.base_scraper import GenericScraper

class MiTiendaScraper(GenericScraper):
    COMMON_PRODUCT_SELECTORS = [".mi-selector-especifico"]
    TITLE_SELECTORS = [".mi-titulo"]
    PRICE_SELECTORS = [".mi-precio"]

    def _get_next_page(self, soup, base_url, current_page):
        # Lógica de paginación específica
        ...
```

---

## Costos IA estimados

- Clasificación: 1 llamada a Claude por producto nuevo
- ~500 tokens por clasificación → ~$0.01 por 100 productos
- 2,000-5,000 productos iniciales → $0.20-$0.50
- Mantenimiento diario (solo productos nuevos) → mínimo

---

## Variables de entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | API key de Anthropic | requerida |
| `DATABASE_URL` | URL de la DB | `sqlite:///./fashion_search.db` |
| `SCRAPE_INTERVAL_HOURS` | Horas entre scraping auto | `24` |
| `MAX_PAGES_PER_STORE` | Páginas máx por tienda | `5` |
| `SCRAPING_DELAY_MIN` | Pausa mínima entre requests (seg) | `1.0` |
| `SCRAPING_DELAY_MAX` | Pausa máxima entre requests (seg) | `2.0` |
