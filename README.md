# FinChat – Financial News Chat Application

AI-powered chat application for querying recent financial news using Vertex AI (Gemini), with Gemini API fallback when configured.

## Features

- Chat interface for financial news Q&A (AAPL, MSFT, AMZN, NFLX, NVDA, INTC, IBM)
- Vertex AI summarization with grounded responses
- Prometheus metrics with four golden signals dashboard
- Structured JSON logging (Cloud Logging compatible)
- Swagger UI + ReDoc API documentation
- CI/CD via GitHub Actions with Workload Identity Federation
- Containerized deployment on Cloud Run

## Quick Start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Visit http://localhost:8080 for the chat UI, http://localhost:8080/docs for Swagger.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for full details on design, alerting, and trade-offs.

**AI summarization troubleshooting:** [docs/AI_SUMMARIZATION.md](docs/AI_SUMMARIZATION.md) (Vertex vs API key, local testing, common errors).

## Deployment

Push to `main` triggers the CI/CD pipeline: lint → test → build → deploy to Cloud Run.

Required GitHub Secrets:
- `WIF_PROVIDER`: Workload Identity Federation provider resource name
- `WIF_SA_EMAIL`: Service account email for deployments
- `BILLING_EXPORT_TABLE` (optional): BigQuery billing export table as `project.dataset.table`
- `SENDGRID_API_KEY` (optional): for daily billing email delivery

Runtime credentials are fetched from Secret Manager (not GitHub secrets) where applicable:
- `GEMINI_API_KEY`

**Grafana (prototype):** CI sets **`admin` / `admin`** on Cloud Run — no Grafana secret required. For production, change `GF_SECURITY_ADMIN_PASSWORD` (and user if needed) on the `finchat-grafana` service.

### Grafana Cloud Run: Secret Manager (optional / production hardening)

If you configure Grafana to use **`GF_SECURITY_ADMIN_PASSWORD`** from Secret Manager instead of the prototype defaults, **`finchat-app-sa`** must have **`roles/secretmanager.secretAccessor`** on that secret (or the project). If the revision fails with permission denied:

**Option A — project-wide (matches `scripts/setup-gcp.sh`):**

```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:finchat-app-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

**Option B — this secret only (least scope):**

```bash
gcloud secrets add-iam-policy-binding GRAFANA_ADMIN_PASSWORD \
  --project=PROJECT_ID \
  --member="serviceAccount:finchat-app-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Replace `PROJECT_ID` with your project (e.g. `project-ede0958a-eb5c-4225-94d`). Then **redeploy** `finchat-grafana` (re-run the GitHub Actions deploy or `gcloud run deploy …`).

Browser errors like “Grafana has failed to load its application files” on the Cloud Run URL often clear once the service revision is **Ready** and serving; fix IAM first, then redeploy.

### Grafana: “Cannot update environment variable … different type”

This appears when **`GF_SECURITY_ADMIN_PASSWORD`** (or **`GF_SECURITY_ADMIN_USER`**) is already wired as a **Secret Manager** value on Cloud Run, but **`gcloud run deploy --set-env-vars`** tries to set a **plain string** (e.g. CI `admin` / `admin`). A variable cannot be both types.

**Fix (pick one path):**

1. **Use CI/plain literals again (prototype):** clear the secret bindings, then redeploy.

   ```bash
   export PROJECT_ID=your-project REGION=us-central1 SERVICE=finchat-grafana
   gcloud run services update "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
     --clear-secrets GF_SECURITY_ADMIN_PASSWORD \
     --clear-secrets GF_SECURITY_ADMIN_USER
   ```
   (Omit either `--clear-secrets` line if only one variable was secret-backed.)

   Then re-run the GitHub workflow (or `gcloud run deploy …` with `--set-env-vars` as in CI).

2. **Keep Secret Manager (production):** do **not** pass those vars as literals in deploy. Remove `GF_SECURITY_ADMIN_PASSWORD` / `GF_SECURITY_ADMIN_USER` from the workflow’s `--set-env-vars`, and set them only via **`--set-secrets`** or the Cloud Console, with IA M as in the section above.

## Public URLs

Deployed hostnames are **per project/region** (not fixed in the repo). After a successful GitHub Actions deploy on `main`, open the workflow run → **Deployment Summary** for the resolved **App URL** and **Grafana URL**.

Or fetch with gCloud:

```bash
export REGION=us-central1   # match your CI REGION
gcloud run services describe finchat-app --region "$REGION" --format='value(status.url)'
gcloud run services describe finchat-grafana --region "$REGION" --format='value(status.url)'
```

- Chat app: `<APP_URL>/`
- Swagger UI: `<APP_URL>/docs`
- Grafana: `<GRAFANA_URL>/` — sign in with **`admin` / `admin`** (prototype defaults from CI; change for production).

The CI/CD workflow also publishes these links automatically in the GitHub Actions
run summary after a successful deployment, along with smoke-test results.
