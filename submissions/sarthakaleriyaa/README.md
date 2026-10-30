# DocuMind — AI Knowledge Assistant

| | |
|---|---|
| **Live link** | Not deployed yet. Add the HTTPS URL after deployment. |
| **Demo video** | Not recorded yet. Add the Google Drive link after recording. |
| **Resume** | [`RESUME.pdf`](RESUME.pdf) |
| **Documentation** | [`DESIGN.md`](DESIGN.md) · [`EVALUATION.md`](EVALUATION.md) · [`AI_USAGE.md`](AI_USAGE.md) · [`FINAL_AUDIT.md`](FINAL_AUDIT.md) |

## Test Credentials

Not available yet. After deployment, create a reviewer account and upload the sample files from `evaluation/documents/`.

Add reviewer credentials here only if the deployment and assessment rules permit sharing them. Never commit private credentials or API keys.

## Project Status

The project has been tested locally, including automated tests and Docker Compose checks.

The following submission items are still pending:

- Public HTTPS deployment
- Successful GitHub Actions workflow run
- Demo video and deployment screenshots
- Reviewer test credentials
- Full scripted Gemini evaluation
- GitHub issues, pull requests and `v1.0.0` release

A small number of manual Gemini checks have succeeded, but some generation requests have timed out. These spot checks should not be treated as a completed live-provider evaluation.

## What It Does

DocuMind is an AI-powered knowledge assistant that allows users to upload PDF, plain-text and Markdown documents and ask questions in natural language.

Answers are grounded in uploaded documents, include source citations and page references where available, and clearly state:

> I couldn't find this in your documents.

### Key Features

- **Authentication:** User registration and login with Argon2 password hashing and JWT bearer tokens.
- **Document Upload:** Upload PDF, TXT and Markdown files.
- **Background Processing:** A separate worker processes documents asynchronously.
- **Processing Status:** Documents move through `queued`, `processing`, `ready` or `failed` states.
- **Document Selection:** Ask questions across all your documents or select specific documents.
- **Retrieval-Augmented Generation (RAG):** Retrieve relevant document passages using vector similarity search.
- **Source Citations:** Answers include source document, passage and page information where available.
- **Usage Information:** Track token usage, latency and estimated cost.
- **Question History:** Questions and answers are stored for later access.
- **User Isolation:** Users can access only their own documents, chunks and question history.
- **Rate Limiting:** Per-user limits protect the question endpoint.
- **Health Monitoring:** `/health` reports application dependencies and worker status.
- **API Documentation:** Interactive OpenAPI documentation is available at `/docs`.

The web interface is served by FastAPI at `/`. It is a dark, responsive single-page app built from plain JavaScript modules and CSS, with no build step and no third-party frontend dependencies:

- **Sign in / create account** with field validation, a password visibility toggle, password confirmation and the backend's own error messages (for example, an existing email).
- **Live system status** in the header, taken from `/health`. It shows "All systems operational" only when the server reports every dependency as healthy.
- **Knowledge base:** drag-and-drop or click to upload (type and 10 MB checks before upload), per-file upload progress, live processing status (queued, processing, ready, failed with the reason), page count, size and upload time, select one or all ready documents, refresh, and delete with confirmation.
- **Grounded Q&A console:** chat-style thread, suggested starter questions, a clear scope indicator (all ready documents or the selected ones), and three distinct outcomes: a cited answer, "Not found in your documents", or an error with a retry button. Citations show the document, the page (or passage) and the exact source text.
- **History** of past questions with status and usage.

The page is served with a strict Content-Security-Policy (scripts and styles only from the same origin, no inline code).

## Project Structure

