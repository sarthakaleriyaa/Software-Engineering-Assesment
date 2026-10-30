# DocuMind — Final Audit

**Audit date:** 2026-10-29  
**Documentation re-checked:** 2026-10-30  
**Source of truth:** Assignment brief (`README.md` at the repository root, sections 3–7).

This report combines local code and test checks with a limited number of manual live-provider observations. It does not claim a GitHub Actions run or a public deployment. Live Gemini observations are limited to the specific requests recorded in Section 6; a full live-provider evaluation has not been completed.

---

## 1. Executive Summary

The main application flows have been verified locally: authentication, per-user isolation, background ingestion, citation and refusal logic, prompt-injection defences, history, rate limiting, health checks, Docker Compose and automated tests.

A small set of manual Gemini requests also succeeded, but generation has been intermittent and a full live-provider evaluation remains outstanding.

The audit identified and fixed a retrieval correctness bug in which the HNSW query could hide a user's own documents. Additional security, reliability and usability issues were also addressed, as detailed in Section 4.

The main remaining submission blockers are deployment, the demo video, GitHub issues and pull requests, the release tag, a CI run on GitHub, a full evaluation against the real Gemini provider, and final review of the AI usage disclosure.

### Compliance summary

| Status | Count | Requirements |
|---|---:|---|
| PASS | 10 | A1, A2, A3, A4, B5, C1, C2, D1, D3, E4 |
| PARTIAL | 7 | A5, B1, B2, B3, B4, E1, E3 |
| FAIL | 4 | C3, D2, D4, E2 |
| NOT VERIFIED | 0 | Unverified aspects are explicitly identified within the relevant PARTIAL rows |

---

## 2. Compliance Matrix

