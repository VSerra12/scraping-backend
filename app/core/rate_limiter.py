"""
rate_limiter.py — Configuración centralizada de rate limiting con slowapi.

Backend:
- Desarrollo/default: memoria (no persiste entre reinicios, no comparte entre workers).
- Producción recomendada: Redis.
  Setear REDIS_URL en .env, ej: REDIS_URL=redis://localhost:6379/0

Límites aplicados (por IP):
  /enrich            POST  → 10 req/min  (llama a Anthropic por cada producto)
  /enrich/{id}       POST  → 10 req/min
  /scrape-all        POST  → 5 req/min   (operación costosa en red y CPU)
  /scrape/{id}       POST  → 10 req/min
  /ai/classify-pending POST → 5 req/min  (batch de llamadas a Anthropic)
  /ai/classify/{id}  POST  → 20 req/min  (llamada individual)

Para endpoints públicos de lectura no se aplica rate limiting — no hay
costo en IA ni riesgo de abuso significativo con los datos actuales.
"""
import os
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

# ── Backend de almacenamiento ─────────────────────────────────────────────────
# slowapi usa la librería `limits` internamente.
# URI de memoria:  "memory://"
# URI de Redis:    "redis://host:port/db"  (requiere pip install redis)

_redis_url = os.getenv("REDIS_URL", "")

if _redis_url:
    storage_uri = _redis_url
    logger.info("Rate limiter: usando Redis (%s)", _redis_url.split("@")[-1])  # oculta credenciales
else:
    storage_uri = "memory://"
    logger.warning(
        "Rate limiter: usando memoria in-process. "
        "En producción con múltiples workers setear REDIS_URL en .env."
    )

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=storage_uri,
    # Respuesta cuando se supera el límite — se sobreescribe en el handler global
    # pero slowapi necesita este default para el middleware.
    default_limits=[],
)

# ── Strings de límite reutilizables ──────────────────────────────────────────
# Centralizar aquí para poder ajustar sin tocar cada router.

LIMIT_ENRICH       = "10/minute"   # /enrich y /enrich/{id}
LIMIT_SCRAPE       = "10/minute"   # /scrape/{id}
LIMIT_SCRAPE_ALL   = "5/minute"    # /scrape-all
LIMIT_AI_CLASSIFY  = "20/minute"   # /ai/classify/{id}
LIMIT_AI_BATCH     = "5/minute"    # /ai/classify-pending
