"""
Scheduler para scraping automático cada 24-48 horas
y enriquecimiento de productos cada 2 horas.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.core.config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="America/Argentina/Buenos_Aires")


def setup_scheduler():
    """Configura y arranca el scheduler."""

    def run_scrape_all():
        from app.core.database import SessionLocal
        from app.models.models import Store
        from app.services.scraping_service import scrape_and_save

        logger.info("⏰ Iniciando scraping automático programado...")
        db = SessionLocal()
        try:
            stores = db.query(Store).filter(Store.active == True).all()  # noqa: E712
            logger.info(f"Scrapeando {len(stores)} tiendas activas...")
            for store in stores:
                try:
                    result = scrape_and_save(store, db)
                    logger.info(
                        f"✓ {store.name}: {result.new_products} nuevos, "
                        f"{result.updated_products} actualizados"
                    )
                except Exception as e:
                    logger.error(f"✗ Error scrapeando {store.name}: {e}")
        finally:
            db.close()
        logger.info("✅ Scraping automático completado")

    def run_enrichment():
        from app.core.database import SessionLocal
        from app.services.enrichment_service import run_enrichment_job, get_enrichment_status

        db = SessionLocal()
        try:
            status = get_enrichment_status(db)
            if status["pending"] == 0:
                logger.info("⏰ Enriquecimiento: no hay productos pendientes, saltando.")
                return

            logger.info(f"⏰ Iniciando enriquecimiento automático ({status['pending']} pendientes)...")
            result = run_enrichment_job(db, batch_size=30)
            logger.info(
                f"✅ Enriquecimiento automático: {result['enriched']} OK, "
                f"{result['failed']} fallidos"
            )
        finally:
            db.close()

    # Scraping periódico
    scheduler.add_job(
        run_scrape_all,
        trigger=IntervalTrigger(hours=settings.SCRAPE_INTERVAL_HOURS),
        id="scrape_all_stores",
        name="Scraping periódico de todas las tiendas",
        replace_existing=True,
    )

    # Enriquecimiento cada 2 horas
    scheduler.add_job(
        run_enrichment,
        trigger=IntervalTrigger(hours=72),
        id="enrich_products",
        name="Enriquecimiento de productos pendientes",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        f"Scheduler iniciado. Scraping cada {settings.SCRAPE_INTERVAL_HOURS}h, "
        f"enriquecimiento cada 72h."
    )


def shutdown_scheduler():
    """Apaga el scheduler limpiamente."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler detenido")