| ID | Requirement | Status | Evidence | Gap / Risk | Priority | Action |
|---|---|---|---|---|---|---|
| A1 | Hashed passwords, token authentication on document/question endpoints, strict per-user isolation | **PASS** | Argon2 via `pwdlib` (`core/security.py`). JWT HS256 with required `sub` and `exp` claims. Every database lookup filters by `user_id` in SQL. Tests cover missing, invalid and expired tokens; foreign document GET/DELETE requests return the same 404 as missing documents; real-Postgres tests verify cross-user chunk isolation and private chat history. | Signup returns 409 for an existing email, so account existence can be probed there. Login no longer leaks account existence through password-verification timing. No login brute-force limit; the brief does not require one. | P2 | Optional: add per-IP login throttling. |
| A2 | PDF/TXT/MD upload with size limit; separate ingestion worker; queued, processing, ready or failed status; list/delete and chunk removal | **PASS** | Upload returns 202 and inserts a `queued` row without processing inline. Worker runs with `python -m app.worker` and uses `FOR UPDATE SKIP LOCKED`. Safe failure reasons, retry backoff and stale-job recovery are covered by tests. Delete removes chunks, files and history passages. Docker end-to-end testing verified that documents queue while the worker is stopped and are processed after it starts. | The `processing` state was not observed during polling because local ingestion completes in under 0.1 seconds. Its transition is covered by unit tests and worker logs. | P2 | None. |
| A3 | Ask across all or selected documents; answer, citations and usage; stored history | **PASS** | `POST /chat` supports optional `document_ids`; foreign or not-ready IDs return 404. Citations include filename, passage, page number, chunk index and score. Usage includes token estimates, `latency_ms` and `estimated_cost_usd`. `GET /chat/history` is available. PDF page citations are covered by the full-flow test and Docker smoke test. | Cost is `null` unless prices are configured; prices are not guessed. Token counts are estimates under the offline provider. | P2 | Configure `GEMINI_*_USD_PER_MTOK` on deployment. |
| A4 | Per-user rate limit; graceful provider failures and retries; health checks; JSON logs with request IDs | **PASS** | Database-backed rate limiting returns 429 with `Retry-After`. Tests verify per-user limits; Docker smoke test returned `[200, 429, 429]`. `/health` checks DB, pgvector version, worker heartbeat and queue depth; it returns 503 when degraded. Worker-stop behaviour was verified in Docker. JSON logs include request IDs, which are carried from upload into worker logs. Provider failures return a generic 503 without logging provider response bodies. | Rate limiting can overshoot slightly under concurrent requests because it uses a count-then-insert approach. | P2 | Limitation documented in README. |
| A5 | Usable web UI for reviewer flows; interactive API docs on live deployment | **PARTIAL** | Single-page UI is served by FastAPI. Manual browser click-through passed signup/login, upload, status polling, Q&A with citations, refusal, delete, history and error states. Re-checked under the new CSP. There are 15 contract tests and 8 Node-based UI behaviour checks. `node --check` passes. `/docs` is served. | The live-deployment requirement cannot be met until C3 is complete. There is no automated browser test; the click-through was manual and used a scripted file selection and `confirm()` stub. | P1 via C3 | Deploy, then repeat the click-through on the live URL. |
| B1 | Justified chunking, embedding model and vector store; semantic retrieval | **PARTIAL** | Page-aware chunking uses 1000/150 character settings and word boundaries. Embeddings use `gemini-embedding-001` at 768 dimensions. pgvector retrieval uses exact cosine ranking per user. Retrieval SQL was tested against real Postgres and pgvector. | Live Gemini embeddings worked in the controlled check and logged manual use, but every scripted evaluation run used the offline lexical embedder. Live semantic retrieval quality is **NOT VERIFIED**. | P1 | Run the Gemini evaluation with permission. |
| B2 | Answers grounded in retrieved text, real citations and clear not-found behaviour | **PARTIAL** | Retrieval gate prevents an LLM call when no chunk meets the configured threshold. The system prompt requires `[S#]` citations and exact refusal text. Refusals are normalised and only cited sources are returned. Logic is covered by `test_rag_logic.py`. | The real LLM was only spot-checked. In logged 30-second-timeout requests, 7 of 13 returned answers. Systematic grounding quality is **NOT VERIFIED**. The offline extractive generator mis-answered u02, as documented in `EVALUATION.md`. | P1 | Run the Gemini evaluation. |
| B3 | Treat document text as data; include injection test document and demonstrate behaviour | **PARTIAL** | Injection test document: `evaluation/documents/kestrel_office_guide_INJECTION_TEST.md`. Layered defences include separate system instructions, escaped source blocks, detection logging, canary and output guard, and redaction. Regression tests cover escaping and the output guard. Evaluation recorded 0/24 payload leaks; i01 and i02 were refused. | Resistance of a real LLM is **NOT VERIFIED**. Pattern-based guards are heuristics, not guarantees. | P1 | Run Gemini evaluation and show i01/i02 in the demo. |
| B4 | 15+ questions, 5+ unanswerable, evaluation metrics, one-variable experiment and write-up | **PARTIAL** | Dataset contains 24 questions, including 8 unanswerable questions. `run_eval.py` reports the required metrics. Two deterministic runs matched the committed `baseline.json` exactly. The experiment changes only `chunk_size`; `EVALUATION.md` clearly identifies results as offline. | The committed automated evaluation uses only the offline provider, not Gemini. Manual Gemini requests are not a substitute for scripted evaluation. The evaluation ranks chunks in memory and does not exercise the pgvector SQL path; Postgres tests cover that path separately. | P1 | Run `python evaluation/run_eval.py --provider gemini --allow-remote --label gemini-baseline`, then update `EVALUATION.md`. |
| B5 | Provider flexibility; keys never committed | **PASS** | `AI_PROVIDER`, `GEMINI_API_KEY` and model IDs come from environment variables. Tests disable dotenv and remove the key. No tracked `.env` exists. Secret-pattern scan across 79 deliverable files found only a synthetic test fixture. | The free-tier project reached the daily limit for `gemini-3.8-flash` (about 20 requests). Default changed to `gemini-3.5-flash-lite`; it has since been called live, but answers were slow and intermittent. | P2 | Investigate generation latency with approved live tests, changing one setting at a time. |
| C1 | Dockerfile per service, small base image, multi-stage, non-root; Compose startup | **PASS** | One multi-stage `python:3.12-slim` image (376 MB) runs as `documind` and is used for `migrate`, `api` and `worker`. Compose includes health checks and dependency ordering. A fresh-volume `up --build` was healthy and the full smoke test passed. Compose config validates with `.env.example`. | Queue uses Postgres rather than a separate broker, documented in `DESIGN.md` Section 13. There is no separate frontend container; the brief permits this. | P2 | None. |
| C2 | GitHub Actions on PR and push to main; lint, tests and Docker build; path filter; green final commit | **PASS** | **Ran on GitHub** (run 41010392777, commit `68b4bef`, 2026-10-30): both jobs succeeded. lint-and-test: ruff clean, 140 passed with nothing skipped (Postgres/pgvector service and Node frontend tests included), offline evaluation reproduced; docker: compose config, image build, non-root check. https://github.com/sarthakaleriyaa/Software-Engineering-Assesment/actions/runs/41010392777 | Triggers now include pushes to every branch (path-filtered), so feature branches get CI before their PRs. Each later commit needs its own green run | P2 | Keep CI green on the final commit |
| C3 | Public HTTPS deployment, live for at least two weeks | **FAIL** | No deployment exists. README marks the link as pending. | Required deliverable is missing. | P0 | Deploy using the steps in Section 9. |
| D1 | `DESIGN.md`: requirements, diagram, API contract, data model and trade-offs; updated with changes | **PASS** | Original design was committed first (`a90a493`). Updated diagram, API table, data model and Section 13 explain implementation changes, including the HNSW fix. | None. | — | — |
| D2 | 6+ issues, 4+ PRs, feature branches and meaningful commits | **FAIL** | Ten meaningful commits on `feature/project-architecture` (2026-10-30), pushed to the fork. No GitHub issues or pull requests exist yet | Issues and at least 4 PRs are still required by the brief; the work landed as one branch push rather than via issue-linked PRs | **P0** | Open the issues (drafts in `docs/SUBMISSION_CHECKLIST.md`) and PRs into the fork's `main` |
| D3 | Unit tests, mocked Q&A integration test and cross-user test | **PASS** | Final audit snapshot: 127 tests executed with Postgres + pgvector; 116 passed and 11 skipped without Postgres; 8 additional UI behaviour checks in Node. Includes mocked LLM end-to-end test and real-Postgres isolation tests. | No automated browser tests. Concurrency behaviour (rate-limit race and two workers) is not tested. | P2 | Optional additional coverage. |
| D4 | `v1.0.0` tag with release notes | **FAIL** | `git tag -l` is empty. | Required deliverable is missing. | P1 | Tag the final commit and publish release notes. |
| E1 | README with required sections in order | **PARTIAL** | All required sections are present and ordered. Placeholders are clearly marked. | Live link, video, test credentials and screenshots are missing. | P1 | Fill after deployment. |
| E2 | 5–7 minute demo video, viewable by anyone with the link | **FAIL** | No video exists. | Required deliverable is missing. | P1 | Record and share the demo using the outline in Section 9. |
| E3 | Honest AI usage disclosure | **PARTIAL** | `AI_USAGE.md` is maintained as the dedicated disclosure document and describes AI assistance in project development and documentation. | The author must verify that the disclosure is complete and accurate, including the tools used, the extent of implementation assistance, documentation drafts, initial design and pre-session work. | P1 | Review `AI_USAGE.md` against the actual development history and confirm it before submission. |
| E4 | `RESUME.pdf` in the submission folder | **PASS** | Valid, unencrypted, two-page PDF; committed in `839f85b`. | Contents were not reviewed because they are personal. | — | — |

