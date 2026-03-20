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

Runtime credentials are fetched from Secret Manager (not GitHub secrets):
- `GEMINI_API_KEY`
- `GRAFANA_ADMIN_PASSWORD`

### Grafana Cloud Run: “Permission denied on secret … GRAFANA_ADMIN_PASSWORD”

The **`finchat-app-sa`** service account must be able to read the secret at runtime (`roles/secretmanager.secretAccessor`). If the revision fails with that error, apply one of the following (as a project Owner or Security Admin):

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

## Public URLs

After deployment, fetch service URLs:

```bash
gcloud run services describe finchat-app --region us-central1 --format='value(status.url)'
gcloud run services describe finchat-grafana --region us-central1 --format='value(status.url)'
```

- Chat app: `<APP_URL>/`
- Swagger UI: `<APP_URL>/docs`
- Grafana: `<GRAFANA_URL>/`

The CI/CD workflow also publishes these links automatically in the GitHub Actions
run summary after a successful deployment, along with smoke-test results.
