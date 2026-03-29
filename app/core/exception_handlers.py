"""
exception_handlers.py — Manejadores globales de excepciones para FastAPI.

Principio: nunca exponer detalles internos al cliente.
- El cliente recibe un mensaje genérico con un request_id para seguimiento.
- El error real se loguea completo (con traceback) internamente.
- Los HTTPException propios de la app se pasan tal cual (ya son intencionales).
- RateLimitExceeded devuelve 429 con el header Retry-After estándar.
"""
import uuid
import logging
import traceback

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Re-expone HTTPException con su status y detail originales.
    Estos son errores intencionales del dominio (404, 400, 401, 409…),
    no fugas de información interna.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Errores de validación de Pydantic (422).
    Se expone el detalle de validación porque es información del cliente,
    no del servidor.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """
    Respuesta estándar 429 para rate limiting.
    Incluye Retry-After para que clientes bien portados sepan cuándo reintentar.
    El límite exacto (ej: "5 per 1 minute") se incluye en el detail — no es
    información sensible, es parte del contrato público de la API.
    """
    logger.warning(
        "Rate limit superado | ip=%s | path=%s | limit=%s",
        request.client.host if request.client else "unknown",
        request.url.path,
        exc.detail,
    )
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": "60"},
        content={
            "detail": f"Demasiadas solicitudes: {exc.detail}. Intentá de nuevo en 60 segundos.",
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Captura cualquier excepción no manejada (500).

    - Genera un request_id único para correlacionar logs con reportes del cliente.
    - Loguea el traceback completo internamente.
    - Devuelve al cliente solo el request_id y un mensaje genérico.
    """
    request_id = str(uuid.uuid4())

    logger.error(
        "Unhandled exception | request_id=%s | %s %s\n%s",
        request_id,
        request.method,
        request.url,
        traceback.format_exc(),
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "Error interno del servidor. Contactá al administrador.",
            "request_id": request_id,
        },
    )