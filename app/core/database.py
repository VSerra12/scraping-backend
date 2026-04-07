from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.core.base import Base  # noqa: F401

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    from app.models.models import Store, Product, SearchLog  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Índices con IF NOT EXISTS — evita errores si ya existen en la DB
    indexes = [
        "CREATE INDEX IF NOT EXISTS ix_products_category_gender ON products (category, gender)",
        "CREATE INDEX IF NOT EXISTS idx_products_store ON products (store_id)",
        "CREATE INDEX IF NOT EXISTS ix_products_enriched ON products (enriched, ai_classified)",
        "CREATE INDEX IF NOT EXISTS ix_products_cut ON products (cut)",
        "CREATE INDEX IF NOT EXISTS ix_products_pattern ON products (pattern)",
        "CREATE INDEX IF NOT EXISTS ix_scrape_logs_store_id ON scrape_logs (store_id)",
        "CREATE INDEX IF NOT EXISTS ix_scrape_logs_started_at ON scrape_logs (started_at)",
        "CREATE INDEX IF NOT EXISTS ix_scrape_logs_success ON scrape_logs (success)",
    ]

    with engine.connect() as conn:
        for sql in indexes:
            conn.execute(text(sql))
        conn.commit()