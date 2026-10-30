"""End-to-end check of a running DocuMind deployment over HTTP.

Registers a new test account, uploads a real 3-page PDF, waits for the worker,
asks an answerable question (answer on page 2) and an unanswerable one,
deletes the document and confirms it is gone. Existing users and documents
are never touched.

Usage (from backend/):
    python scripts/e2e_check.py --base-url http://127.0.0.1:8000
    python scripts/e2e_check.py --base-url https://<your-app>.onrender.com --ready-timeout 300
Exit code 0 only if every check passes.
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import httpx

PDF = Path(__file__).resolve().parents[2] / "evaluation" / "documents" / "kestrel_it_security_policy.pdf"
ANSWERABLE = "How quickly must a suspected security incident be reported?"
UNANSWERABLE = "What was the company's annual revenue last year?"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ready-timeout", type=int, default=120, help="seconds to wait for processing")
    parser.add_argument("--request-timeout", type=float, default=90.0, help="per request (cold starts, slow LLMs)")
    args = parser.parse_args()

    c = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.request_timeout, follow_redirects=True)

    page = c.get("/")
    check(page.status_code == 200 and "<title>DocuMind</title>" in page.text, "UI page loads", f"HTTP {page.status_code}")
    check(page.status_code == 200 and "script-src 'self'" in page.headers.get("Content-Security-Policy", ""), "UI served with strict CSP")
    if args.base_url.startswith("https://"):
        check(str(page.url).startswith("https://"), "served over HTTPS", str(page.url))

    health = c.get("/health")
    body = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
    checks = {k: v.get("status") for k, v in body.get("checks", {}).items()}
    check(health.status_code == 200 and body.get("status") == "ok", "health: all dependencies ok", f"{health.status_code} {checks} provider={body.get('ai_provider')}")

    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    password = f"E2e-{uuid.uuid4().hex[:12]}"
    signup = c.post("/auth/signup", json={"email": email, "password": password})
    check(signup.status_code == 201, "register a new test account", email)
    login = c.post("/auth/login", json={"email": email, "password": password})
    if not check(login.status_code == 200, "log in", f"HTTP {login.status_code}"):
        return 1
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

    upload = c.post("/documents", files={"file": (PDF.name, PDF.read_bytes(), "application/pdf")}, headers=auth)
    if not check(upload.status_code == 202 and upload.json()["status"] == "queued", "upload a real PDF (202 queued)", f"HTTP {upload.status_code}"):
        return 1
    doc_id = upload.json()["id"]

    deadline = time.time() + args.ready_timeout
    seen = []
    doc = {}
    while time.time() < deadline:
        doc = c.get(f"/documents/{doc_id}", headers=auth).json()
        if not seen or seen[-1] != doc["status"]:
            seen.append(doc["status"])
        if doc["status"] in ("ready", "failed"):
            break
        time.sleep(2)
    check(doc.get("status") == "ready", "worker processes the PDF to ready", " -> ".join(seen) + (f" ({doc.get('failure_reason')})" if doc.get("failure_reason") else ""))
    check(doc.get("page_count") == 3, "page count recorded", str(doc.get("page_count")))

    answer = c.post("/chat", json={"question": ANSWERABLE}, headers=auth)
    a = answer.json()
    text = (a.get("answer") or a.get("detail") or "").lower()
    check(answer.status_code == 200 and not a.get("refused"), "answerable question answered", f"HTTP {answer.status_code}: {text[:140]}")
    check("1 hour" in text or "one hour" in text, "answer matches the source (within 1 hour)")
    citations = a.get("citations") or []
    check(any(ci["filename"] == PDF.name and ci["page_number"] == 2 for ci in citations), "citation names the PDF and page 2",
          "; ".join(f"{ci['filename']} p{ci['page_number']}" for ci in citations))
    check(any("within 1 hour of discovery" in ci["passage"] for ci in citations), "cited passage contains the supporting sentence")

    refusal = c.post("/chat", json={"question": UNANSWERABLE}, headers=auth)
    r = refusal.json()
    check(refusal.status_code == 200 and r.get("refused") is True and not r.get("citations"), "unanswerable question is refused honestly",
          (r.get("answer") or r.get("detail") or "")[:100])

    deleted = c.delete(f"/documents/{doc_id}", headers=auth)
    check(deleted.status_code == 204, "delete the document", f"HTTP {deleted.status_code}")
    check(c.get(f"/documents/{doc_id}", headers=auth).status_code == 404, "deleted document is no longer available")
    check(all(d["id"] != doc_id for d in c.get("/documents", headers=auth).json()), "deleted document is gone from the list")

    after = c.post("/chat", json={"question": ANSWERABLE}, headers=auth).json()
    check(after.get("refused") is True and not after.get("citations"), "deleted content is never cited again")

    passed = sum(ok for ok, _, _ in results)
    print(f"\n{passed}/{len(results)} checks passed against {args.base_url}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