```text
sarthakaleriyaa/
│
├── backend/
│   ├── app/
│   │   ├── static/
│   │   │   ├── index.html        # app shell and icon sprite
│   │   │   ├── css/app.css       # design tokens and components
│   │   │   └── js/
│   │   │       ├── api.js        # the only module that calls the API
│   │   │       ├── ui-logic.js   # pure rules: validation, formatting, status/health mapping
│   │   │       └── app.js        # views and interactions
│   │   ├── worker.py
│   │   └── ...
│   │
│   ├── migrations/               # Alembic migrations 0001–0004
│   ├── scripts/e2e_check.py      # end-to-end check against any running deployment
│   ├── tests/                    # pytest suites; tests/js holds the frontend unit tests
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── ...
│
├── evaluation/
│   ├── documents/
│   ├── run_eval.py
│   └── ...
│
├── docs/
│   └── SUBMISSION_CHECKLIST.md
│
├── .env.example
├── docker-compose.yml
├── render.yaml                  # Render Blueprint (free plan)
├── README.md
├── DESIGN.md
├── EVALUATION.md
├── AI_USAGE.md
└── FINAL_AUDIT.md
```

## Architecture

```mermaid
flowchart LR
    B[Browser: UI at /] --> API[FastAPI API]

    API -->|Document row: queued + file bytes| PG[(PostgreSQL + pgvector)]

    W[Background Worker] -->|Claim using SKIP LOCKED, read file bytes| PG
    W -->|Generate embeddings| AI[Gemini or Offline Local Provider]

    API -->|Question embedding and answer| AI
    API -->|Owner-filtered cosine search| PG
```

### Main Components

| Component | Responsibility |
|---|---|
| FastAPI | Authentication, APIs, document management and question answering |
| PostgreSQL | Users, documents, chunks, question history and worker queue |
| pgvector | Store embeddings and perform vector similarity search |
| Background Worker | Extract text, create chunks and generate embeddings |
| Gemini | Hosted embedding and answer generation |
| Local Provider | Offline embeddings and extractive answers for testing |
| Alembic | Database schema migrations |
| Web UI (`app/static`) | Plain JavaScript modules and CSS served by FastAPI; talks only to this API |

The Docker Compose environment contains four services:

1. `db` — PostgreSQL 17 with pgvector.
2. `migrate` — Runs Alembic database migrations.
3. `api` — FastAPI application and web interface.
4. `worker` — Background document-processing process.

Uploaded files are stored in PostgreSQL (`document_blobs`) in the same transaction that queues the document, so they survive restarts and redeploys and any worker can read them. The `documents` table also acts as the job queue. Workers claim jobs using PostgreSQL `FOR UPDATE SKIP LOCKED`, so a separate Redis or Celery service is not required.

See [`DESIGN.md`](DESIGN.md) for the detailed architecture, API contract, data model and design decisions.

## How a Question Is Answered

1. The user sends a question to `POST /chat` with a valid JWT bearer token.
2. The API verifies the JWT and loads the authenticated user.
3. **Rate limiting:** If the user has already submitted 10 or more questions in the last 60 seconds, the API returns HTTP 429 with `Retry-After`.
4. The question is saved in history with `pending` status. Every attempt counts towards the rate limit.
5. If `document_ids` are supplied, the API verifies that every selected document belongs to the user and is ready. Otherwise, it returns 404.
6. The question is converted into an embedding using the `RETRIEVAL_QUERY` task type with 768 dimensions.
7. The API retrieves the user's own ready document chunks using exact pgvector cosine-distance ranking. The top 5 results are selected and then checked against a distance threshold.
8. If no chunk passes the retrieval threshold, the API returns a fixed refusal without calling the LLM.
9. Otherwise, the system builds a grounded prompt. System instructions are kept separate from retrieved passages, which are marked as untrusted source data.
10. The LLM generates an answer with citations such as `[S1]` or `[S1, S3]`.
11. The application normalises refusals, checks for prompt leakage and injected instructions, and redacts credentials.
12. Only the sources cited by the final answer are returned, together with usage information and latency.
13. The question history is updated with `answered`, `refused` or `error` status.

Provider failures return a generic HTTP 503 response. A provider quota error receives a separate usage-limit response with `Retry-After`. HTTP 504 errors are not retried; only selected transient failures are eligible for retries.

Provider error bodies are never logged or returned to users.

## Technology Stack