---

## 3. Evidence Notes

- **Local vs. GitHub:** Every CI-related pass in this report means reproduced locally in containers. It does not mean a GitHub Actions run.
- **Local Docker vs. production:** The Compose stack was verified locally with fresh volumes. No production environment exists yet.
- **Offline vs. real provider:** All scripted evaluation numbers come from `AI_PROVIDER=local` (hashed lexical embeddings plus an extractive answerer). Gemini code paths are covered by mocked unit tests. Live behaviour is limited to the controlled check in row 18 and logged manual use in row 22 of Section 6.

---

## 4. Issues Found in the Audit and Fixes Applied

| # | Severity | Finding | Fix | Regression test |
|---|---|---|---|---|
| 1 | P1 correctness | When the planner chose the HNSW index, pgvector ranked approximately 40 neighbours across all users and filtered by owner afterwards. Reproduced: an owner with a matching chunk received `[]`. `hnsw.iterative_scan` (pgvector 0.8.7) did not recover the result in the same test. This could cause false not-found answers. | `rag.retrieve_chunks` now selects the user's ready chunks in a `MATERIALIZED` CTE, then ranks them by exact distance. Migration `0003` drops the unused HNSW index and is reversible. | `test_hnsw_retrieval_finds_a_users_chunks_behind_many_closer_foreign_chunks`: old query fails with `[] == ['mine']`; new query passes. |
| 2 | P1 security | `/chat` mapped any `ValueError` to 404 and returned its message, potentially exposing internal error text with the wrong status. | Added `DocumentSelectionError`; only that exception maps to 404. | `test_unrelated_value_error_is_a_generic_500_not_404`. |
| 3 | P2 security | Login for an unknown email skipped Argon2 verification, allowing response timing to reveal whether an account existed. | Verify against a dummy hash for unknown emails. | `test_unknown_email_still_runs_password_verification`. |
| 4 | P2 privacy | History retained copies of deleted document passages. | Replace deleted passages with a marker in the same transaction. | `test_delete_scrubs_document_passages_from_history`. |
| 5 | P2 hardening | Browser security headers were missing. | Added `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy` to every response. Added strict CSP on `/`, allowing the inline script only through its hash. Browser verification showed the UI works without CSP violations. | `test_ui_page_has_strict_csp_that_allows_its_own_script`, `test_security_headers_on_api_responses`. |
| 6 | P2 configuration | A weak `JWT_SECRET_KEY` was accepted silently. | Startup warning when the key is shorter than 32 characters. It is not fatal, so existing setups can still start. | `test_short_jwt_secrets_are_flagged`. |
| 7 | Documentation | DESIGN.md and README still described the HNSW index, and DESIGN Section 11 listed Redis. | Corrected documentation to match the implementation. | — |
| 8 | P2 performance | `EXPLAIN ANALYZE` showed the fixed query joining all of a user's chunks back to `document_chunks`, causing a second full sequential scan before applying the limit. With 50,000 other users' chunks, measured database time was 33 ms for a user with 1,000 chunks and 68 ms for 10,000. | Take the top k from materialised distances first, then load those k rows by primary key. New measurements: 12 ms and 45 ms. Results match brute-force exact top-5. The owner filter may still use a sequential scan; DESIGN.md documents this. | Existing HNSW regression test and full Postgres suite. |
| 9 | P1 correctness | Offline retrieval gate at maximum distance 0.90 refused “What is the hotel reimbursement limit per night?” because its answer chunk scored 0.918. The unanswerable “Who is the CEO?” scored 0.867. The gate was therefore not a useful refusal signal. Across 24 questions, removing the gate entirely produced the same correct-refusal rate (0.875). | Offline default raised to 0.95 as a loose cost guard; the answer step decides refusals. Evaluation rerun: hit rate improved from 0.938 to 1.0; other metrics unchanged. Previous results retained as `run1_*`. Gemini default 0.55 remains unvalidated. | `test_hotel_limit_question_reaches_the_answer_step_offline`, `test_hotel_limit_answered_with_citation_and_ceo_refused`. |
| 10 | P1 reliability | Free-tier project reached the daily limit for `gemini-3.8-flash` (21/20 requests). SDK retried HTTP 429 by default, spending another request that was certain to fail. A 429 was reported as a generic outage. | Default generation model changed to `gemini-3.5-flash-lite` in code, `.env.example` and Compose. SDK retries narrowed to selected transient failures. HTTP 429 maps to a rate-limit response and is not retried. HTTP 504 was later removed from the retry list. Added `AIProviderRateLimited`: `/chat` returns 503 with a usage-limit message and `Retry-After: 60`; ingestion fails with a named reason after backed-off retries. | `tests/test_provider_config.py`: nine tests covering defaults, environment override, retry codes, 429 mapping without leaking the body, chat response and worker failure reason. |
| 11 | P1 correctness | `/chat` kept the retrieval transaction open during the model call. Live Gemini generation hit the 15-second server deadline (504), and the SDK retried. The request outlasted Postgres's 15-second `idle_in_transaction_session_timeout`, killing the connection and showing a database error instead of the provider error. A slow successful answer would fail similarly. | `answer_question` commits the read-only retrieval transaction before calling the model. | `test_slow_llm_call_does_not_hold_a_database_transaction` covers slow success and failure. |
| 12 | P1 reliability | After fix 11, Docker logs still showed the same database-unavailable 503 after a 504 at 27.7 seconds. The running API and worker images predated fix 11; changing the API key recreated containers from the old image. The SDK also retried 504 with the same 15-second deadline, and a failed history save could replace the provider error. | Rebuilt and recreated only `api` and `worker` with `--no-deps`; database, migration service and volumes were untouched. Removed 504 from SDK retry list. Error-path history save became best-effort through `_mark_failed`. Timeout remained 15 seconds. | `test_generation_504_is_a_provider_error_not_a_database_error`, `test_provider_error_is_reported_even_if_saving_history_fails`, retry-list test. A throwaway DB test with a 1-second idle limit confirmed 504 gives 503 “AI service temporarily unavailable”; slow success gives 200 without an open transaction. |
| 13 | P1 correctness | Gemini sometimes cites several sources as `[S1, S3]`. The parser matched only `[S1]`, causing fallback to all retrieved sources. One stored answer returned four citations while using two. | `rag.cited_source_ids` now reads every `S<n>` inside any bracket group. | `test_multi_source_citation_groups_are_parsed`, `test_cited_source_ids_formats`. |
| 14 | P2 configuration | Any `AI_PROVIDER` value other than `local`, including a typo, silently meant Gemini. Thinking level was hard-coded. | Added `validate_ai_settings()` at API and worker startup. Added `GEMINI_THINKING_LEVEL` with default `LOW` and supported values `MINIMAL`, `MEDIUM` and `HIGH` to `.env.example` and Compose. | `test_startup_rejects_unknown_ai_settings`, `test_valid_ai_settings_pass`, `test_thinking_level_reaches_the_generation_request`; also checked inside the built image. |
| 15 | P2 frontend | Document-list and history loaders swallowed errors. A brief API outage during processing could stop status polling permanently without showing a message. | Added visible `role="status"` error messages. Document list retries every five seconds while logged in. | `tests/js/ui_behaviour.mjs` (removed in fix 16 when the inline script it tested was replaced; see `frontend_unit.mjs`) via `test_frontend_behaviour.py`: eight behaviours covering 401, 422, 503, malformed responses, network failure and polling. Loader checks fail against the old code. |
| 16 | UI redesign (2026-10-30) | The single inline page was functional but basic, and its inline script forced a hash-based CSP | Rebuilt as a dark single-page app of plain JavaScript modules (`api.js` for all API calls, `ui-logic.js` for pure rules, `app.js` for views) and one stylesheet. Covers sign-in/sign-up with validation and a password toggle, a real `/health` status pill, drag-and-drop upload with progress, live status, select-all, a delete dialog, a Q&A thread that keeps answers, refusals and errors distinct, citations with page or passage, and history. CSP tightened to `script-src 'self'; style-src 'self'` with no inline code. No new dependencies | `tests/js/frontend_unit.mjs` (19 checks on the real modules) via `test_frontend_behaviour.py`; `test_frontend_contract.py` (24 checks: API calls exist, fields exist, no inline code, labels, CSP, assets, static path traversal) |
| 17 | P1 deployment correctness | Uploads lived on a local volume shared by the API and worker. Hosted platforms give each service its own disk, and free tiers an ephemeral one, so uploads would be lost on restart and unreadable by a separate worker | Migration 0004 (additive) adds `document_blobs`; upload stores the bytes in the same transaction as the queued row; the worker reads them; documents uploaded before this still work from disk | `test_upload_returns_202_queued_without_processing_inline`, `test_document_stored_in_database_is_processed_from_its_blob`, `test_legacy_on_disk_document_is_still_deleted_with_its_file`; a container destroyed and recreated still served the document (row 26) |
| 18 | P2 deployment | Free hosting tiers have no background workers or pre-deploy step, and hosted Postgres hands out `postgres://` URLs (psycopg2) | `RUN_EMBEDDED_WORKER=true` runs the same worker loop in the API process; `RUN_MIGRATIONS_ON_START=true` runs `alembic upgrade head` before the server (fails fast); URLs are normalised to psycopg 3; `render.yaml` blueprint | `test_embedded_worker_starts_and_stops_with_the_api`, `test_embedded_worker_is_off_by_default`, `test_hosted_postgres_urls_use_psycopg3`; single-container simulation (row 26) |

