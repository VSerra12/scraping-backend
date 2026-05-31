"""
Migración: agrega columna scraper_type a la tabla stores.

Ejecutar UNA VEZ en producción:
    python migration_add_scraper_type.py

O desde Python/shell en el servidor:
    from app.core.database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE stores ADD COLUMN scraper_type VARCHAR(20) NOT NULL DEFAULT 'auto'"))
        conn.commit()
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text

try:
    from app.core.database import engine
except ImportError:
    print("Ejecutar desde la raíz del proyecto: python migration_add_scraper_type.py")
    sys.exit(1)

with engine.connect() as conn:
    # PostgreSQL: consultar information_schema para ver si la columna ya existe
    result = conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'stores'
          AND column_name = 'scraper_type'
    """))
    exists = result.fetchone() is not None

    if exists:
        print("La columna scraper_type ya existe, nada que hacer.")
    else:
        conn.execute(text(
            "ALTER TABLE stores ADD COLUMN scraper_type VARCHAR(20) NOT NULL DEFAULT 'auto'"
        ))
        conn.commit()
        print("✓ Columna scraper_type agregada a stores (default: 'auto')")

print("Tiendas existentes tendrán scraper_type='auto' → detección automática como antes.")