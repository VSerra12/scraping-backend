import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import create_tables
from app.core.scheduler import setup_scheduler, shutdown_scheduler
from app.api.routes import router

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización y limpieza al arrancar/apagar la app."""
    # Startup
    logger.info("🚀 Iniciando Fashion Search API...")
    create_tables()
    logger.info("✅ Base de datos lista")
    setup_scheduler()

    yield  # La app corre aquí

    # Shutdown
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

# CORS — solo localhost en desarrollo
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

# Registrar rutas
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
