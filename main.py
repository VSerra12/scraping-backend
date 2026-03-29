import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler  # noqa: F401 — no se usa, ver nota abajo
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.core.database import create_tables
from app.core.scheduler import setup_scheduler, shutdown_scheduler
from app.core.rate_limiter import limiter
from app.core.exception_handlers import (
    http_exception_handler,
    validation_exception_handler,
    rate_limit_exceeded_handler,
    unhandled_exception_handler,
)
from app.api.routes import router

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# NOTA DE SEGURIDAD: solo se registra `router` (app/api/routes.py).
# Ese router ya incluye auth_router y ai_router internamente.
# NO montar scraping_router ni ningún router adicional sin require_admin.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización y limpieza al arrancar/apagar la app."""
    logger.info("🚀 Iniciando Fashion Search API...")
    create_tables()
    logger.info("✅ Base de datos lista")
    setup_scheduler()

    yield  # La app corre aquí

    logger.info("🛑 Apagando Fashion Search API...")
    shutdown_scheduler()


app = FastAPI(
    title="Fashion Search API",
    description=(
        "API para búsqueda inteligente de ropa en tiendas argentinas. "
        "Scrapea múltiples tiendas, clasifica productos con IA y permite "
        "búsqueda por descripciones naturales."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Rate limiter ──────────────────────────────────────────────────────────────
# El limiter necesita estar en app.state para que el middleware lo encuentre.
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# ── Exception handlers globales ───────────────────────────────────────────────
# Orden importa:
# 1. RateLimitExceeded  → 429 con Retry-After (antes que el catch-all de Exception)
# 2. HTTPException      → errores de dominio intencionales (404, 401, 409…)
# 3. RequestValidationError → errores de input del cliente (422)
# 4. Exception          → catch-all para 500 no manejados (siempre al final)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# CORS — ajustar orígenes en producción, nunca usar allow_origins=["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",  # Vite dev server
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Único router — todos los endpoints protegidos están aquí
app.include_router(router, prefix="/api")


@app.get("/", tags=["Health"])
def root():
    return {
        "status": "ok",
        "message": "Fashion Search API corriendo 🛍️",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}