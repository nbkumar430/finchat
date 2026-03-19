"""Application configuration loaded from environment variables."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with sensible defaults for Cloud Run."""

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "project-ede0958a-eb5c-4225-94d")
    gcp_region: str = os.getenv("GCP_REGION", "us-central1")
    vertex_model: str = os.getenv("VERTEX_MODEL", "gemini-3.1-flash-lite-preview")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    use_vertex_ai: bool = os.getenv("USE_VERTEX_AI", "true").lower() == "true"
    vertex_fallback_models: str = os.getenv(
        "VERTEX_FALLBACK_MODELS",
        "gemini-2.0-flash-001,gemini-2.0-flash,gemini-2.0-flash-lite-001,gemini-1.5-flash-002,gemini-1.5-flash",
    )
    vertex_max_retries: int = int(os.getenv("VERTEX_MAX_RETRIES", "3"))
    vertex_retry_base_seconds: float = float(os.getenv("VERTEX_RETRY_BASE_SECONDS", "0.8"))
    vertex_max_in_flight: int = int(os.getenv("VERTEX_MAX_IN_FLIGHT", "8"))
    vertex_circuit_threshold: int = int(os.getenv("VERTEX_CIRCUIT_THRESHOLD", "4"))
    vertex_circuit_cooldown_seconds: int = int(os.getenv("VERTEX_CIRCUIT_COOLDOWN_SECONDS", "45"))
    summary_cache_ttl_seconds: int = int(os.getenv("SUMMARY_CACHE_TTL_SECONDS", "120"))
    summary_cache_max_entries: int = int(os.getenv("SUMMARY_CACHE_MAX_ENTRIES", "512"))

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
    otlp_endpoint: str = os.getenv("OTLP_ENDPOINT", "")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
