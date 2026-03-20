# FinChat – Architecture & Operational Runbook

## Overview

FinChat is an AI-powered financial news chat application. Users submit natural-language
questions about recent stock news, and the system returns grounded, summarized responses
using Google Vertex AI (Gemini) with optional **Gemini API (AI Studio) fallback**
when Vertex model IDs are unavailable or quotas differ. See [docs/AI_SUMMARIZATION.md](docs/AI_SUMMARIZATION.md).

**JSON-first answers:** Chat retrieval ranks bundled `stock_news.json` by title/question overlap
and passes article **`full_text`** plus **source links** into the model. A *strict* prompt answers
only from that context; if the match is weak or the model signals insufficient coverage,
summarization escalates to a *general supplement* path. API responses and the web UI include
**`summarization_attribution`** (e.g. JSON-grounded vs supplemented, model label).

---

## Architecture Diagram

```
┌──────────────┐       HTTPS        ┌──────────────────────┐
│   Browser    │ ──────────────────► │  Cloud Run: App      │
│  (Chat UI)   │ ◄────────────────── │  FastAPI + Uvicorn   │
└──────────────┘                     │                      │
                                     │  ┌──────────────┐   │
                                     │  │ News Store    │   │  ┌──────────────────┐
                                     │  │ (in-memory)   │   │  │  Vertex AI       │
                                     │  └──────────────┘   │──►  Gemini Flash     │
                                     │  ┌──────────────┐   │  │  (Summarization)  │
                                     │  │ Prometheus    │   │  └──────────────────┘
                                     │  │ /metrics      │   │
                                     │  └──────┬───────┘   │
                                     └─────────┼───────────┘
                                               │ scrape
                                     ┌─────────▼───────────┐
                                     │  Cloud Run: Grafana  │
                                     │  Dashboard (4 Golden │
                                     │  Signals)            │
                                     └──────────────────────┘
```

## Components

### 1. FastAPI Application (`app/`)

| Module            | Responsibility                                      |
|-------------------|-----------------------------------------------------|
| `main.py`         | HTTP endpoints, middleware, lifespan management      |
| `vertex_client.py`| Summarization: Vertex AI, Gemini API, or **OpenRouter** |
| `openrouter_client.py` | OpenRouter HTTP API (`OPEN_ROUTER_API_KEY` env) |

**Cloud Run (OpenRouter):** set `OPEN_ROUTER_API_KEY`, `SUMMARIZATION_PROVIDER=openrouter`, and optional `OPENROUTER_MODEL` (default in code: `google/gemini-3-flash-preview`). CI uses `--update-env-vars` so console-set secrets/env merge with the deploy step.
| `news_store.py`   | In-memory article index; `search_json_priority()` for title-weighted JSON-first retrieval (`strong` / `weak` / `minimal` / `none`) |
| `schemas.py`      | Pydantic request/response models (OpenAPI/Swagger)   |
| `metrics.py`      | Prometheus counters, histograms, gauges              |
| `logging_config.py`| Structured JSON logging (Cloud Logging compatible)  |
| `tracing.py`      | OpenTelemetry tracing + FastAPI instrumentation      |
| `config.py`       | Env-based configuration via pydantic-settings        |
| `orm_models.py`   | SQLAlchemy models: `chat_sessions`, `chat_messages`   |
| `database.py`     | SQLite engine, `get_db` dependency, `init_db`           |
| `chat_repository.py` | CRUD for sessions/messages                         |
| `chat_storage_gcs.py` | Optional restore/backup of SQLite file to GCS     |

#### Chat sessions (SQLite + optional GCS)