| Technology | Purpose |
|---|---|
| **Python** | Backend development |
| **FastAPI** | REST API and request validation |
| **PostgreSQL** | Relational database |
| **pgvector** | Vector storage and similarity search |
| **Gemini** | Embedding generation and AI answers |
| **Alembic** | Database migrations |
| **pypdf** | PDF text extraction |
| **HTML, CSS and JavaScript** | Frontend interface |
| **pytest** | Automated testing |
| **Ruff** | Python linting |
| **Docker Compose** | Local service orchestration |
| **GitHub Actions** | CI workflow |

### AI Providers

**Gemini provider**

- Generation model: `gemini-3.5-flash-lite`
- Embedding model: `gemini-embedding-001`
- Embedding dimensions: 768

Gemini provides hosted embedding and answer generation. Live latency and availability depend on provider limits and service availability.

**Offline local provider**

The `local` provider uses hashed bag-of-words embeddings and an extractive answerer. It makes no external network calls and allows local development, automated testing and evaluation without API keys or usage costs.

It is a lexical baseline, not a replacement for semantic retrieval or production-quality LLM answers.

**Privacy note:** When `AI_PROVIDER=gemini` is enabled, document text and questions are sent to Google's service for processing.

## Local Setup

### Requirements

- Python 3.12+
- Docker Desktop (Windows/macOS) or Docker Engine with Compose v2 (Linux/WSL)
- Git
- Optional: Node.js for the UI behaviour test
- Gemini API key only when using the hosted Gemini provider

### 1. Clone or open the repository

Run the following commands from the submission repository root:

```bash
git clone <your-repository-url>
cd sarthakaleriyaa
```

If you have already cloned the repository, simply open its root directory.

### 2. Configure environment variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Update the following values in `.env`:

- `POSTGRES_PASSWORD`: Set a secure database password.
- `JWT_SECRET_KEY`: Use a strong random secret.
- `AI_PROVIDER`: Set to `local` or `gemini`.
- `GEMINI_API_KEY`: Required when using Gemini.

You can generate a JWT secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For offline usage:

```env
AI_PROVIDER=local
```

For Gemini usage:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
```

Do not commit `.env` or expose API keys in source code.

### 3. Start the application

From the repository root:

```bash
docker compose up --build
```

The migration service runs first and exits after applying migrations. The API and worker then start.

### 4. Open the application

| Service | URL |
|---|---|
| Web Interface | http://localhost:8000/ |
| API Documentation | http://localhost:8000/docs |
| Health Check | http://localhost:8000/health |

PostgreSQL is published on `127.0.0.1:5433` only.

To try the application, upload the sample documents from `evaluation/documents/` and ask:

> How many days of paid annual leave do full-time employees get?

## Development Without Docker

For development without running the complete Compose environment, start the database separately.

### 1. Start PostgreSQL

From the repository root:

```bash
docker compose up -d db
```

### 2. Create a virtual environment

Move into the backend directory:

```bash
cd backend
```

Create the environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

### 3. Configure the database connection

Set `DATABASE_URL` using the values from your `.env` file. It should point to the local database on port `5433`.

### 4. Run migrations

From `backend/`:

```bash
alembic upgrade head
```

### 5. Start the API

In one terminal:

```bash
uvicorn app.main:app --reload
```

### 6. Start the worker

In another terminal, activate the same virtual environment and run:

```bash
python -m app.worker
```

## Tests and Linting

Run these commands from the `backend/` directory.

### Run automated tests

```bash
python -m pytest tests -q
```

No API keys are required. Tests use controlled provider behaviour and do not rely on the developer's `.env` file.

The frontend tests import the real `api.js` and `ui-logic.js` modules in Node.js (`tests/js/frontend_unit.mjs`). They run only when Node.js is installed; otherwise, they are skipped.

### End-to-end check

`scripts/e2e_check.py` drives a running deployment over HTTP. It registers a new test account, uploads a real 3-page PDF, waits for processing, checks a page-2 citation and an honest refusal, then deletes the document and confirms it is gone. It never touches existing data.

```bash
python scripts/e2e_check.py --base-url http://127.0.0.1:8000
```

### Run Ruff

```bash
ruff check . && ruff check ../evaluation --config ruff.toml
```

### PostgreSQL and pgvector integration tests

The Postgres integration tests run when `TEST_DATABASE_URL` points to a disposable test database.

**Warning:** The integration tests drop and recreate the test database schema. Never point `TEST_DATABASE_URL` at a database containing important data.

Start a disposable pgvector database:

```bash
docker run -d --name documind-test-db -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test -p 127.0.0.1:56543:5432 pgvector/pgvector:pg17
```

On Linux/macOS:

```bash
TEST_DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:56543/test python -m pytest tests -q
```

On Windows PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://test:test@127.0.0.1:56543/test"
python -m pytest tests -q
```

