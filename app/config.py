"""Application configuration loaded from environment variables."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with sensible defaults for Cloud Run."""

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "project-ede0958a-eb5c-4225-94d")
    gcp_region: str = os.getenv("GCP_REGION", "us-central1")
    vertex_model: str = os.getenv("VERTEX_MODEL", "gemini-2.0-flash")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # App
    app_name: str = "FinChat"
    app_version: str = "1.0.0"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    port: int = int(os.getenv("PORT", "8080"))

    # Paths
    news_json_path: str = os.getenv("NEWS_JSON_PATH", "app/stock_news.json")

    # Observability
    otel_service_name: str = "finchat"
    enable_tracing: bool = os.getenv("ENABLE_TRACING", "true").lower() == "true"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
