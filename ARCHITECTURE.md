# FinChat – Architecture & Operational Runbook

## Overview

FinChat is an AI-powered financial news chat application. Users submit natural-language
questions about recent stock news, and the system returns grounded, summarized responses
using Google Vertex AI (Gemini 2.0 Flash).

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
| `vertex_client.py`| Vertex AI Gemini integration for summarization       |
| `news_store.py`   | In-memory article index with keyword search          |
| `schemas.py`      | Pydantic request/response models (OpenAPI/Swagger)   |
| `metrics.py`      | Prometheus counters, histograms, gauges              |
| `logging_config.py`| Structured JSON logging (Cloud Logging compatible)  |
| `config.py`       | Env-based configuration via pydantic-settings        |

### 2. Grafana Dashboard (`grafana/`)

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
| Secrets management     | Grafana password via GitHub Secrets → Cloud Run env |
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
