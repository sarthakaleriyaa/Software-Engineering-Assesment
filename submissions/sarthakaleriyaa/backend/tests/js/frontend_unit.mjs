// Unit tests for the real frontend modules (app/static/js/api.js and ui-logic.js),
// run in Node with fake fetch, storage and XMLHttpRequest.
// Usage: node frontend_unit.mjs <path to app/static/js>
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";

const jsDir = process.argv[2];
const { createApi, ApiError } = await import(pathToFileURL(path.join(jsDir, "api.js")).href);
const logic = await import(pathToFileURL(path.join(jsDir, "ui-logic.js")).href);

const results = [];
async function test(name, fn) {
  try { await fn(); results.push(["PASS", name]); }
  catch (err) { results.push(["FAIL", `${name}: ${err.message}`]); }
}

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return { getItem: (k) => data[k] ?? null, setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; } };
}

function response(status, body, headers = {}) {
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: (n) => headers[n] ?? null },
    json: async () => { if (body === "malformed") throw new SyntaxError("Unexpected token <"); return body; },
  };
}

function apiWith(responses, { token = null, onUnauthorized = () => {} } = {}) {
  const calls = [];
  const storage = memoryStorage(token ? { documind_token: token } : {});
  const api = createApi({
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      const next = responses.shift();
      if (next === "network") throw new TypeError("Failed to fetch");
      return next;
    },
    storage,
    xhrFactory: () => { throw new Error("no xhr in this test"); },
    onUnauthorized,
  });
  return { api, calls, storage };
}

// ---------------- api.js ----------------
await test("503 detail string is shown as-is", async () => {
  const { api } = apiWith([response(503, { detail: "The AI service is temporarily unavailable. Please try again shortly." })]);
  await assert.rejects(api.ask("q"), (e) => e instanceof ApiError && e.status === 503 && /temporarily unavailable/.test(e.message));
});

await test("422 validation details are joined into one message", async () => {
  const { api } = apiWith([response(422, { detail: [{ msg: "String should have at most 4000 characters" }, { msg: "Field required" }] })]);
  await assert.rejects(api.ask("q"), /at most 4000 characters; Field required/);
});

await test("malformed error body falls back to the status code", async () => {
  const { api } = apiWith([response(500, "malformed")]);
  await assert.rejects(api.listDocuments(), /Request failed \(500\)/);
});

await test("network failure gives a readable message", async () => {
  const { api } = apiWith(["network"]);
  await assert.rejects(api.listDocuments(), (e) => e.status === 0 && /Cannot reach the DocuMind server/.test(e.message));
});

await test("401 with a stored token clears it and reports an expired session", async () => {
  let notified = 0;
  const { api, storage } = apiWith([response(401, { detail: "Invalid or missing authentication token" })], { token: "t", onUnauthorized: () => notified++ });
  await assert.rejects(api.listDocuments(), /session has expired/);
  assert.equal(storage.getItem("documind_token"), null);
  assert.equal(notified, 1);
});

await test("401 at login (no token) keeps the server's message", async () => {
  let notified = 0;
  const { api } = apiWith([response(401, { detail: "Invalid email or password" })], { onUnauthorized: () => notified++ });
  await assert.rejects(api.login("a@b.co", "wrong-password"), /Invalid email or password/);
  assert.equal(notified, 0);
});

await test("429 exposes Retry-After", async () => {
  const { api } = apiWith([response(429, { detail: "Rate limit exceeded" }, { "Retry-After": "60" })]);
  await assert.rejects(api.ask("q"), (e) => e.status === 429 && e.retryAfter === 60);
});

await test("bearer token is sent when signed in", async () => {
  const { api, calls } = apiWith([response(200, [])], { token: "abc" });
  await api.listDocuments();
  assert.equal(calls[0].init.headers.Authorization, "Bearer abc");
});

await test("ask() searches all documents when none are selected, and only the selected ones otherwise", async () => {
  const { api, calls } = apiWith([response(200, { answer: "a", citations: [] }), response(200, { answer: "a", citations: [] })], { token: "t" });
  await api.ask("What?", []);
  await api.ask("What?", [3, 7]);
  assert.deepEqual(JSON.parse(calls[0].init.body), { question: "What?" });
  assert.deepEqual(JSON.parse(calls[1].init.body), { question: "What?", document_ids: [3, 7] });
});

await test("delete returns null for 204", async () => {
  const { api, calls } = apiWith([response(204, null)], { token: "t" });
  assert.equal(await api.deleteDocument(5), null);
  assert.equal(calls[0].url, "/documents/5");
  assert.equal(calls[0].init.method, "DELETE");
});

await test("health keeps the JSON body of a 503 and reports unreachable on network error", async () => {
  const body = { status: "degraded", checks: { worker: { status: "error" } } };
  const { api } = apiWith([response(503, body), "network"]);
  assert.deepEqual(await api.health(), { status: 503, body });
  assert.deepEqual(await api.health(), { unreachable: true });
});

