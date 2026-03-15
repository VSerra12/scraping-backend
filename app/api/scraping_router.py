"""
Router de scraping.
Endpoints para disparar scraping manual y enriquecimiento de productos.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.models import Store
from app.services.scraping_service import scrape_and_save
from app.services.enrichment_service import run_enrichment_job, get_enrichment_status

router = APIRouter()


@router.post("/scrape/{store_id}", tags=["Scraping"])
def scrape_store(store_id: int, db: Session = Depends(get_db)):
    """
    Scrapea una tienda específica y guarda los productos nuevos.
    Los productos quedan con enriched=False, pendientes de clasificación por IA.
    """
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    if not store.active:
        raise HTTPException(status_code=400, detail="La tienda está inactiva")

    result = scrape_and_save(store, db)
    return result


@router.post("/scrape-all", tags=["Scraping"])
def scrape_all(db: Session = Depends(get_db)):
    """
    Scrapea todas las tiendas activas.
    Los productos nuevos quedan pendientes de enriquecimiento.
    """
    stores = db.query(Store).filter(Store.active == True).all()  # noqa: E712
    if not stores:
        raise HTTPException(status_code=404, detail="No hay tiendas activas")

    results = []
    total_new = 0
    total_updated = 0

    for store in stores:
        result = scrape_and_save(store, db)
        results.append(result)
        total_new += result.new_products
        total_updated += result.updated_products

    return {
        "stores_scraped": len(stores),
        "total_new_products": total_new,
        "total_updated_products": total_updated,
        "details": results,
    }


@router.post("/enrich", tags=["Scraping"])
def enrich_products(
    batch_size: int = 20,
    db: Session = Depends(get_db),
):
    """
    Enriquece productos pendientes visitando sus páginas individuales.
    Extrae descripción completa, materiales y talles, luego clasifica con IA.

    - batch_size: cuántos productos procesar en esta llamada (default 20, máx recomendado 50)
    - Llamar varias veces hasta que /enrich/status muestre pending=0
    """
    if batch_size < 1 or batch_size > 100:
        raise HTTPException(status_code=400, detail="batch_size debe estar entre 1 y 100")

    result = run_enrichment_job(db, batch_size=batch_size)
    status = get_enrichment_status(db)

    return {
        "message": "Enriquecimiento completo",
        "enriched_this_run": result["enriched"],
        "failed_this_run": result["failed"],
        "status": status,
    }


@router.get("/enrich/status", tags=["Scraping"])
def enrichment_status(db: Session = Depends(get_db)):
    """
    Estado del proceso de enriquecimiento.
    Muestra cuántos productos tienen enriquecimiento pendiente.
    """
    return get_enrichment_status(db)
