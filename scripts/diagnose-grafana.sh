#!/usr/bin/env bash
# FinChat Grafana on Cloud Run — quick diagnostics (run with gcloud authenticated).
# Usage:
#   export PROJECT_ID=your-project REGION=us-central1
#   ./scripts/diagnose-grafana.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-project-ede0958a-eb5c-4225-94d}"
REGION="${REGION:-us-central1}"
SERVICE="${GRAFANA_SERVICE:-finchat-grafana}"
APP_SERVICE="${FINCHAT_APP_SERVICE:-finchat-app}"

echo "=== Cloud Run: ${SERVICE} (${REGION}) ==="
gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" \
  --format 'table(status.url,status.latestReadyRevisionName,spec.template.spec.containers[0].resources.limits.memory)'

echo ""
echo "=== Grafana env (subset from YAML) ==="
gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" \
  --format yaml | grep -E 'FINCHAT_APP_BASE_URL|GF_SERVER_ROOT_URL|GF_SECURITY_ADMIN_USER' || true

GRAFANA_URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format 'value(status.url)')
APP_URL=$(gcloud run services describe "${APP_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format 'value(status.url)' 2>/dev/null || echo "")

echo ""
echo "=== HTTP checks ==="
if [[ -n "${GRAFANA_URL}" ]]; then
  echo "Grafana public health: ${GRAFANA_URL}/api/health"
  curl -sS -o /tmp/graf-health.json -w "HTTP %{http_code}\n" "${GRAFANA_URL}/api/health" || true
  head -c 500 /tmp/graf-health.json 2>/dev/null | sed 's/^/  /' || true
  echo ""
fi
if [[ -n "${APP_URL}" ]]; then
  echo "FinChat /metrics (what Prometheus scrapes): ${APP_URL}/metrics"
  curl -sS -o /dev/null -w "HTTP %{http_code}\n" "${APP_URL}/metrics" || true
fi

echo ""
echo "=== Recent Grafana revision logs (last 15m, errors/warnings) ==="
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE} AND severity>=WARNING" \
  --project="${PROJECT_ID}" --limit=25 --format='table(timestamp,severity,textPayload)' \
  --freshness=15m 2>/dev/null || echo "(no logs or logging API unavailable)"

echo ""
echo "Tip: If UI assets 404, set GF_SERVER_ROOT_URL to the exact public URL with trailing slash:"
echo "  ${GRAFANA_URL%/}/"
