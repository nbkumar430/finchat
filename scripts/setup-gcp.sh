#!/usr/bin/env bash
# FinChat GCP Infrastructure Setup Script
# Run this in Cloud Shell or any authenticated gcloud environment.
#
# Usage: bash scripts/setup-gcp.sh

set -euo pipefail

PROJECT_ID="project-ede0958a-eb5c-4225-94d"
REGION="us-central1"
AR_REPO="finchat-repo"
APP_SA="finchat-app-sa"
DEPLOY_SA="finchat-deploy-sa"
GITHUB_ORG="nbkumar430"
GITHUB_REPO="finchat"
WIF_POOL="github-pool"
WIF_PROVIDER="github-provider"

echo "=== Setting project ==="
gcloud config set project "$PROJECT_ID"

echo "=== Enabling APIs ==="
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  cloudbilling.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com

echo "=== Creating Artifact Registry repository ==="
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="FinChat container images" \
  2>/dev/null || echo "AR repo already exists"

echo "=== Creating App Service Account ==="
gcloud iam service-accounts create "$APP_SA" \
  --display-name="FinChat App SA" \
  2>/dev/null || echo "App SA already exists"

APP_SA_EMAIL="${APP_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant Vertex AI access to app SA
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${APP_SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --condition=None --quiet

# Grant Secret Manager access to runtime SA
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${APP_SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None --quiet

echo "=== Creating Deploy Service Account ==="
gcloud iam service-accounts create "$DEPLOY_SA" \
  --display-name="FinChat Deploy SA (GitHub Actions)" \
  2>/dev/null || echo "Deploy SA already exists"

DEPLOY_SA_EMAIL="${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant deploy SA necessary roles
for role in \
  "roles/run.admin" \
  "roles/artifactregistry.writer" \
  "roles/iam.serviceAccountUser" \
  "roles/secretmanager.secretAccessor"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_SA_EMAIL}" \
    --role="$role" \
    --condition=None --quiet
done

echo "=== Creating required secrets (if absent) ==="
gcloud secrets create GEMINI_API_KEY \
  --replication-policy="automatic" \
  2>/dev/null || echo "Secret GEMINI_API_KEY already exists"

gcloud secrets create GRAFANA_ADMIN_PASSWORD \
  --replication-policy="automatic" \
  2>/dev/null || echo "Secret GRAFANA_ADMIN_PASSWORD already exists"

echo "=== Setting up Workload Identity Federation ==="
# Create WIF pool
gcloud iam workload-identity-pools create "$WIF_POOL" \
  --location="global" \
  --display-name="GitHub Actions Pool" \
  2>/dev/null || echo "WIF pool already exists"

# Create WIF provider
gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
  --location="global" \
  --workload-identity-pool="$WIF_POOL" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  2>/dev/null || echo "WIF provider already exists"

# Allow GitHub repo to impersonate deploy SA
WIF_POOL_ID=$(gcloud iam workload-identity-pools describe "$WIF_POOL" \
  --location="global" --format="value(name)")

gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${WIF_POOL_ID}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}" \
  --quiet

WIF_PROVIDER_RESOURCE=$(gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
  --location="global" \
  --workload-identity-pool="$WIF_POOL" \
  --format="value(name)")

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Add these as GitHub Secrets:"
echo "  WIF_PROVIDER: ${WIF_PROVIDER_RESOURCE}"
echo "  WIF_SA_EMAIL: ${DEPLOY_SA_EMAIL}"
echo ""
echo "Then add Secret Manager values:"
echo "  echo -n '<gemini-api-key>' | gcloud secrets versions add GEMINI_API_KEY --data-file=-"
echo "  echo -n '<grafana-admin-password>' | gcloud secrets versions add GRAFANA_ADMIN_PASSWORD --data-file=-"
echo ""
echo "App SA (for Cloud Run): ${APP_SA_EMAIL}"
echo ""
