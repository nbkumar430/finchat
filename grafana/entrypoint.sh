#!/bin/sh
# Start a local Prometheus that scrapes the FinChat app's /metrics (HTTPS), then Grafana.
# Grafana datasource points at http://127.0.0.1:9090 (see provisioning/datasources/prometheus.yml).

set -eu

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

cat >/tmp/prom-finchat.yml <<EOF
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: finchat
    scheme: ${SCHEME}
    metrics_path: /metrics
    static_configs:
      - targets: ['${HOST_PORT}']
EOF

echo "entrypoint: Prometheus scraping ${SCHEME}://${HOST_PORT}/metrics -> Grafana datasource http://127.0.0.1:9090"
/usr/local/bin/prometheus \
  --config.file=/tmp/prom-finchat.yml \
  --storage.tsdb.path=/tmp/prom-tsdb \
  --web.listen-address=127.0.0.1:9090 \
  >/tmp/prometheus.log 2>&1 &

exec /run.sh
