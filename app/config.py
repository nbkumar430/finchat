"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with sensible defaults for Cloud Run."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "project-ede0958a-eb5c-4225-94d")
    gcp_region: str = os.getenv("GCP_REGION", "us-central1")
    # Default to a widely available Vertex ID; override with VERTEX_MODEL when your project has a specific catalog entry.
    vertex_model: str = os.getenv("VERTEX_MODEL", "gemini-2.0-flash")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    use_vertex_ai: bool = os.getenv("USE_VERTEX_AI", "true").lower() == "true"
    # After all Vertex model IDs fail (e.g. 404), retry using Gemini Developer API if GEMINI_API_KEY is set.
    vertex_fallback_to_api_key: bool = os.getenv("VERTEX_FALLBACK_TO_API_KEY", "true").lower() == "true"
    # summarization_provider: vertex (default) | openrouter | gemini_api
    summarization_provider: str = os.getenv("SUMMARIZATION_PROVIDER", "vertex").strip().lower()
    # OpenRouter (https://openrouter.ai/) — set OPEN_ROUTER_API_KEY on Cloud Run (legacy: OPENROUTER_API_KEY)
    open_router_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPEN_ROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "google/gemini-3-flash-preview")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_http_referer: str = os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/finchat")
    openrouter_app_title: str = os.getenv("OPENROUTER_APP_TITLE", "FinChat")
    openrouter_temperature: float = float(os.getenv("OPENROUTER_TEMPERATURE", "0.3"))
    openrouter_max_output_tokens: int = int(os.getenv("OPENROUTER_MAX_OUTPUT_TOKENS", "768"))
    openrouter_timeout_seconds: float = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "90"))
    gemini_api_fallback_models: str = os.getenv(
        "GEMINI_API_FALLBACK_MODELS",
        "gemini-2.0-flash,gemini-2.0-flash-lite,gemini-1.5-flash",
    )
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

    # Chat persistence (SQLite; optional GCS backup/restore for Cloud Run / DR)
    chat_sessions_enabled: bool = Field(default=True, validation_alias="CHAT_SESSIONS_ENABLED")
    chat_sqlite_path: str = Field(default="data/finchat_chat.sqlite3", validation_alias="CHAT_SQLITE_PATH")
    gcs_chat_db_bucket: str = os.getenv("GCS_CHAT_DB_BUCKET", "")
    gcs_chat_db_object: str = os.getenv("GCS_CHAT_DB_OBJECT", "finchat_chat.sqlite3")
    restore_chat_db_from_gcs: bool = os.getenv("RESTORE_CHAT_DB_FROM_GCS", "false").lower() == "true"
    backup_chat_db_on_shutdown: bool = os.getenv("BACKUP_CHAT_DB_ON_SHUTDOWN", "false").lower() == "true"

    # Auth: require_auth=true forces login before chat. Default false = optional sign-in (chat works
    # without account; sign in to persist chats in SQLite). Admin user is seeded on startup (passcode from ADMIN_INITIAL_PASSCODE).
    require_auth: bool = Field(default=False, validation_alias="FINCHAT_REQUIRE_AUTH")
    auth_secret: str = Field(
        default="change-me-in-production-finchat-auth-secret",
        validation_alias="FINCHAT_AUTH_SECRET",
    )
    auth_cookie_name: str = Field(default="finchat_auth", validation_alias="FINCHAT_AUTH_COOKIE")
    auth_token_max_age_seconds: int = Field(default=7 * 24 * 3600, validation_alias="FINCHAT_AUTH_MAX_AGE_SECONDS")
    admin_initial_passcode: str = Field(default="change-me-finchat-admin-2026", validation_alias="ADMIN_INITIAL_PASSCODE")
    auth_cookie_secure: bool = Field(default=False, validation_alias="FINCHAT_AUTH_COOKIE_SECURE")

    # Public URLs for admin traceability (set on Cloud Run after deploy; see CI/CD)
    grafana_public_url: str = Field(default="", validation_alias="GRAFANA_PUBLIC_URL")
    app_public_base_url: str = Field(default="", validation_alias="FINCHAT_APP_PUBLIC_URL")
    grafana_golden_signals_uid: str = Field(
        default="finchat-golden-signals",
        validation_alias="GRAFANA_GOLDEN_SIGNALS_UID",
    )

    # Observability
    otel_service_name: str = "finchat"
    enable_tracing: bool = os.getenv("ENABLE_TRACING", "true").lower() == "true"
    otlp_endpoint: str = os.getenv("OTLP_ENDPOINT", "")

    @model_validator(mode="after")
    def _normalize_public_urls(self):
        self.grafana_public_url = (self.grafana_public_url or "").rstrip("/")
        self.app_public_base_url = (self.app_public_base_url or "").rstrip("/")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