- **Local / default**: SQLite file at `CHAT_SQLITE_PATH` (default `data/finchat_chat.sqlite3`). User accounts (username + passcode hash) and sessions are stored in SQLite; signed-in users get `session_id` on chat and can list threads via the session APIs.
- **Optional sign-in (default)**: `FINCHAT_REQUIRE_AUTH=false` — guests can use `POST /api/chat` without a cookie (no `session_id` returned); signing in restores saved chats. Set `FINCHAT_REQUIRE_AUTH=true` to require login before chat.
- **Bootstrap admin**: On startup, an `admin` user is created if missing; initial passcode from env `ADMIN_INITIAL_PASSCODE` (override in production via Secret Manager). Do not publish default credentials in the product UI.
- **APIs**: `POST /api/sessions`, `GET /api/sessions/{id}/messages`, `POST /api/chat` with optional `session_id`; responses include `session_id` when persistence is enabled and the caller is authenticated.
- **Disable**: `CHAT_SESSIONS_ENABLED=false` (no DB; session APIs return 503).
- **Disaster recovery / Cloud Run**: set `GCS_CHAT_DB_BUCKET`, `GCS_CHAT_DB_OBJECT`, `RESTORE_CHAT_DB_FROM_GCS=true` to download DB on startup; `BACKUP_CHAT_DB_ON_SHUTDOWN=true` uploads on shutdown (best-effort). Service account needs `storage.objectAdmin` (or narrower) on the bucket.

### 2. Grafana Dashboard (`grafana/`)

The Cloud Run Grafana image runs a **local Prometheus sidecar** (started by `grafana/entrypoint.sh`) that scrapes the **FinChat app’s `/metrics`** over HTTPS (`FINCHAT_APP_BASE_URL`). Grafana’s Prometheus datasource targets **`http://127.0.0.1:9090`** (the sidecar), which fixes “empty / broken panels” when the datasource incorrectly pointed at raw `/metrics` text.

After deploy, CI sets **`GF_SERVER_ROOT_URL`** to the Grafana service URL so static assets load correctly (avoids “failed to load application files”). The FinChat app receives **`GRAFANA_PUBLIC_URL`** and **`FINCHAT_APP_PUBLIC_URL`** for **admin traceability** links in the UI (`GET /api/admin/traceability`).

**Prototype:** Grafana uses standard UI login with **`admin` / `admin`** (set by CI/CD env vars). For production, use a strong password and Secret Manager — never ship default creds on a public URL.

Pre-provisioned dashboard covering the **Four Golden Signals**:

| Signal      | Metric(s)                                          | Panel Type  |
|-------------|---------------------------------------------------|-------------|
| **Latency** | `finchat_http_request_duration_seconds` (p50/p95/p99) | Time series |
|             | `finchat_vertex_ai_duration_seconds`               | Time series |
| **Traffic** | `finchat_http_requests_total` (rate)               | Time series |
|             | `finchat_chat_requests_total` by ticker            | Time series |
| **Errors**  | `finchat_errors_total` by type                     | Time series |
|             | 5xx error ratio percentage                         | Stat        |
| **Saturation** | `finchat_http_in_flight_requests`              | Time series |
|             | `finchat_news_articles_loaded` by ticker           | Bar gauge   |

### 3. CI/CD Pipeline (`.github/workflows/ci-cd.yml`)

```
push to main ─► lint (ruff) ─► test (pytest) ─► build (Docker) ─► deploy (Cloud Run)
```

- **Lint**: `ruff check` + `ruff format --check`
- **Test**: `pytest` with JUnit XML output
- **Build**: Multi-stage Docker builds for both app and Grafana
- **Deploy**: Cloud Run with Workload Identity Federation (no keys)

---

## Security Design

| Concern                | Approach                                            |
|------------------------|-----------------------------------------------------|
| No credentials in repo | Workload Identity Federation for CI/CD auth         |
| Secrets management     | Secret Manager (Gemini key, Grafana admin password) |
| Container security     | Non-root user, multi-stage build, slim base image   |
| API authentication     | Cloud Run IAM (optional); public for demo           |
| Network                | HTTPS-only (Cloud Run enforces TLS)                 |

---

## Alerting Suggestions

### Critical Alerts

