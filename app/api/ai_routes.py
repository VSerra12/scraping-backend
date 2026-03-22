"""
ai_routes.py – Endpoints para clasificación con IA (protegidos)
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.models import Product
from app.services.ai_classifier import classify_product
from app.api.auth import require_admin

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
def classify_single_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
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
        logger.error(f"Error clasificando producto {product_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ai_router.post("/classify-pending")
def classify_pending_products(
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
        {"id": p.id, "title": p.title, "description": p.description or ""}
        for p in pending
    ]
    cost = estimate_cost(len(products_data))
    background_tasks.add_task(_run_classification_batch, products_data, db)
    return {
        "message": "Clasificación iniciada en background",
        "products_to_classify": len(products_data),
        "cost_estimate": cost,
    }


def _run_classification_batch(products_data: list[dict], db: Session):
    import time
    success_count = 0
    for item in products_data:
        try:
            classification = classify_product(item["title"], item["description"])
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
            logger.error(f"Error clasificando producto {item['id']}: {e}")
            continue
    db.commit()
    logger.info(f"Batch completado: {success_count}/{len(products_data)} productos clasificados")


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