#!/usr/bin/env bash
# Create a Google Cloud API key restricted to Generative Language API (Gemini
# Developer API), then add it as a new version of GEMINI_API_KEY in Secret Manager.
#
# Vertex AI itself uses the Cloud Run service account, not this key. This key is
# what FinChat uses for GEMINI_API_KEY / API fallback (see docs/AI_SUMMARIZATION.md).
#
# Prerequisites:
#   - gcloud authenticated (e.g. gcloud auth login)
#   - Roles such as roles/serviceusage.apiKeysAdmin (or Owner) on the project
#   - roles/secretmanager.admin (or versions add) for the secret
#
# Usage:
#   export PROJECT_ID=your-gcp-project-id
#   bash scripts/create-gemini-api-key-and-secret.sh
#
# Optional:
#   SECRET_NAME=GEMINI_API_KEY     # default
#   DISPLAY_NAME=finchat-gemini-api # default
#   DRY_RUN=1                      # print steps only, do not create key or secret

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-project-ede0958a-eb5c-4225-94d}"
SECRET_NAME="${SECRET_NAME:-GEMINI_API_KEY}"
DISPLAY_NAME="${DISPLAY_NAME:-finchat-gemini-api-$(date +%Y%m%d)}"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "ERROR: $*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

require_cmd gcloud
require_cmd python3

echo "=== FinChat: Gemini API key → Secret Manager ==="
echo "Project:     $PROJECT_ID"
echo "Secret:      $SECRET_NAME"
echo "Key display: $DISPLAY_NAME"
echo ""

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[DRY_RUN] Would run:"
  echo "  gcloud config set project $PROJECT_ID"
  echo "  gcloud services enable generativelanguage.googleapis.com aiplatform.googleapis.com"
  echo "  gcloud services api-keys create --api-targets=service=generativelanguage.googleapis.com ..."
  echo "  gcloud secrets create $SECRET_NAME || true"
  echo "  gcloud secrets versions add $SECRET_NAME --data-file=-"
  exit 0
fi

gcloud config set project "$PROJECT_ID"

echo "=== Enabling APIs ==="
gcloud services enable generativelanguage.googleapis.com --project="$PROJECT_ID"
gcloud services enable aiplatform.googleapis.com --project="$PROJECT_ID"

echo "=== Creating API key (restricted to Generative Language API only) ==="
# keyString is only returned on create; capture JSON from stdout only.
if ! RESPONSE="$(
  gcloud services api-keys create \
    --project="$PROJECT_ID" \
    --display-name="$DISPLAY_NAME" \
    --api-targets=service=generativelanguage.googleapis.com \
    --format=json
)"; then
  die "api-keys create failed. Ensure you have permission (e.g. Service Usage > API Keys Admin)."
fi

KEY_STRING="$(
  echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
ks = data.get('keyString')
if not ks:
    sys.stderr.write('missing keyString in gcloud response — create the key in Console and upload manually\n')
    sys.exit(1)
print(ks, end='')
"
)" || die "Could not parse keyString from gcloud output."

KEY_RESOURCE="$(
  echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
name = data.get('name')
if not name:
    sys.stderr.write('missing name in gcloud response\n')
    sys.exit(1)
print(name, end='')
"
)" || die "Could not parse key resource name from gcloud output."

echo "Created key resource: $KEY_RESOURCE"
echo "(Raw key value is NOT printed; it is sent only to Secret Manager.)"

echo "=== Ensuring secret exists: $SECRET_NAME ==="
if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "Secret already exists."
else
  gcloud secrets create "$SECRET_NAME" \
    --project="$PROJECT_ID" \
    --replication-policy=automatic
  echo "Secret created."
fi

echo "=== Adding new secret version (GEMINI_API_KEY) ==="
printf '%s' "$KEY_STRING" | gcloud secrets versions add "$SECRET_NAME" \
  --project="$PROJECT_ID" \
  --data-file=-

echo ""
echo "=== Done ==="
echo "Secret Manager: projects/$PROJECT_ID/secrets/$SECRET_NAME (new version added)"
echo "Cloud Run already mounts this as GEMINI_API_KEY:latest when deployed via CI/CD."
echo "Redeploy the app or wait for the next rollout to pick up the new version."
