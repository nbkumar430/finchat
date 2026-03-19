# AI summarization – failure scenarios & fixes

FinChat uses the Google **GenAI** SDK when available. If the model call fails, answers are still produced from `stock_news.json` using an **extractive TF–IDF ranker** (k nearest sentences) — no extra dependencies, no hallucination outside the file.

Summarization modes:

| Mode | When | Auth |
|------|------|------|
| **Vertex AI** | `USE_VERTEX_AI=true` (default on Cloud Run) | Cloud Run service account (`roles/aiplatform.user`) |
| **Gemini API (AI Studio)** | `USE_VERTEX_AI=false` **or** automatic fallback | `GEMINI_API_KEY` |
| **OpenRouter** | `SUMMARIZATION_PROVIDER=openrouter` | `OPEN_ROUTER_API_KEY` (Cloud Run env; legacy: `OPENROUTER_API_KEY`) |

## Why summarization can fail (checklist)

1. **Vertex: model not available in project/region** (`404 NOT_FOUND`, “Publisher Model … was not found”)  
   - The model ID must exist in **Vertex AI → Model Garden** for your project and region.  
   - Preview models may require allowlisting.  
   - **Fix:** In GCP console, enable/use a listed Gemini model; set `VERTEX_MODEL` to that exact ID.  
   - **App fix:** With `VERTEX_FALLBACK_TO_API_KEY=true` (default) and `GEMINI_API_KEY` set, the app automatically tries the **Gemini API** after Vertex models fail.

2. **Gemini API: quota / billing** (`429 RESOURCE_EXHAUSTED`, free tier limit 0)  
   - **Fix:** Enable billing or upgrade quota on the Google AI / Gemini API project tied to the key.

3. **Wrong or deprecated model ID**  
   - Vertex expects IDs like `gemini-2.0-flash-001` depending on release; AI Studio may accept `gemini-2.0-flash`.  
   - **Fix:** Tune `VERTEX_MODEL`, `VERTEX_FALLBACK_MODELS`, and `GEMINI_API_FALLBACK_MODELS`.

4. **Missing credentials**  
   - Vertex: runtime SA without `roles/aiplatform.user` or wrong `GCP_PROJECT_ID` / `GCP_REGION`.  
   - API: missing or invalid `GEMINI_API_KEY`.

5. **Circuit breaker / overload** (app-side)  
   - After repeated failures the circuit opens briefly; concurrent calls are capped.  
   - **Fix:** Wait for cooldown; reduce traffic; fix root cause (quota or model access).

6. **Empty model response**  
   - Treated as error; user sees grounded fallback.

## One-shot: create Gemini API key and upload to Secret Manager

From the repo root (authenticated `gcloud` with permissions to create API keys and Secret Manager versions):

```bash
export PROJECT_ID=your-gcp-project-id   # optional; defaults in script for this demo project
bash scripts/create-gemini-api-key-and-secret.sh
```

Optional: `DRY_RUN=1 bash scripts/create-gemini-api-key-and-secret.sh` to preview steps only.

## OpenRouter (when Vertex / org policy blocks GCP Gemini)

1. Create an [OpenRouter](https://openrouter.ai/) API key.
2. Set it as an environment variable (**Cloud Run**: add env var `OPEN_ROUTER_API_KEY`; optional legacy name `OPENROUTER_API_KEY`).
3. Configure the app:

```bash
export SUMMARIZATION_PROVIDER=openrouter
export OPENROUTER_MODEL=google/gemini-3-flash-preview   # default; override if needed
export OPEN_ROUTER_API_KEY="sk-or-v1-..."
```

4. **Connection test** from repo root:

```bash
PYTHONPATH=. python scripts/test_openrouter_connection.py
```

5. Run the app with the same env vars; `/health` will probe OpenRouter when `SUMMARIZATION_PROVIDER=openrouter`. Chat responses use `answer_source=openrouter`.

---

## Local testing (real summarization)

**Option A – Gemini API only (simplest for laptops):**

```bash
cd /path/to/finchat
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export USE_VERTEX_AI=false
export GEMINI_API_KEY="your-key"   # AI Studio
set PYTHONPATH=.   # if needed
python scripts/test_local_summarization.py
# Or run the API:
uvicorn app.main:app --host 127.0.0.1 --port 8080
curl -s -X POST http://127.0.0.1:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the latest Apple news?","ticker":"AAPL"}'
```

**Option B – Vertex AI (requires `gcloud auth application-default login` and project access):**

```bash
export USE_VERTEX_AI=true
export GCP_PROJECT_ID=your-project-id
export GCP_REGION=us-central1
export VERTEX_MODEL=gemini-2.0-flash   # use an ID your project actually has
python scripts/test_local_summarization.py
```

## Production (Cloud Run)

Recommended env (in addition to secrets):

- `USE_VERTEX_AI=true`
- `VERTEX_FALLBACK_TO_API_KEY=true` (default) so a valid `GEMINI_API_KEY` covers Vertex model gaps.
- `GEMINI_API_KEY` from Secret Manager (already in CI/CD).

If every Vertex candidate returns `404`, logs will show **“attempting Gemini API key fallback”**; if the key then hits `429`, fix billing/quota on the API side.