### Deliberately not changed

- **`ruff format`:** It would rewrite 35 files. This is a style-only change and would churn the original authored code. `ruff check` is clean locally and configured as a CI step; CI has not yet run on GitHub.
- **No React rewrite:** The assignment brief permits the embedded UI.

---

## 5. Files Changed in This Audit

| File | Reason |
|---|---|
| `backend/app/services/rag.py` | `retrieve_chunks` exact per-user ranking; `DocumentSelectionError`. |
| `backend/app/api/chat.py` | Catch only `DocumentSelectionError` for 404. |
| `backend/app/static/index.html`, `css/app.css`, `js/api.js`, `js/ui-logic.js`, `js/app.js` | UI redesign (fix 16) |
| `backend/app/main.py` | Static assets mount, strict CSP, optional embedded worker (fixes 16, 18) |
| `backend/app/models/document_blob.py`, `backend/migrations/versions/0004_document_blobs.py`, `backend/app/api/documents.py`, `backend/app/services/document_processor.py` | File bytes in Postgres (fix 17) |
| `backend/app/worker.py`, `backend/app/core/database.py`, `backend/Dockerfile`, `render.yaml`, `.env.example` | Embedded worker, URL normalisation, migrate-on-start, Render blueprint (fix 18) |
| `backend/scripts/e2e_check.py`, `backend/tests/js/frontend_unit.mjs`, `backend/tests/test_frontend_*.py` and other tests | End-to-end script and regression tests for fixes 16–18 |
| `backend/migrations/versions/0003_drop_unused_hnsw_index.py` | New migration to drop the unused HNSW index. |
| `backend/app/core/security.py`, `backend/app/api/auth.py` | Dummy-hash verification and weak-secret check. |
| `backend/app/main.py` | Security headers, CSP on `/`, weak-secret warning. |
| `backend/app/api/documents.py` | Scrub deleted passages from history. |
| `backend/tests/test_postgres_integration.py`, `test_chat.py`, `test_auth.py`, `test_documents_api.py`, `test_frontend_contract.py` | Regression tests; migration test now asserts head `0003`. |
| `README.md`, `DESIGN.md`, `AI_USAGE.md`, `docs/SUBMISSION_CHECKLIST.md` | Documentation aligned with implementation, retrieval, security notes and submission requirements. |
| `backend/app/services/rag.py` (later fixes) | Local gate 0.95; `gemini-3.5-flash-lite` default; `AIProviderRateLimited`; retry list without 429/504; commit before model call; grouped citation parsing; configurable thinking level. |
| `backend/app/api/chat.py` (later fixes) | Usage-limit response and best-effort error bookkeeping through `_mark_failed`. |
| `backend/app/services/document_processor.py` | Named rate-limit failure reason. |
| `backend/app/core/config.py`, `backend/app/worker.py`, `backend/app/main.py` | Startup validation of `AI_PROVIDER` and `GEMINI_THINKING_LEVEL`. |
| `backend/app/static/index.html` | Visible loader errors and document-list retry. |
| `.env.example`, `docker-compose.yml` | Model, thinking-level and worker settings. |
| `backend/tests/test_provider_config.py`, `test_rag_logic.py`, `test_frontend_behaviour.py`, `tests/js/ui_behaviour.mjs` (removed in fix 16 when the inline script it tested was replaced; see `frontend_unit.mjs`) | Regression tests for fixes 9–15. |
| `FINAL_AUDIT.md` | This report. |