## Troubleshooting

| Symptom | Possible Fix |
|---|---|
| `Set POSTGRES_PASSWORD in .env` | Create `.env` from `.env.example` and configure the database password. |
| `Set JWT_SECRET_KEY in .env` | Configure a strong JWT secret in `.env`. |
| `port is already allocated` | Change `API_PORT` or `DB_PORT` in `.env`. |
| Documents remain `queued` | Check whether the worker is running using `docker compose logs worker`. |
| Worker shows `unhealthy` | Check the worker logs and database connectivity. |
| Document stays queued after a provider outage | Retry backoff is expected. The default retry intervals start at 30 seconds and increase. |
| Embedding service unavailable | Check the Gemini API key, provider availability and retry attempts. Use `AI_PROVIDER=local` for offline development. |
| Document fails with `No extractable text found` | The PDF may be scanned or image-only. OCR is not supported. |
| Alembic migration fails on an old manually created database | Back up any required data before resetting the development database. `docker compose down -v` deletes volumes and database data. |

## Deployment

**Status: not deployed yet.** Everything needed is in the repository; creating the hosting account is the remaining manual step.

### Render (free plan)

[`render.yaml`](render.yaml) is a Render Blueprint for one free web service plus a free PostgreSQL 17 database (pgvector is supported). It is built around the free-plan limits (Render docs, checked 2026-10-30):

| Free-plan limit | How DocuMind handles it |
|---|---|
| No free background workers | The ingestion worker runs inside the web service (`RUN_EMBEDDED_WORKER=true`). Same queue, retries and heartbeats |
| Ephemeral filesystem | Uploaded files are stored in PostgreSQL (`document_blobs`), not on disk |
| No pre-deploy command | Migrations run at start (`RUN_MIGRATIONS_ON_START=true`); the server does not start if they fail |
| Sleeps after 15 minutes idle | First request after idle takes about a minute to wake the service |
| Free database expires after 30 days, no backups | Upgrade the database plan, or redeploy, to keep the app live longer |

Steps:

