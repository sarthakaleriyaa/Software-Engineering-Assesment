// Pure UI rules (no DOM, no network) so they can be unit-tested in Node.

export const ALLOWED_EXTENSIONS = [".pdf", ".md", ".txt"];
// Mirrors the server default (MAX_UPLOAD_MB=10). The server stays authoritative:
// if it is configured differently, its 413 message is shown instead.
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MIN_PASSWORD_LENGTH = 8;
export const MAX_PASSWORD_LENGTH = 128;
export const MAX_QUESTION_LENGTH = 4000;

export function extensionOf(name) {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

export function validateFile(file) {
  if (!ALLOWED_EXTENSIONS.includes(extensionOf(file.name))) {
    return { ok: false, reason: "Only PDF, Markdown (.md) and plain-text (.txt) files are supported." };
  }
  if (file.size === 0) return { ok: false, reason: "The file is empty." };
  if (file.size > MAX_UPLOAD_BYTES) {
    return { ok: false, reason: `The file is larger than ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB.` };
  }
  return { ok: true, reason: "" };
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatRelativeTime(iso, now = Date.now()) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d ago`;
  return new Date(then).toLocaleDateString();
}

// Only the four statuses the backend defines; anything else is shown as-is.
const STATUS = {
  queued: { label: "Queued", tone: "pending", busy: true },
  processing: { label: "Processing", tone: "pending", busy: true },
  ready: { label: "Ready", tone: "ok", busy: false },
  failed: { label: "Failed", tone: "bad", busy: false },
};

export function statusInfo(status) {
  return STATUS[status] ?? { label: String(status ?? "unknown"), tone: "neutral", busy: false };
}

export function emailLooksValid(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

export function passwordProblems(password, confirm) {
  const problems = [];
  if (password.length < MIN_PASSWORD_LENGTH) problems.push(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
  if (password.length > MAX_PASSWORD_LENGTH) problems.push(`Use at most ${MAX_PASSWORD_LENGTH} characters.`);
  if (confirm !== undefined && password !== confirm) problems.push("The passwords do not match.");
  return problems;
}

// Maps the real /health response. "Operational" only when the server says so.
export function summarizeHealth(result) {
  if (!result || result.unreachable) {
    return { state: "down", label: "Server unreachable", detail: "The API did not respond." };
  }
  const checks = (result.body && result.body.checks) || {};
  const failing = Object.entries(checks).filter(([, c]) => c && c.status !== "ok").map(([name]) => name);
  if (result.status === 200 && result.body && result.body.status === "ok" && failing.length === 0) {
    return { state: "ok", label: "All systems operational", detail: "Database, vector store, worker and queue are healthy." };
  }
  const names = failing.map((n) => n.replace("_", " "));
  return {
    state: "degraded",
    label: failing.length ? `Degraded: ${names.join(", ")}` : "Degraded",
    detail: failing.length ? `Not healthy: ${names.join(", ")}.` : "The health check did not report ok.",
  };
}

export function citationLocation(citation) {
  return citation.page_number ? `Page ${citation.page_number}` : `Passage ${citation.chunk_index + 1}`;
}

export function scopeLabel(selectedCount, readyCount) {
  if (selectedCount > 0) return `Searching ${selectedCount} selected document${selectedCount === 1 ? "" : "s"}`;
  if (readyCount === 0) return "No ready documents yet";
  return `Searching all ${readyCount} ready document${readyCount === 1 ? "" : "s"}`;
}

export function answerKind(response) {
  return response && response.refused ? "refused" : "answered";
}

// Starter prompts. They are only templates for the user to send; they never
// claim anything about document contents.
export function suggestedQuestions(readyDocs) {
  if (!readyDocs.length) return [];
  const name = readyDocs[0].filename.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ");
  return [
    `What are the key points in ${name}?`,
    "What limits, amounts or deadlines are mentioned?",
    "Which rules apply to employees?",
  ];
}

export function usageSummary(usage) {
  if (!usage) return "";
  const parts = [];
  if (usage.latency_ms != null) parts.push(usage.latency_ms >= 1000 ? `${(usage.latency_ms / 1000).toFixed(1)} s` : `${usage.latency_ms} ms`);
  if (usage.input_tokens != null) parts.push(`${usage.input_tokens} in / ${usage.output_tokens ?? "?"} out tokens`);
  if (usage.estimated_cost_usd != null) parts.push(`$${Number(usage.estimated_cost_usd).toFixed(6)}`);
  return parts.join(" · ");
}