1. **High Error Rate**
   ```promql
   sum(rate(finchat_errors_total[5m])) / sum(rate(finchat_http_requests_total[5m])) > 0.05
   ```
   *Fire if >5% of requests are failing over 5 minutes.*

2. **Vertex AI Latency Spike**
   ```promql
   histogram_quantile(0.95, sum(rate(finchat_vertex_ai_duration_seconds_bucket[5m])) by (le)) > 10
   ```
   *Fire if p95 Vertex AI latency exceeds 10 seconds.*

3. **Vertex AI Errors**
   ```promql
   sum(rate(finchat_vertex_ai_errors_total[5m])) > 0.1
   ```
   *Fire on sustained Vertex AI failures.*

### Warning Alerts

4. **High Saturation**
   ```promql
   finchat_http_in_flight_requests > 50
   ```
   *Warn if concurrent requests approach capacity.*

5. **Elevated Latency**
   ```promql
   histogram_quantile(0.95, sum(rate(finchat_http_request_duration_seconds_bucket[5m])) by (le)) > 2
   ```
   *Warn if p95 overall latency exceeds 2 seconds.*

### Notification Channels
- **PagerDuty** or **Opsgenie** for critical alerts (on-call rotation)
- **Slack** for warning-level alerts (#finchat-alerts channel)

---

## Key Trade-offs

### 1. In-Memory News Store vs. Database
**Chose**: In-memory JSON loading at startup.
**Why**: 138 articles is small enough to fit in memory. Eliminates database
dependency, simplifies deployment, and gives sub-millisecond search latency.
**Trade-off**: No persistence across restarts; can't dynamically update articles
without redeployment. For production scale, migrate to Cloud Firestore or
PostgreSQL with pgvector for semantic search.

### 2. Keyword Search vs. Vector/Semantic Search
**Chose**: Simple keyword matching with scoring.
**Why**: Fast to implement, zero additional infrastructure, works well for
ticker-specific queries. Vertex AI compensates by synthesizing from imperfect
matches.
**Trade-off**: Misses semantic relevance (e.g., "tech earnings" won't match
articles that don't contain those exact words). For production, add embeddings
via Vertex AI text-embedding model + vector similarity.

### 3. Prometheus Metrics Endpoint vs. Push-based Telemetry
**Chose**: `/metrics` endpoint scraped by Grafana (pull model).
**Why**: Standard Prometheus pattern; works with any scraper; no additional
infrastructure like an OTLP collector needed.
**Trade-off**: On Cloud Run (scale-to-zero), metrics are lost when instances
shut down. For production, push metrics to Google Cloud Monitoring or run a
managed Prometheus instance (GMP).

### 4. Grafana on Cloud Run vs. Managed Grafana
**Chose**: Self-hosted Grafana on Cloud Run.
**Why**: Full control over dashboards; free tier friendly; easy to version
control dashboard JSON.
**Trade-off**: No persistent storage for Grafana state; limited to
pre-provisioned dashboards. For production, use Grafana Cloud or Cloud
Monitoring dashboards.

### 5. Single Container vs. Microservices
**Chose**: Monolithic FastAPI application.
**Why**: Simple to deploy, test, and reason about for this scope. The chat
endpoint, news store, and metrics all share the same process.
**Trade-off**: Can't scale search and AI independently. For production,
separate the Vertex AI proxy into its own service with dedicated scaling.

### 6. Workload Identity Federation vs. Service Account Keys
**Chose**: WIF for CI/CD authentication.
**Why**: No long-lived credentials to manage or rotate. GitHub OIDC token is
exchanged for short-lived GCP credentials.
**Trade-off**: Slightly more complex initial setup, but dramatically better
security posture.

---

## Local Development

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Run locally
uvicorn app.main:app --reload --port 8080

# Lint
ruff check app/ tests/
ruff format app/ tests/
```

## API Documentation

- **Swagger UI**: `{APP_URL}/docs`
- **ReDoc**: `{APP_URL}/redoc`
- **OpenAPI JSON**: `{APP_URL}/openapi.json`
