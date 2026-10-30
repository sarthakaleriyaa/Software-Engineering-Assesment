# Submission Checklist (Local Preparation)

The work in this repository was done in local mode only. **No remote actions were performed:** nothing was pushed, and no issues, pull requests, tags, releases, or deployments were created. The author must complete these steps on their own GitHub fork and hosting account. The drafts below are ready to copy.

## Remaining steps, in order

Priorities follow `FINAL_AUDIT.md` (P0 = blocks submission).

**1. Git history (D2, P0)**

- [ ] Restore the repository-root `.DS_Store` (`git restore .DS_Store`). `git status` shows it deleted, and the brief forbids changing files outside your submission folder.
- [ ] Confirm `git config user.email` is the email linked to your GitHub account (the name is now `sarthakaleriyaa`, checked 2026-10-30).
- [ ] Keep the fork **public**; enable **Issues** (Settings → General → Features).
- [ ] Open the issues below (at least 6) *before* the matching commits.
- [ ] Commit in small, meaningful slices on the feature branches below (at least 4 PRs into your fork's `main`, each linked to its issue). Use real current dates; do not backdate. AI assistance is disclosed in `AI_USAGE.md`.
- [ ] Before each commit, check that `git status` shows no `.env`, `.venv*`, `storage/`, or cache files.

**2. CI on GitHub (C2, P1)**

- [ ] Push, and confirm `sarthakaleriyaa-ci` runs and is green on every PR and on the final `main` commit. Fix anything that differs from the local replay.

**3. Public HTTPS deployment (C3, P0)**

- [ ] Deploy Postgres with pgvector, the API (web service), and the worker (`python -m app.worker`), sharing one uploads disk. Run `alembic upgrade head` as the release step. Steps: `FINAL_AUDIT.md`, section 9.
- [ ] Confirm `https://<host>/health` returns 200 with all four checks `ok`, and that `/docs` loads.
- [ ] Keep it up for at least 2 weeks after submission. If the host sleeps when idle, note the wake-up time in the README.

**4. Gemini evaluation (B1–B4, P1). Requires your explicit permission: it sends the five evaluation documents and 24 questions to Google.**

- [ ] Run `python evaluation/run_eval.py --provider gemini --allow-remote --label gemini-baseline`, then repeat with `--chunk-size 400`. Update `EVALUATION.md` with the real numbers; until then it must keep saying "offline only".

**5. README and demo (E1, E2, P1)**

- [ ] Create the test account on the live site and upload `evaluation/documents/*`. Put the credentials in the README (test data only).
- [ ] Add screenshots and the live link.
- [ ] Record the 5–7 minute video (outline: `FINAL_AUDIT.md`, section 9). Upload to Google Drive as "Anyone with the link can view", **test it in an incognito window**, and add the link to the README.

**6. AI usage (E3, P1)**

- [ ] Review `AI_USAGE.md`: keep your own statement, check the Claude Code record, and answer the "To be confirmed by the author" points. Do not reduce the disclosure below what was actually used.

**7. Resume (E4)**

Already present (`RESUME.pdf`, valid 2-page PDF). Only replace it if it is out of date.

**8. Release and submission (D4, P1)**

- [ ] Tag the final commit `v1.0.0` and publish a GitHub release using the note below.
- [ ] Open **one** PR from your fork's `main` to the upstream repository using the template (draft below). Do not close and reopen it. Confirm it changes nothing outside `submissions/sarthakaleriyaa/` and `.github/workflows/sarthakaleriyaa-ci.yml`.
- [ ] Final scan: no secrets in the repository or its history.

## Draft issues

1. **Schema migrations with Alembic and pgvector:** initial migration (0001), retry backoff (0002), `failure_reason`, `attempts`, question history, worker heartbeats.
2. **Background ingestion worker:** Postgres queue (`SKIP LOCKED`), statuses, retries, stale-job recovery, 202 upload.
3. **Page-aware extraction and chunking:** PDF page numbers in citations, word-boundary chunks.
4. **Grounded answering and prompt-injection defences:** system/source separation, refusal, citation filtering, output guard.
5. **Question history, usage and per-user rate limiting:** tokens, latency, cost, 429 with `Retry-After`.
6. **Observability:** `/health` checks (DB, pgvector, worker, queue), JSON logs, request IDs.
7. **Minimal web UI:** sign up, upload, live status, ask with citations, history.
8. **Evaluation:** dataset (24 questions), `run_eval.py`, chunk-size experiment, `EVALUATION.md`.
9. **Docker and CI:** multi-stage non-root image, Compose with migrate/API/worker, CI with Postgres, lint, Docker build.
10. **Documentation:** README, DESIGN changes, AI usage.
11. **Final audit fixes:** exact per-user retrieval (drop HNSW, migration 0003), selection error handling, login timing, history scrubbing on delete, security headers and CSP, `FINAL_AUDIT.md`.

## Suggested branches and PRs

| Branch | Issues |
|---|---|
| `feature/migrations-and-worker` | 1, 2 |
| `feature/rag-grounding` | 3, 4 |
| `feature/history-ratelimit-observability` | 5, 6 |
| `feature/web-ui` | 7 |
| `feature/evaluation` | 8 |
| `feature/docker-ci-docs` | 9, 10 |
| `fix/final-audit` | 11 |

## Draft release note (`v1.0.0`)

> First complete version of DocuMind. JWT authentication with per-user isolation; PDF/TXT/Markdown upload with a background worker on a Postgres queue; pgvector retrieval with grounded, cited answers and explicit refusals; layered prompt-injection defences; question history with usage; rate limiting; health checks; JSON logs with request IDs; Docker Compose; CI; evaluation set and report.

## Draft upstream PR description

| Field | Answer |
|---|---|
| Title | `[SWE Intern]` or `[AI Engineer Intern]` – *Full Name* – DocuMind |
| Live link | *HTTPS URL* |
| Demo video | *Drive link* |
| Test credentials | *email / password* |
| Resume | `submissions/sarthakaleriyaa/RESUME.pdf` |
| Release | *link to v1.0.0 release* |

**Summary:** DocuMind is a FastAPI service where users upload PDF/TXT/Markdown documents and ask questions answered only from them, with passage- and page-level citations and an explicit "not found" refusal. Ingestion runs in a separate worker that uses the Postgres `documents` table as its queue (`FOR UPDATE SKIP LOCKED`). Retrieval ranks only the user's own chunks by exact pgvector cosine distance. An earlier HNSW index was removed after a test showed it could hide a user's results. Prompt-injection defences are layered: system rules are separated from escaped, untrusted source blocks, and an output guard checks answers. A 24-question evaluation (including injection attacks) and a chunk-size experiment are reported in `EVALUATION.md`.

**Not completed:** *List anything still open at submission time, e.g. the Gemini evaluation run.*