Earlier work involving worker backoff and heartbeat, migration 0002, UI fixes and contract tests is documented separately in `AI_USAGE.md`.

---

## 6. Verification Commands and Results

### How to read these results

Rows 1–17 are dated snapshots taken at different points during the audit; their counts are historical and are not expected to match. Row 21 is the final automated-test snapshot after fixes 13–15. Row 22 is historical live-provider telemetry, not a new test run. Row 23 is the documentation re-check on 2026-10-30.

Rows 1–22 ran on 2026-10-29 and row 23 on 2026-10-30, on Windows 10, Python 3.14.3 (local virtual environment) and Docker 29.8.2. The CI replay used `python:3.12-slim`. All exit codes were 0 unless noted.

| # | Command / Check | Result |
|---|---|---|
| 1 | `python -m pytest tests -q` | **94 passed, 8 skipped**. Postgres tests skip without `TEST_DATABASE_URL`. |
| 2 | `TEST_DATABASE_URL=postgresql+psycopg://test:test@127.0.0.1:56546/test python -m pytest tests -q` using a throwaway pgvector container | **102 passed**. |
| 3 | `alembic downgrade base` / `upgrade head`, twice, on the same DB | All four operations succeeded; `alembic current` = `0003 (head)`. |
| 4 | `ruff check .` and `ruff check ../evaluation --config ruff.toml` | All checks passed. |
| 5 | `ruff format --check .` | Informational: 35 files would be reformatted; not enforced. |
| 6 | `node --check` on extracted UI script | Passed. |
| 7 | `python evaluation/run_eval.py` twice | Identical to each other and to the committed baseline at that time (0.90 gate, now `run1_threshold_090.json`): hit rate 0.938, correctness 0.938, correct refusals 0.875, 0 payload leaks, 24 questions (8 unanswerable). |
| 8 | `docker compose --env-file <copy of .env.example> config --quiet` | Passed. |
| 9 | `docker compose -p documind-audit … up -d --build --wait`, followed by HTTP smoke script | Migration exit 0; DB, API and worker healthy; image user `documind`, 376 MB; all 18 smoke checks passed. u02 is a known offline-provider miss. |
| 10 | Browser pane on Docker UI | Script runs under CSP; signup and data loading work; no console messages. |
| 11 | Container logs (187 lines) | 0 test passwords, 0 bearer tokens, 0 tracebacks. |
| 12 | Secret-pattern scan of deliverable files | 79 files; only `tests/test_redaction.py` matched, containing a synthetic fixture. Re-run during readiness review. |
| 13 | Earlier CI replay (`lint-and-test` and `docker` jobs in containers) | Green at 95 tests. Not re-run after audit fixes; later rows cover the same steps. |
| 14 | Readiness review: rows 1–4, 6, 7 and Compose config re-run after fix 8 | 94 passed / 8 skipped; 102 passed on Postgres; migration downgrade/upgrade passed at head `0003`; lint clean; `node --check` passed; Compose config passed; evaluation identical to `baseline.json`. |
| 15 | HNSW regression test re-checked during readiness review | Old query fails with `assert [] == ['mine']`, as intended. New query passes. |
| 16 | After fix 9: pytest offline and on Postgres; baseline and chunk400 evaluation | 95 passed / 9 skipped; **104 passed** on Postgres. Baseline hit rate 1.0, correctness 0.938, refusals 0.875, 0 leaks. chunk400 identical except chunks and latency. |
| 17 | After fix 10: pytest offline and on Postgres; lint; Compose config | 104 passed / 9 skipped; **113 passed** on Postgres + pgvector; lint clean; Compose config passed. No live Gemini call made. |
| 18 | Live Gemini check using isolated throwaway Postgres and only `kestrel_expense_policy.txt`; `gemini-3.5-flash-lite` and `gemini-embedding-001`; free tier confirmed by the user | Two embedding calls and one generation call at app level; SDK may have retried the 504 once. `/health` reported `ai_provider: gemini`. Ingestion reached ready: two chunks, 768 dimensions, hotel passage in chunk 0. Retrieval distances were 0.2968 and 0.3534, both below 0.55. Generation failed with 504 `DEADLINE_EXCEEDED`, which surfaced as a database error and led to fix 11. Answer, citation and unanswerable question were not verified live. |
| 19 | After fix 11: pytest on Postgres and offline; lint | **115 passed** on Postgres + pgvector; 104 passed / 11 skipped offline; lint clean. |
| 20 | After fix 12: pytest offline and on Postgres; lint; running stack after rebuild | 106 passed / 11 skipped; **117 passed** on Postgres + pgvector; lint clean. Running API and worker contained the fixes. `/health` returned 200 with `ai_provider: gemini`; DB counts, migration head and uploads were unchanged. No live Gemini call. |
| 21 | Final code audit after fixes 13–15 | `pytest`: 116 passed / 11 skipped offline; **127 passed** on Postgres + pgvector; UI behaviour 8/8; `node --check` passed; Ruff clean; Alembic downgrade/upgrade passed at head `0003`; model/migration comparison showed 0 differences; evaluation matched `baseline.json`; Compose config passed; image builds as non-root; startup validation works inside it. Running stack was not rebuilt and remained on image `acd5004046d6`. |
| 22 | Gemini latency evidence from logs and stored token counts; no new live calls | With a 30-second timeout, 13 recorded attempts: 7 succeeded (2.5–26.9 seconds, most around 23–24 seconds) and 6 reached the deadline (27.5–29.3 seconds). Successful answers used 689–1,329 input tokens and 18–131 output tokens (output includes thinking). Captured request was well-formed: `X-Server-Timeout: 30`, `maxOutputTokens 1024`, `thinkingConfig LOW`, separate `systemInstruction`, 254-character prompt. Logs are consistent with slow provider responses but cannot establish the root cause or prove that free-tier queueing, model or region caused the latency. `MINIMAL` is supported by the SDK and documented for this model, but there is insufficient evidence that it would resolve latency. |
| 23 | Documentation re-check on 2026-10-30: `python -m pytest tests -q`; `pytest --collect-only`; `node tests/js/ui_behaviour.mjs`; `ruff check`; evaluation result files and `dataset.json` read directly | **116 passed, 11 skipped** offline; 127 tests collected (11 skipped Postgres tests require Docker); UI behaviour 8/8; lint clean; every metric quoted in `EVALUATION.md` and README matches `evaluation/results/*.json`; dataset contains 24 questions, 8 unanswerable and 2 injection attacks. Postgres suite and Docker checks were not re-run because Docker Desktop was not running. The running stack's state could not be checked. |
| 24 | After fixes 16–18 (2026-10-30): `python -m pytest tests -q` (offline and with a throwaway pgvector DB); `ruff check`; `node tests/js/frontend_unit.mjs`; `alembic` round trip; `compare_metadata` | **129 passed, 11 skipped** offline; **140 passed** on Postgres + pgvector; frontend modules 19/19; lint clean; `downgrade base → head → 0003 → head` ok (head `0004`); schema vs models 0 differences |
| 25 | Browser walkthrough of the redesigned UI on an isolated Compose stack (offline provider) | Sign-up validation (short and mismatched passwords) and success; health pill "All systems operational" from a real 200; a real 3-page PDF dropped onto the drop zone (with an `.exe`, which was rejected before upload) went queued → ready with 3 pages; the answer "within 1 hour of discovery" cited page 2 with the exact sentence; an unanswerable question shown as "Not found in your documents" with no citations; selection scope; history; delete through the dialog (API then returned 0 documents, and the test DB had 0 chunks for that user); no console errors; no horizontal overflow at 375 px and 1440 px. Two cosmetic issues found and fixed (history spacing, header wrapping) |
| 26 | `scripts/e2e_check.py` against the isolated Compose stack, and against a single-container Render simulation (embedded worker, migrations on start, `postgres://` URL, `PORT=10000`) | **17/17** checks passed on both. In the simulation, migrations 0001→0004 ran at start; after the web container was destroyed and recreated, the uploaded PDF was still ready and still answered with its page-2 citation |
| 27 | Your existing database (Compose project `sarthakaleriyaa`), read-only checks | At head `0003` with all tables present; schema vs models 0 differences (before 0004); sequences consistent; 2 users, 4 ready documents, 12 chunks (all embedded), 39 history rows. A verified `pg_dump` backup was taken to the git-ignored `backups/` folder. The missing-`alembic_version` / `DuplicateTable` problem described in the task did not exist in this database, so no repair or stamping was done. Migration 0004 has **not** been applied to it yet (it applies on the next `docker compose up --build`) |

