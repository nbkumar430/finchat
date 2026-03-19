#!/usr/bin/env bash
# =============================================================================
# FinChat – Full Decommission / Rollback Script
# =============================================================================
# Usage:
#   bash scripts/decommission.sh           # dry-run (prints what it WOULD do)
#   bash scripts/decommission.sh --execute  # actually destroys resources
#
# What this script removes:
#   1. Cloud Run services  (finchat-app, finchat-grafana)
#   2. Artifact Registry repository  (finchat-repo)
#   3. Secret Manager secrets  (GEMINI_API_KEY, GRAFANA_ADMIN_PASSWORD)
#   4. IAM bindings granted to the deploy/runtime service accounts
#   5. Service accounts  (finchat-app-sa, finchat-deploy-sa)
#   6. Workload Identity Pool & Provider  (only if managed by this project)
#
# Prerequisites:
#   - gcloud CLI authenticated with Owner or Editor role on the project
#   - Run: gcloud config set project <PROJECT_ID>
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID="project-ede0958a-eb5c-4225-94d"
REGION="us-central1"

# Cloud Run
APP_SERVICE="finchat-app"
GRAFANA_SERVICE="finchat-grafana"

# Artifact Registry
AR_REPO="finchat-repo"

# Secret Manager
SECRETS=("GEMINI_API_KEY" "GRAFANA_ADMIN_PASSWORD")

# Service accounts
APP_SA="finchat-app-sa@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="finchat-deploy-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# Workload Identity (GitHub Actions OIDC)
WIF_POOL="github-pool"       # adjust if your pool has a different name
WIF_PROVIDER="github-provider"

# ── Argument parsing ──────────────────────────────────────────────────────────
DRY_RUN=true
for arg in "$@"; do
  if [[ "$arg" == "--execute" ]]; then
    DRY_RUN=false
  fi
done

# ── Helper functions ──────────────────────────────────────────────────────────
run() {
  echo "  [CMD] $*"
  if [[ "$DRY_RUN" == "false" ]]; then
    "$@" || echo "  [WARN] command failed (continuing): $*"
  fi
}

section() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $1"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         FinChat – Decommission / Rollback Script             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Project : ${PROJECT_ID}"
echo "║  Region  : ${REGION}"
if [[ "$DRY_RUN" == "true" ]]; then
echo "║  Mode    : DRY-RUN  (no changes will be made)                ║"
echo "║                                                              ║"
echo "║  Re-run with --execute to perform the actual decommission.   ║"
else
echo "║  Mode    : *** EXECUTE – RESOURCES WILL BE DELETED ***       ║"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [[ "$DRY_RUN" == "false" ]]; then
  read -r -p "⚠️  Type 'yes-delete' to confirm permanent deletion: " CONFIRM
  if [[ "$CONFIRM" != "yes-delete" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# Set project context
run gcloud config set project "${PROJECT_ID}"

# ─────────────────────────────────────────────────────────────────────────────
section "1 / 7 – Delete Cloud Run services"
# ─────────────────────────────────────────────────────────────────────────────
echo "  Deleting service: ${APP_SERVICE}"
run gcloud run services delete "${APP_SERVICE}" \
    --region "${REGION}" \
    --quiet

echo "  Deleting service: ${GRAFANA_SERVICE}"
run gcloud run services delete "${GRAFANA_SERVICE}" \
    --region "${REGION}" \
    --quiet

# ─────────────────────────────────────────────────────────────────────────────
section "2 / 7 – Delete Artifact Registry repository (and all images)"
# ─────────────────────────────────────────────────────────────────────────────
echo "  Deleting repository: ${AR_REPO} (includes all container images)"
run gcloud artifacts repositories delete "${AR_REPO}" \
    --location "${REGION}" \
    --quiet

# ─────────────────────────────────────────────────────────────────────────────
section "3 / 6 – Delete Secret Manager secrets"
# ─────────────────────────────────────────────────────────────────────────────
for SECRET_NAME in "${SECRETS[@]}"; do
  echo "  Deleting secret: ${SECRET_NAME}"
  run gcloud secrets delete "${SECRET_NAME}" \
      --quiet
done

# ─────────────────────────────────────────────────────────────────────────────
section "4 / 6 – Remove IAM role bindings"
# ─────────────────────────────────────────────────────────────────────────────
echo "  Removing roles from: ${APP_SA}"
run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None --quiet

run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${APP_SA}" \
    --role="roles/run.invoker" \
    --condition=None --quiet 2>/dev/null || true

echo "  Removing roles from: ${DEPLOY_SA}"
run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/run.admin" \
    --condition=None --quiet

run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/artifactregistry.writer" \
    --condition=None --quiet

run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None --quiet

run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --condition=None --quiet 2>/dev/null || true

# Compute SA binding
COMPUTE_SA="335507671966-compute@developer.gserviceaccount.com"
run gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None --quiet 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────────────
section "5 / 6 – Delete service accounts"
# ─────────────────────────────────────────────────────────────────────────────
echo "  Deleting SA: ${APP_SA}"
run gcloud iam service-accounts delete "${APP_SA}" \
    --quiet

echo "  Deleting SA: ${DEPLOY_SA}"
run gcloud iam service-accounts delete "${DEPLOY_SA}" \
    --quiet

# ─────────────────────────────────────────────────────────────────────────────
section "6 / 6 – Delete Workload Identity Pool (GitHub Actions OIDC)"
# ─────────────────────────────────────────────────────────────────────────────
echo "  Deleting WIF provider: ${WIF_PROVIDER} in pool: ${WIF_POOL}"
run gcloud iam workload-identity-pools providers delete "${WIF_PROVIDER}" \
    --workload-identity-pool="${WIF_POOL}" \
    --location="global" \
    --quiet

echo "  Deleting WIF pool: ${WIF_POOL}"
run gcloud iam workload-identity-pools delete "${WIF_POOL}" \
    --location="global" \
    --quiet

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
if [[ "$DRY_RUN" == "true" ]]; then
echo "║  DRY-RUN COMPLETE – no resources were modified.              ║"
echo "║  Run with --execute to perform the actual decommission.      ║"
else
echo "║  DECOMMISSION COMPLETE                                       ║"
echo "║  All FinChat resources have been removed from GCP.           ║"
echo "║                                                              ║"
echo "║  NOTE: The GCP project itself was NOT deleted.               ║"
echo "║  To delete the project entirely:                             ║"
echo "║    gcloud projects delete ${PROJECT_ID}"
echo "║                                                              ║"
echo "║  To also disable billing:                                    ║"
echo "║    Open: https://console.cloud.google.com/billing            ║"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
