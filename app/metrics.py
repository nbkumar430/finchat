"""Prometheus metrics for the four golden signals.

Golden Signals:
  1. Latency   – request duration histogram
  2. Traffic   – request count by endpoint/method
  3. Errors    – error count by type/status
  4. Saturation – in-flight requests gauge
"""

from prometheus_client import Counter, Gauge, Histogram, Info

# ── App info ─────────────────────────────────────────────────────────
APP_INFO = Info("finchat_app", "Application metadata")

# ── 1. Latency ───────────────────────────────────────────────────────
REQUEST_DURATION = Histogram(
    "finchat_http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=["method", "endpoint", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

VERTEX_LATENCY = Histogram(
    "finchat_vertex_ai_duration_seconds",
    "Vertex AI API call latency in seconds",
    labelnames=["model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ── 2. Traffic ───────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "finchat_http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status"],
)

CHAT_REQUESTS = Counter(
    "finchat_chat_requests_total",
    "Total chat requests processed",
    labelnames=["ticker"],
)

# ── 3. Errors ────────────────────────────────────────────────────────
ERROR_COUNT = Counter(
    "finchat_errors_total",
    "Total errors",
    labelnames=["type", "endpoint"],
)

VERTEX_ERRORS = Counter(
    "finchat_vertex_ai_errors_total",
    "Vertex AI call failures",
    labelnames=["model", "error_type"],
)

# ── 4. Saturation ────────────────────────────────────────────────────
IN_FLIGHT = Gauge(
    "finchat_http_in_flight_requests",
    "Number of HTTP requests currently being processed",
)

NEWS_ARTICLES_LOADED = Gauge(
    "finchat_news_articles_loaded",
    "Number of news articles loaded in memory",
    labelnames=["ticker"],
)