### Docker image note

The Docker image was not rebuilt after fix 8. The smoke run in row 9 used the previous version of the retrieval query. Fix 8 changes only SQL; rows 2 and 15 cover that change against real Postgres + pgvector.

Temporary Docker resources were removed after the run and verified gone. Pre-existing containers, volumes and images were untouched.

---

## 7. Known Limitations

- Retrieval computes exact distances for all of a user's chunks: approximately 45 ms of database time at 10,000 chunks, measured locally. Expect it to grow linearly beyond that. The owner filter may scan the whole chunk table (`DESIGN.md`, Section 13).
- Uploads sit on a shared volume, so the API and worker must share a disk.
- No OCR is implemented; scanned PDFs fail with a clear reason.
- Injection defences are layered heuristics, not guarantees.
- Evaluation uses a small dataset of 24 questions, string-match grading and the offline provider only.

---

## 8. Security Observations and Open Risks

- **Isolation:** Enforced in SQL on every query and tested on real Postgres. Foreign IDs are indistinguishable from missing ones.
- **Tokens:** Stored in `sessionStorage`. XSS risk is reduced through `textContent`-only rendering and CSP, but tokens cannot be revoked before expiry (30 minutes).
- **Public endpoints:** `/health` and `/docs` are public, as required. `/health` reveals pgvector version and queue depth, which is a minor information exposure.
- **Signup 409:** Reveals whether an email already exists; this is a usability trade-off.
- **Rate limits:** Apply only to `/chat`; login and upload have no throttle.
- **Document privacy:** Uploaded documents are sent to Google when `AI_PROVIDER=gemini`. README and `.env.example` disclose this. Repeat this disclosure in the demo.

