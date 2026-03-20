#!/bin/sh
# Start a local Prometheus that scrapes the FinChat app's /metrics, then Grafana.
# Grafana datasource points at http://127.0.0.1:9090 (see provisioning/datasources/prometheus.yml).
# Prometheus is best-effort: if it fails, Grafana still starts (panels may be empty).

set -e

BASE="${FINCHAT_APP_BASE_URL:-http://host.docker.internal:8080}"
# Normalize: strip trailing slash
BASE=$(echo "$BASE" | sed 's|/*$||')

case "$BASE" in
  https://*) SCHEME="https" ;;
  http://*)  SCHEME="http" ;;
  *)         SCHEME="http"; BASE="http://${BASE}" ;;
esac

HOST_PORT=$(echo "$BASE" | sed 's|.*://||' | cut -d/ -f1)
mkdir -p /tmp/prom-tsdb

# Cloud Run cold starts: allow long first scrape. Short TSDB retention caps RAM (single container).
SCRAPE_INTERVAL="${FINCHAT_PROMETHEUS_SCRAPE_INTERVAL:-30s}"
SCRAPE_TIMEOUT="${FINCHAT_PROMETHEUS_SCRAPE_TIMEOUT:-30s}"
RETENTION="${FINCHAT_PROMETHEUS_RETENTION:-4h}"

if [ "$SCHEME" = "https" ] && [ "${FINCHAT_PROMETHEUS_INSECURE_SKIP_VERIFY:-}" = "true" ]; then
  cat >/tmp/prom-finchat.yml <<EOF
global:
  scrape_interval: ${SCRAPE_INTERVAL}
  scrape_timeout: ${SCRAPE_TIMEOUT}
scrape_configs:
  - job_name: finchat
    scrape_interval: ${SCRAPE_INTERVAL}
    scrape_timeout: ${SCRAPE_TIMEOUT}
    scheme: https
    metrics_path: /metrics
    tls_config:
      insecure_skip_verify: true
    static_configs:
      - targets: ['${HOST_PORT}']
EOF
else
  cat >/tmp/prom-finchat.yml <<EOF
global:
  scrape_interval: ${SCRAPE_INTERVAL}
  scrape_timeout: ${SCRAPE_TIMEOUT}
scrape_configs:
  - job_name: finchat
    scrape_interval: ${SCRAPE_INTERVAL}
    scrape_timeout: ${SCRAPE_TIMEOUT}
    scheme: ${SCHEME}
    metrics_path: /metrics
    static_configs:
      - targets: ['${HOST_PORT}']
EOF
fi

if [ "${ENTRYPOINT_DEBUG:-}" = "1" ]; then
  echo "entrypoint: generated /tmp/prom-finchat.yml:" >&2
  sed 's/^/  /' /tmp/prom-finchat.yml >&2
fi

if [ -x /usr/local/bin/prometheus ]; then
  echo "entrypoint: Prometheus scraping ${SCHEME}://${HOST_PORT}/metrics -> Grafana datasource http://127.0.0.1:9090 (retention ${RETENTION})"
  /usr/local/bin/prometheus \
    --config.file=/tmp/prom-finchat.yml \
    --storage.tsdb.path=/tmp/prom-tsdb \
    --storage.tsdb.retention.time="${RETENTION}" \
    --web.listen-address=127.0.0.1:9090 \
    >/tmp/prometheus.log 2>&1 &
  PROM_PID=$!
  echo "entrypoint: Prometheus pid ${PROM_PID}"

  i=0
  while [ "$i" -lt 30 ]; do
    if kill -0 "$PROM_PID" 2>/dev/null; then
      if curl -sf "http://127.0.0.1:9090/-/ready" >/dev/null 2>&1; then
        echo "entrypoint: Prometheus ready"
        break
      fi
    else
      echo "entrypoint: ERROR Prometheus exited; last log lines:" >&2
      tail -n 40 /tmp/prometheus.log >&2 || true
      break
    fi
    i=$((i + 1))
    sleep 1
  done
else
  echo "entrypoint: WARN prometheus binary missing; starting Grafana only."
fi

exec /run.sh
