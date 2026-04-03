"""
ai_routes.py – Endpoints para clasificación con IA (protegidos).

Política de errores:
- Los errores internos se loguean completos con logger.error().
- Al cliente solo llega un mensaje genérico + request_id para seguimiento.
- Nunca se expone str(e), stack traces ni detalles de implementación.

Rate limiting (por IP, via slowapi):
- /ai/classify/{id}      → 20 req/min
- /ai/classify-pending   → 5 req/min  (batch costoso)
"""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db, SessionLocal
from app.models.models import Product
from app.services.ai_classifier import classify_product
from app.api.auth import require_admin
from app.core.rate_limiter import limiter, LIMIT_AI_CLASSIFY, LIMIT_AI_BATCH

logger = logging.getLogger(__name__)
ai_router = APIRouter(prefix="/ai", tags=["IA"])


def estimate_cost(product_count: int) -> dict:
    cost_per_product = 0.0005
    return {
        "product_count": product_count,
        "estimated_cost_usd": round(product_count * cost_per_product, 4),
        "estimated_cost_ars": round(product_count * cost_per_product * 1000, 2),
    }


@ai_router.post("/classify/{product_id}")
@limiter.limit(LIMIT_AI_CLASSIFY)
def classify_single_product(
    request: Request,                          # requerido por slowapi
    product_id: int,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    request_id = str(uuid.uuid4())
    try:
        classification = classify_product(product.title, product.description or "")
        product.category      = classification["category"]
        product.subcategory   = classification["subcategory"]
        product.colors        = classification["colors"]
        product.style_tags    = classification["style_tags"]
        product.gender        = classification["gender"]
        product.ai_classified = True
        db.commit()
        db.refresh(product)
        return {"success": True, "product_id": product_id, "classification": classification}

    except Exception as e:
        db.rollback()
        logger.error(
            "Error clasificando producto | request_id=%s | product_id=%d | %s",
            request_id, product_id, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Error interno al clasificar el producto.",
                "request_id": request_id,
            },
        )


@ai_router.post("/classify-pending")
@limiter.limit(LIMIT_AI_BATCH)
def classify_pending_products(
    request: Request,                          # requerido por slowapi
    background_tasks: BackgroundTasks,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    pending = db.scalars(
        select(Product).where(Product.ai_classified == False).limit(limit)  # noqa: E712
    ).all()
    if not pending:
        return {"message": "No hay productos pendientes de clasificación", "count": 0}

    products_data = [
        {
            "id": p.id,
            "title": p.title,
            "description": p.description or "",
            "image_url": p.image_url,       # ← agregado
        }
        for p in pending
    ]
    cost = estimate_cost(len(products_data))
    background_tasks.add_task(_run_classification_batch, products_data)  # sin db
    return {
        "message": "Clasificación iniciada en background",
        "products_to_classify": len(products_data),
        "cost_estimate": cost,
    }


def _run_classification_batch(products_data: list[dict]):  # ← sin db en parámetro
    import time
    db = SessionLocal()  # sesión propia, independiente del request
    success_count = 0
    try:
        for item in products_data:
            try:
                classification = classify_product(
                    item["title"],
                    item["description"],
                    image_url=item.get("image_url"),   # ← agregado
                )
                product = db.get(Product, item["id"])
                if not product:
                    continue
                product.category      = classification["category"]
                product.subcategory   = classification["subcategory"]
                product.colors        = classification["colors"]
                product.style_tags    = classification["style_tags"]
                product.gender        = classification["gender"]
                product.ai_classified = True
                success_count += 1
                time.sleep(0.3)
            except Exception as e:
                logger.error(
                    "Error clasificando producto en batch | product_id=%d | %s",
                    item["id"], e, exc_info=True,
                )
                db.rollback()  # limpia estado sucio para que el siguiente producto pueda continuar
                continue
        db.commit()
        logger.info(
            "Batch completado: %d/%d productos clasificados",
            success_count, len(products_data),
        )
    except Exception as e:
        db.rollback()
        logger.error("Error fatal en batch | %s", e, exc_info=True)
    finally:
        db.close()  # siempre se libera la conexión


@ai_router.get("/stats")
def ai_stats(db: Session = Depends(get_db)):
    """Público — muestra progreso general."""
    total      = db.query(Product).count()
    classified = db.query(Product).filter(Product.ai_classified == True).count()  # noqa: E712
    pending    = total - classified
    return {
        "total_products":        total,
        "classified":            classified,
        "pending":               pending,
        "classification_rate":   f"{(classified/total*100):.1f}%" if total > 0 else "0%",
        "cost_estimate_pending": estimate_cost(pending),
    }


@ai_router.get("/estimate")
def cost_estimate(product_count: int = 100, _: dict = Depends(require_admin)):
    return estimate_cost(product_count)