---

## 9. Outstanding Submission Items (Manual)

### Deployment (C3)

1. Choose a host that runs Docker images, offers Postgres with pgvector, supports a worker process and provides persistent disk storage. Examples include a VM, Render or Railway.
2. Create Postgres and enable the `vector` extension.
3. Deploy `backend/` twice:
   - Web service using the default command and respecting `$PORT`.
   - Worker using `python -m app.worker`.
   - Both services must mount the same uploads disk.
4. Set `.env.example` variables as host secrets, including a strong `JWT_SECRET_KEY`, `AI_PROVIDER=gemini` and the Gemini key.
5. Run `alembic upgrade head` as the release command.
6. Confirm `https://<host>/health` returns 200.
7. Create a test account and upload files from `evaluation/documents/`.
8. Record the wake-up time if the host sleeps.

### Demo video (E2), 5–7 minutes

- **0:00–2:00:** On the live link, sign up, upload the PDF and watch status change; ask a cited question and an unanswerable question; show the injection document and i01; show a second user seeing no documents from the first account.
- **2:00–4:00:** Show the architecture diagram, worker queue (`SKIP LOCKED`) and question flow.
- **4:00–5:30:** Explain `rag.retrieve_chunks` and the HNSW finding.
- **5:30–7:00:** Show the evaluation table, failures and next steps.
- Upload the video to Google Drive with “Anyone with the link can view” permission and test the link in an incognito window.

