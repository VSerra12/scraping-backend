from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.models.models import Store, Product, SearchLog, ScrapeLog
from app.models.schemas import (
    StoreCreate, StoreRead,
    SearchRequest, SearchResponse,
    ScrapeResponse, StatsResponse,
    ScrapeLogRead, StoreEnrichStatus,
)
from app.services.scraping_service import scrape_and_save
from app.services.search_service import search_products
from app.services.enrichment_service import run_enrichment_job, get_enrichment_status
from app.api.ai_routes import ai_router
from app.api.auth import auth_router, require_admin

router = APIRouter()
router.include_router(auth_router)
router.include_router(ai_router)


# ─── Stores ────────────────────────────────────────────────────────────────────

@router.post("/stores", response_model=StoreRead, status_code=201, tags=["Tiendas"])
def create_store(store_data: StoreCreate, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    existing = db.query(Store).filter(Store.url == store_data.url).first()
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe una tienda con esa URL")
    store = Store(**store_data.model_dump())
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.get("/stores", response_model=list[StoreRead], tags=["Tiendas"])
def list_stores(location: str = None, db: Session = Depends(get_db)):
    query = db.query(Store)
    if location:
        term = f"%{location.lower()}%"
        query = query.filter(
            func.lower(Store.location).like(term) |
            func.lower(Store.country).like(term)
        )
    return query.all()


@router.get("/stores/{store_id}", response_model=StoreRead, tags=["Tiendas"])
def get_store(store_id: int, db: Session = Depends(get_db)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    return store


@router.delete("/stores/{store_id}", status_code=204, tags=["Tiendas"])
def delete_store(store_id: int, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    db.delete(store)
    db.commit()


@router.patch("/stores/{store_id}/toggle", response_model=StoreRead, tags=["Tiendas"])
def toggle_store_active(store_id: int, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    store.active = not store.active
    db.commit()
    db.refresh(store)
    return store


# ─── Scraping ──────────────────────────────────────────────────────────────────

@router.post("/scrape/{store_id}", response_model=ScrapeResponse, tags=["Scraping"])
def scrape_store(
    store_id: int,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    if not store.active:
        raise HTTPException(status_code=400, detail="La tienda está desactivada")
    return scrape_and_save(store, db)


@router.post("/scrape-all", response_model=list[ScrapeResponse], tags=["Scraping"])
def scrape_all_stores(
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    stores = db.query(Store).filter(Store.active == True).all()  # noqa: E712
    if not stores:
        raise HTTPException(status_code=404, detail="No hay tiendas activas")
    return [scrape_and_save(store, db) for store in stores]


@router.get(
    "/scrape-logs/{store_id}",
    response_model=list[ScrapeLogRead],
    tags=["Scraping"],
)
def get_scrape_logs(
    store_id: int,
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Devuelve los últimos N runs de scraping para una tienda (default 10).
    Público — el front lo usa para mostrar historial y detectar errores.
    """
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")

    logs = (
        db.query(ScrapeLog)
        .filter(ScrapeLog.store_id == store_id)
        .order_by(ScrapeLog.started_at.desc())
        .limit(limit)
        .all()
    )
    return logs


# ─── Enriquecimiento ───────────────────────────────────────────────────────────

@router.post("/enrich", tags=["Scraping"])
def enrich_products(
    batch_size: int = 20,
    store_id: int = None,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    if batch_size < 1 or batch_size > 100:
        raise HTTPException(status_code=400, detail="batch_size debe estar entre 1 y 100")
    result = run_enrichment_job(db, batch_size=batch_size, store_id=store_id)
    status = get_enrichment_status(db)
    return {
        "message": "Enriquecimiento completo",
        "enriched_this_run": result["enriched"],
        "failed_this_run": result["failed"],
        "status": status,
    }


@router.get(
    "/enrich/status",
    tags=["Scraping"],
)
def enrichment_status(db: Session = Depends(get_db)):
    """
    Público — el front lo usa para mostrar progreso de enriquecimiento.
    Incluye el último scrape log de cada tienda para que el front
    pueda mostrar estado del scraping sin un request extra.
    """
    global_status = get_enrichment_status(db)
    stores = db.query(Store).all()

    # Precarga el último log de cada tienda en una sola query
    latest_logs: dict[int, ScrapeLog] = {}
    subq = (
        db.query(
            ScrapeLog.store_id,
            func.max(ScrapeLog.started_at).label("max_started"),
        )
        .group_by(ScrapeLog.store_id)
        .subquery()
    )
    for log in (
        db.query(ScrapeLog)
        .join(subq, (ScrapeLog.store_id == subq.c.store_id) &
                    (ScrapeLog.started_at == subq.c.max_started))
        .all()
    ):
        latest_logs[log.store_id] = log

    by_store: list[StoreEnrichStatus] = []
    for store in stores:
        total      = db.query(Product).filter(Product.store_id == store.id).count()
        enriched   = db.query(Product).filter(Product.store_id == store.id, Product.enriched == True).count()       # noqa: E712
        classified = db.query(Product).filter(Product.store_id == store.id, Product.ai_classified == True).count()  # noqa: E712
        pending    = total - enriched
        last_log   = latest_logs.get(store.id)

        by_store.append(StoreEnrichStatus(
            store_id=store.id,
            store_name=store.name,
            total=total,
            enriched=enriched,
            classified=classified,
            pending=pending,
            percent=round((enriched / total * 100) if total > 0 else 0, 1),
            last_scrape=ScrapeLogRead.model_validate(last_log) if last_log else None,
        ))

    return {**global_status, "by_store": [s.model_dump() for s in by_store]}


@router.post("/enrich/{store_id}", tags=["Scraping"])
def enrich_store(
    store_id: int,
    batch_size: int = 20,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin),
):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    result = run_enrichment_job(db, batch_size=batch_size, store_id=store_id)
    status = get_enrichment_status(db)
    return {
        "message": f"Enriquecimiento de {store.name} completo",
        "enriched_this_run": result["enriched"],
        "failed_this_run": result["failed"],
        "status": status,
    }


# ─── Búsqueda ──────────────────────────────────────────────────────────────────

@router.post("/search", response_model=SearchResponse, tags=["Búsqueda"])
def search(request: SearchRequest, db: Session = Depends(get_db)):
    return search_products(request, db)


# ─── Estadísticas ──────────────────────────────────────────────────────────────

@router.get("/stats", response_model=StatsResponse, tags=["Estadísticas"])
def get_stats(db: Session = Depends(get_db)):
    total_stores        = db.query(Store).count()
    active_stores       = db.query(Store).filter(Store.active == True).count()         # noqa: E712
    total_products      = db.query(Product).count()
    classified_products = db.query(Product).filter(Product.ai_classified == True).count()  # noqa: E712
    enriched_products   = db.query(Product).filter(Product.enriched == True).count()   # noqa: E712
    pending_enrichment  = db.query(Product).filter(Product.enriched == False).count()  # noqa: E712
    total_searches      = db.query(SearchLog).count()
    return StatsResponse(
        total_stores=total_stores,
        active_stores=active_stores,
        total_products=total_products,
        classified_products=classified_products,
        enriched_products=enriched_products,
        pending_enrichment=pending_enrichment,
        total_searches=total_searches,
    )