1. Push the repository to your GitHub fork (done for the current branch).
2. Sign in at [render.com](https://render.com) with GitHub and allow access to the fork.
3. **New → Blueprint**, choose the fork, select the branch, and set the **Blueprint path** to `submissions/sarthakaleriyaa/render.yaml`.
4. When prompted, paste your `GEMINI_API_KEY` into Render (it is stored by Render, never in the repository). `JWT_SECRET_KEY` is generated automatically. To run without Gemini, change `AI_PROVIDER` to `local` in the service's environment settings.
5. Apply the Blueprint. Wait for the deploy to finish, then open `https://<service>.onrender.com/health`.
6. Verify the deployment end to end:

   ```bash
   python scripts/e2e_check.py --base-url https://<service>.onrender.com --ready-timeout 300
   ```

The same container image has been verified locally in this single-service configuration: one container, embedded worker, migrations on start, a `postgres://` database URL, and documents still available after the container was destroyed and recreated.

### Other hosts

Any Docker host works. Run the `backend/` image as the web service, either with `RUN_EMBEDDED_WORKER=true` or with a second service running `python -m app.worker`. Use PostgreSQL 13+ with pgvector, and run `alembic upgrade head` before starting (or set `RUN_MIGRATIONS_ON_START=true`).

## Evaluation Summary

The evaluation dataset contains 24 questions across 5 original documents:

- 16 answerable questions
- 8 unanswerable questions
- 2 prompt-injection attack cases

The reported baseline metrics below were generated using the offline `local` provider and `evaluation/run_eval.py`.

| Metric | Baseline (1,000-character chunks) |
|---|---:|
| Retrieval hit rate | 1.000 |
| Answer correctness | 0.938 |
| Correct refusal rate | 0.875 |
| Injection payload leaks | 0 / 24 |

### Chunking Experiment

The evaluation compared the default 1,000-character chunks against 400-character chunks.

The experiment showed no measurable improvement from the smaller chunk size, so the default remains 1,000 characters with 150 characters of overlap.

A false refusal involving hotel limits led to adjusting the offline retrieval gate from 0.90 to 0.95.

These results are limited to the recorded offline evaluation runs. They should not be interpreted as proof of production performance or Gemini answer quality.

See [`EVALUATION.md`](EVALUATION.md) for the dataset, metrics, experiment details and limitations.

## Feature Status

| Requirement | Status |
|---|---|
| A1 — Authentication, password hashing, JWT and per-user isolation | Done and tested, including cross-user tests on real PostgreSQL |
| A2 — PDF/TXT/MD upload, background worker, document statuses and deletion | Done and tested |
| A3 — Question answering, selected-document filtering, citations, usage and history | Done and tested |
| A4 — Rate limiting, provider error handling, health checks and request-ID logging | Done and tested |
| A5 — Minimal web UI and OpenAPI documentation | Implemented and tested locally; not deployed publicly |
| B1–B3 — Retrieval, grounding, refusal and prompt-injection defences | Core logic tested with local provider and mocked LLM; full live Gemini quality and injection evaluation pending |
| B4 — Evaluation dataset, script, experiment and documentation | Completed for the local provider; scripted Gemini evaluation pending |
| C1 — Dockerfile and Docker Compose | Implemented; previously verified on a clean volume |
| C2 — GitHub Actions CI workflow | Passing on GitHub ([run 41012013155](https://github.com/sarthakaleriyaa/Software-Engineering-Assesment/actions/runs/41012013155), commit `ed79b66`): lint, 140 tests, evaluation, Docker build |
| C3 — Public deployment | Not done |
| D2 — GitHub issues and pull requests | Not done |
| D4 — `v1.0.0` tag and release | Not done |
| E2 — Demo video, screenshots and reviewer credentials | Not done |

For the detailed submission checklist, see [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md).

## Known Limitations and Future Improvements

- **File storage in PostgreSQL:** Uploads (up to 10 MB each) are stored in the database so they survive redeploys on hosts with ephemeral disks. For much larger files or volumes, object storage (S3, GCS) would be more economical.
- **Polling worker:** The worker polls the PostgreSQL queue every 2 seconds. `LISTEN/NOTIFY` or a dedicated message broker could improve queue efficiency at higher volumes.
- **Rate limiting:** The current implementation counts question-history records. A token bucket or Redis-based limiter would provide more precise behaviour under heavy concurrency.
- **No OCR:** Scanned PDFs and image-only documents are not supported.
- **No table-aware extraction:** Complex tables may not be represented accurately by basic PDF text extraction.
- **Vector-only retrieval:** Hybrid BM25 and vector search, followed by re-ranking, could improve retrieval quality.
- **Prompt-injection defence:** Current protections are layered heuristics, not a guarantee against every possible attack.
- **Single-turn questions:** Conversation memory and streaming responses are not currently implemented.

## Future Scope

Potential improvements include:

1. Public HTTPS deployment and production monitoring.
2. Object storage for very large documents.
3. Hybrid keyword and vector retrieval.
4. Re-ranking retrieved passages.
5. OCR support for scanned PDFs.
6. Improved evaluation with a larger dataset and scripted Gemini runs.
7. Conversation memory and streaming responses.
8. More extensive browser-based end-to-end testing.

---

**DocuMind — AI Knowledge Assistant**

Built with FastAPI, PostgreSQL, pgvector and Gemini, with an offline provider for development and evaluation.