### Other manual items

- **D2:** Commit work in slices using feature branches. `docs/SUBMISSION_CHECKLIST.md` contains a proposed branch/PR split. Open 6+ issues and 4+ PRs in the fork.
- **C2:** Push to GitHub and confirm the Actions workflow is green.
- **D4:** Tag `v1.0.0` and publish release notes.
- **E1:** Add the live link, video, test credentials and screenshots to README.
- **E3:** Review and confirm `AI_USAGE.md` against the actual development history.
- **B1–B4:** With permission, run the Gemini evaluation and update `EVALUATION.md`. This sends the evaluation documents to Google when using the Gemini provider.

---

## 10. Prioritised Checklist

The detailed, ordered version is maintained in `docs/SUBMISSION_CHECKLIST.md`.

1. [ ] **P0:** Restore the repository-root `.DS_Store` before committing. `git status` showed it deleted, and it is outside the submission folder, which the brief forbids changing. Run `git restore .DS_Store`.
2. [ ] **P0:** Commit work in meaningful slices on feature branches (D2). Local Git config name is `sarthakaleriyaa` (checked 2026-10-30), and existing commits are authored “Sarthak Aleriya”. Confirm the configured email is linked to the GitHub account.
3. [ ] **P0:** Deploy and confirm `/health` returns 200 over HTTPS (C3).
4. [ ] **P1:** Push and confirm CI is green (C2). Open issues and PRs (D2).
5. [ ] **P1:** Run Gemini evaluation and update `EVALUATION.md` (B1–B4).
6. [ ] **P1:** Record and share demo video; fill in README links, credentials and screenshots (E1, E2).
7. [ ] **P1:** Review and confirm `AI_USAGE.md` (E3).
8. [ ] **P1:** Tag `v1.0.0` with release notes (D4); open the upstream PR using the template.
9. [ ] **P2 — local tidy-up, optional:**
   - [x] Broken copied `backend/.venv` deleted (checked 2026-10-30).
   - [x] Empty `frontend/`, `worker/` and `scripts/` folders deleted (checked 2026-10-30).
   - [ ] One stray file remains in `backend/storage/uploads/` (ignored by Git; delete if not needed).

---

## 11. Be Ready to Explain

- **FastAPI + Postgres/pgvector:** One database stores users, chunks, vectors, job queue and rate-limit counts. Typed validation and OpenAPI documentation are provided by FastAPI.
- **Worker:** `documents` acts as the queue. The worker claims a row using `SELECT … FOR UPDATE SKIP LOCKED`, then processes it. Provider outages retry using `available_at` backoff. A heartbeat thread feeds `/health` and the container health check. Stale-job recovery uses `updated_at`, refreshed after each embedding batch.
- **Chunking and retrieval:** 1000/150 character settings, word boundaries and no chunking across PDF pages. Exact cosine ranking within the user's chunks, top 5, followed by a distance threshold. Explain why the HNSW index was removed and possible scaling options such as tenant partitioning and per-tenant indexes.
- **Grounding and citations:** Retrieval gate; system rules separate from `<source id="S#">` blocks; model cites `[S#]`; only cited sources are returned; refusals are normalised.
- **Prompt injection:** Escaping, untrusted-data rules, detection logging, canary and output guard. The initial evaluation exposed an output-guard gap. These protections are heuristics and cannot guarantee resistance.
- **Authentication and isolation:** Argon2, JWT `sub`/`exp`, `user_id` in every relevant SQL `WHERE` clause, 404 for foreign IDs and history scrubbing on delete.
- **Evaluation:** Explain how each metric is computed, why string matching and the offline provider limit conclusions, and why the chunk-size result (one question) did not justify a change.
- **Trade-offs at scale:** Object storage for uploads, a real broker or `LISTEN/NOTIFY`, a token-bucket rate limiter, hybrid search and re-ranking.