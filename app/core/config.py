from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./fashion_search.db"

    # Scraping
    SCRAPING_DELAY_MIN: float = 1.0
    SCRAPING_DELAY_MAX: float = 2.0
    MAX_PAGES_PER_STORE: int = 20
    PRODUCTS_PER_PAGE: int = 50

    # IA
    AI_MODEL: str = "claude-sonnet-4-20250514"
    AI_MAX_TOKENS: int = 500
    AI_BATCH_SIZE: int = 10   # productos por llamada de clasificación

    # Búsqueda
    DEFAULT_SEARCH_LIMIT: int = 20
    MAX_SEARCH_LIMIT: int = 50

    # Scheduler (horas entre scraping automático)
    SCRAPE_INTERVAL_HOURS: int = 24

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