await test("upload reports progress, sends the token and returns the queued document", async () => {
  const progress = [];
  let sent = null;
  class FakeXhr {
    constructor() { this.headers = {}; this.upload = {}; }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(k, v) { this.headers[k] = v; }
    getResponseHeader() { return null; }
    send(form) {
      sent = this;
      this.upload.onprogress({ lengthComputable: true, loaded: 50, total: 100 });
      this.upload.onprogress({ lengthComputable: true, loaded: 100, total: 100 });
      this.status = 202;
      this.responseText = JSON.stringify({ id: 9, filename: "a.pdf", status: "queued" });
      this.onload();
    }
  }
  globalThis.FormData = class { append() {} };
  const api = createApi({ fetchImpl: async () => { throw new Error("unused"); }, storage: memoryStorage({ documind_token: "tok" }), xhrFactory: () => new FakeXhr() });
  const doc = await api.uploadDocument({ name: "a.pdf" }, (p) => progress.push(p));
  assert.deepEqual(progress, [50, 100]);
  assert.equal(sent.method, "POST");
  assert.equal(sent.url, "/documents");
  assert.equal(sent.headers.Authorization, "Bearer tok");
  assert.equal(doc.status, "queued");
});

await test("upload surfaces the server's 413 message", async () => {
  class FakeXhr {
    constructor() { this.upload = {}; }
    open() {} setRequestHeader() {} getResponseHeader() { return null; }
    send() { this.status = 413; this.responseText = JSON.stringify({ detail: "File size must not exceed 10 MB" }); this.onload(); }
  }
  const api = createApi({ fetchImpl: async () => {}, storage: memoryStorage(), xhrFactory: () => new FakeXhr() });
  await assert.rejects(api.uploadDocument({ name: "big.pdf" }), /must not exceed 10 MB/);
});

// ---------------- ui-logic.js ----------------
await test("file validation: type, empty and size", () => {
  assert.equal(logic.validateFile({ name: "a.PDF", size: 10 }).ok, true);
  assert.equal(logic.validateFile({ name: "notes.md", size: 10 }).ok, true);
  assert.match(logic.validateFile({ name: "run.exe", size: 10 }).reason, /Only PDF/);
  assert.match(logic.validateFile({ name: "a.txt", size: 0 }).reason, /empty/);
  assert.match(logic.validateFile({ name: "a.txt", size: logic.MAX_UPLOAD_BYTES + 1 }).reason, /larger than 10 MB/);
});

await test("password and email rules match the backend (8–128 characters)", () => {
  assert.deepEqual(logic.passwordProblems("12345678"), []);
  assert.match(logic.passwordProblems("short")[0], /at least 8/);
  assert.match(logic.passwordProblems("x".repeat(129))[0], /at most 128/);
  assert.match(logic.passwordProblems("longenough1", "different1")[0], /do not match/);
  assert.equal(logic.emailLooksValid("user@example.com"), true);
  assert.equal(logic.emailLooksValid("not-an-email"), false);
});

await test("health is 'operational' only when the server reports every check ok", () => {
  const ok = { status: 200, body: { status: "ok", checks: { database: { status: "ok" }, worker: { status: "ok" } } } };
  assert.equal(logic.summarizeHealth(ok).state, "ok");
  const degraded = { status: 503, body: { status: "degraded", checks: { database: { status: "ok" }, worker: { status: "error" }, vector_store: { status: "error" } } } };
  const summary = logic.summarizeHealth(degraded);
  assert.equal(summary.state, "degraded");
  assert.match(summary.label, /worker, vector store/);
  assert.equal(logic.summarizeHealth({ unreachable: true }).state, "down");
  assert.equal(logic.summarizeHealth({ status: 200, body: { status: "ok", checks: { worker: { status: "error" } } } }).state, "degraded");
});

await test("only the four backend statuses get labels; polling only while queued/processing", () => {
  assert.deepEqual(["queued", "processing", "ready", "failed"].map((s) => logic.statusInfo(s).label), ["Queued", "Processing", "Ready", "Failed"]);
  assert.deepEqual(["queued", "processing", "ready", "failed"].map((s) => logic.statusInfo(s).busy), [true, true, false, false]);
  assert.equal(logic.statusInfo("weird").tone, "neutral");
});

await test("citations show the real page number, or a 1-based passage when there is none", () => {
  assert.equal(logic.citationLocation({ page_number: 2, chunk_index: 4 }), "Page 2");
  assert.equal(logic.citationLocation({ page_number: null, chunk_index: 0 }), "Passage 1");
});

await test("scope label, answer kind, suggestions and usage formatting", () => {
  assert.equal(logic.scopeLabel(0, 3), "Searching all 3 ready documents");
  assert.equal(logic.scopeLabel(1, 3), "Searching 1 selected document");
  assert.equal(logic.scopeLabel(0, 0), "No ready documents yet");
  assert.equal(logic.answerKind({ refused: true }), "refused");
  assert.equal(logic.answerKind({ refused: false }), "answered");
  assert.deepEqual(logic.suggestedQuestions([]), []);
  assert.match(logic.suggestedQuestions([{ filename: "kestrel_expense_policy.txt" }])[0], /kestrel expense policy/);
  assert.equal(logic.usageSummary({ latency_ms: 2400, input_tokens: 10, output_tokens: 5, estimated_cost_usd: null }), "2.4 s · 10 in / 5 out tokens");
  assert.equal(logic.formatBytes(2048), "2.0 KB");
  assert.equal(logic.formatBytes(5 * 1024 * 1024), "5.0 MB");
});

for (const [status, name] of results) console.log(status, name);
console.log(`${results.filter(([s]) => s === "PASS").length}/${results.length} passed`);
process.exit(results.some(([s]) => s === "FAIL") ? 1 : 0);
