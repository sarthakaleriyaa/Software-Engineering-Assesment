// DocuMind UI controller. All server and document text is rendered with
// textContent; nothing is ever inserted as HTML.
import { ApiError, createApi } from "./api.js";
import {
  MAX_QUESTION_LENGTH, answerKind, citationLocation, emailLooksValid, formatBytes,
  formatRelativeTime, passwordProblems, scopeLabel, statusInfo, suggestedQuestions,
  summarizeHealth, usageSummary, validateFile,
} from "./ui-logic.js";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const POLL_BUSY_MS = 2000;
const POLL_RETRY_MS = 5000;
const HEALTH_MS = 30000;

const state = {
  user: null,
  docs: [],
  docsLoaded: false,
  selected: new Set(),
  pollTimer: null,
  healthTimer: null,
  asking: false,
};

const api = createApi({
  fetchImpl: (...args) => window.fetch(...args),
  storage: window.sessionStorage,
  xhrFactory: () => new XMLHttpRequest(),
  onUnauthorized: () => showAuth("Your session has expired. Please sign in again."),
});

// ---------- DOM helpers ----------
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function icon(name, extraClass = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", `icon ${extraClass}`.trim());
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function setBusy(button, busy, busyText) {
  if (busy) {
    button.dataset.label = button.textContent;
    button.replaceChildren(el("span", { class: "spinner", "aria-hidden": "true" }), busyText);
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
  }
}

function toast(message, tone = "neutral") {
  const node = el("div", { class: "toast", dataset: { tone }, role: tone === "bad" ? "alert" : "status" }, message);
  $("toasts").append(node);
  setTimeout(() => node.remove(), 4500);
}

function showMessage(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

// ---------- Views ----------
function showAuth(message) {
  clearTimeout(state.pollTimer);
  clearInterval(state.healthTimer);
  Object.assign(state, { user: null, docs: [], docsLoaded: false, selected: new Set() });
  $("view-app").hidden = true;
  $("view-auth").hidden = false;
  switchAuthTab("signin");
  showMessage("signin-error", message);
  $("signin-email").focus();
}

function showApp(user) {
  state.user = user;
  $("view-auth").hidden = true;
  $("view-app").hidden = false;
  $("user-email").textContent = user.email;
  $("user-avatar").textContent = user.email.charAt(0).toUpperCase();
  resetThread();
  refreshHealth();
  clearInterval(state.healthTimer);
  state.healthTimer = setInterval(refreshHealth, HEALTH_MS);
  loadDocs({ initial: true });
}

// ---------- Auth ----------
function switchAuthTab(which) {
  const signin = which === "signin";
  $("tab-signin").setAttribute("aria-selected", String(signin));
  $("tab-signup").setAttribute("aria-selected", String(!signin));
  $("form-signin").hidden = !signin;
  $("form-signup").hidden = signin;
  for (const id of ["signin-error", "signin-success", "signup-error"]) showMessage(id, "");
}

function fieldError(inputId, message) {
  $(`${inputId}-error`).textContent = message || "";
  $(inputId).setAttribute("aria-invalid", message ? "true" : "false");
  return !message;
}

async function onSignin(event) {
  event.preventDefault();
  showMessage("signin-error", "");
  showMessage("signin-success", "");
  const email = $("signin-email").value.trim();
  const password = $("signin-password").value;
  const ok = [
    fieldError("signin-email", emailLooksValid(email) ? "" : "Enter a valid email address."),
    fieldError("signin-password", password ? "" : "Enter your password."),
  ].every(Boolean);
  if (!ok) return;
  const button = $("signin-submit");
  setBusy(button, true, "Signing in…");
  try {
    const result = await api.login(email, password);
    api.tokens.set(result.access_token);
    $("signin-password").value = "";
    showApp(result.user);
  } catch (err) {
    showMessage("signin-error", err.status === 401 ? "Incorrect email or password." : err.message);
  } finally {
    setBusy(button, false);
  }
}

async function onSignup(event) {
  event.preventDefault();
  showMessage("signup-error", "");
  const email = $("signup-email").value.trim();
  const password = $("signup-password").value;
  const confirm = $("signup-confirm").value;
  const problems = passwordProblems(password);
  const ok = [
    fieldError("signup-email", emailLooksValid(email) ? "" : "Enter a valid email address."),
    fieldError("signup-password", problems.join(" ")),
    fieldError("signup-confirm", password === confirm ? "" : "The passwords do not match."),
  ].every(Boolean);
  if (!ok) return;
  const button = $("signup-submit");
  setBusy(button, true, "Creating account…");
  try {
    await api.signup(email, password);
  } catch (err) {
    setBusy(button, false);
    showMessage("signup-error", err.status === 409 ? "An account with this email already exists. Sign in instead." : err.message);
    return;
  }
  try {
    const result = await api.login(email, password);
    api.tokens.set(result.access_token);
    ["signup-password", "signup-confirm"].forEach((id) => { $(id).value = ""; });
    showApp(result.user);
  } catch {
    switchAuthTab("signin");
    $("signin-email").value = email;
    showMessage("signin-success", "Account created. Please sign in.");
  } finally {
    setBusy(button, false);
  }
}

function onTogglePassword(event) {
  const button = event.currentTarget;
  const input = $(button.dataset.togglePassword);
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  button.setAttribute("aria-pressed", String(show));
  button.setAttribute("aria-label", show ? "Hide password" : "Show password");
  button.replaceChildren(icon(show ? "eye-off" : "eye"));
}

// ---------- Health ----------
async function refreshHealth() {
  const summary = summarizeHealth(await api.health());
  $("health-pill").dataset.state = summary.state;
  $("health-label").textContent = summary.label;
  $("health-pill").title = summary.detail;
  $("health-detail").textContent = `${summary.label}. ${summary.detail}`;
}

// ---------- Knowledge base ----------
function readyDocs() {
  return state.docs.filter((d) => d.status === "ready");
}

async function loadDocs({ initial = false } = {}) {
  clearTimeout(state.pollTimer);
  if (initial) {
    $("docs-loading").hidden = false;
    $("doc-list").hidden = true;
    $("docs-empty").hidden = true;
  }
  try {
    state.docs = await api.listDocuments();
    state.docsLoaded = true;
    showMessage("docs-error", "");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return;
    showMessage("docs-error", `Could not refresh documents: ${err.message}`);
    if (api.tokens.get()) state.pollTimer = setTimeout(loadDocs, POLL_RETRY_MS);
    $("docs-loading").hidden = true;
    return;
  }
  $("docs-loading").hidden = true;
  renderDocs();
  if (state.docs.some((d) => statusInfo(d.status).busy)) {
    state.pollTimer = setTimeout(loadDocs, POLL_BUSY_MS);
  }
}

function renderDocs() {
  const readyIds = new Set(readyDocs().map((d) => d.id));
  for (const id of [...state.selected]) if (!readyIds.has(id)) state.selected.delete(id);

  const list = $("doc-list");
  list.replaceChildren(...state.docs.map(renderDoc));
  list.hidden = state.docs.length === 0;
  $("docs-empty").hidden = !state.docsLoaded || state.docs.length > 0;
  $("kb-sub").textContent = state.docs.length
    ? `${state.docs.length} document${state.docs.length === 1 ? "" : "s"} · ${readyIds.size} ready`
    : "Your documents";
  updateSelectionUi();
}

function renderDoc(doc) {
  const info = statusInfo(doc.status);
  const selected = state.selected.has(doc.id);
  const checkbox = el("input", {
    type: "checkbox", checked: selected, disabled: doc.status !== "ready",
    "aria-label": `Search only in ${doc.filename}`,
    onchange: (e) => { e.target.checked ? state.selected.add(doc.id) : state.selected.delete(doc.id); renderDocs(); },
  });
  const meta = [formatBytes(doc.size_bytes)];
  if (doc.page_count) meta.push(`${doc.page_count} page${doc.page_count === 1 ? "" : "s"}`);
  if (doc.created_at) meta.push(`uploaded ${formatRelativeTime(doc.created_at)}`);
  const badge = el("span", { class: "badge", dataset: { tone: info.tone } },
    info.busy ? el("span", { class: "mini-spin", "aria-hidden": "true" }) : null, info.label);
  return el("li", { class: `doc${selected ? " is-selected" : ""}` },
    el("label", { class: "check" }, checkbox),
    el("div", { class: "doc-main" },
      el("div", { class: "doc-name" }, icon("file"), el("span", { title: doc.filename }, doc.filename)),
      el("div", { class: "doc-meta" }, meta.join(" · ")),
      doc.status === "failed" && doc.failure_reason ? el("div", { class: "doc-error" }, doc.failure_reason) : null),
    el("div", { class: "doc-side" }, badge,
      el("button", { class: "btn btn-ghost btn-icon btn-danger", type: "button", "aria-label": `Delete ${doc.filename}`, title: "Delete", onclick: () => confirmDelete(doc) }, icon("trash"))));
}

function updateSelectionUi() {
  const ready = readyDocs();
  const selectAll = $("select-all");
  selectAll.disabled = ready.length === 0;
  selectAll.checked = ready.length > 0 && state.selected.size === ready.length;
  selectAll.indeterminate = state.selected.size > 0 && state.selected.size < ready.length;
  $("selected-count").textContent = state.selected.size ? `${state.selected.size} selected` : "";
  $("scope-label").textContent = scopeLabel(state.selected.size, ready.length);
  renderSuggestions();
}

function onSelectAll(event) {
  state.selected = event.target.checked ? new Set(readyDocs().map((d) => d.id)) : new Set();
  renderDocs();
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  for (const file of files) {
    const bar = el("div", { class: "progress-bar" });
    const status = el("span", {}, "Waiting…");
    const item = el("li", { class: "upload-item", dataset: { state: "pending" } },
      el("div", { class: "upload-item-row" }, el("span", { class: "upload-item-name", title: file.name }, file.name), status),
      el("div", { class: "progress" }, bar));
    $("upload-list").append(item);
    const check = validateFile(file);
    if (!check.ok) {
      item.dataset.state = "error";
      status.textContent = check.reason;
      bar.parentElement.remove();
      setTimeout(() => item.remove(), 8000);
      continue;
    }
    try {
      status.textContent = "Uploading 0%";
      await api.uploadDocument(file, (pct) => { bar.style.width = `${pct}%`; status.textContent = `Uploading ${pct}%`; });
      item.dataset.state = "done";
      bar.style.width = "100%";
      status.textContent = "Queued for processing";
      setTimeout(() => item.remove(), 4000);
      loadDocs();
    } catch (err) {
      item.dataset.state = "error";
      status.textContent = err.message;
      setTimeout(() => item.remove(), 10000);
    }
  }
}

function confirmDelete(doc) {
  const dialog = $("confirm-dialog");
  $("confirm-text").textContent = `"${doc.filename}" and its indexed passages will be removed. Saved answers keep their text but not its passages.`;
  const ok = $("confirm-ok");
  const cancel = $("confirm-cancel");
  const close = () => { ok.onclick = null; cancel.onclick = null; dialog.close(); };
  cancel.onclick = close;
  ok.onclick = async () => {
    close();
    try {
      await api.deleteDocument(doc.id);
      state.selected.delete(doc.id);
      toast(`Deleted ${doc.filename}`, "ok");
      loadDocs();
    } catch (err) {
      toast(`Could not delete: ${err.message}`, "bad");
    }
  };
  dialog.showModal();
  cancel.focus();
}

// ---------- Q&A console ----------
function renderSuggestions() {
  const box = $("suggestions");
  const ready = readyDocs();
  $("welcome-text").textContent = ready.length
    ? "Ask anything covered by your documents. Answers cite their sources, and say so when the documents don't contain the answer."
    : "Upload a document, wait until it is ready, then ask a question.";
  box.replaceChildren(...suggestedQuestions(ready).map((q) =>
    el("button", { class: "chip", type: "button", onclick: () => { $("question").value = q; ask(); } }, q)));
}

function resetThread() {
  $("thread").replaceChildren($("welcome"));
  $("welcome").hidden = false;
}

function appendToThread(node) {
  $("welcome").hidden = true;
  $("thread").append(node);
  node.scrollIntoView({ block: "end", behavior: "smooth" });
  return node;
}

function userMessage(text) {
  return el("div", { class: "msg msg-user" }, el("div", { class: "bubble" }, text));
}

function typingMessage() {
  return el("div", { class: "msg", "aria-label": "Generating answer" },
    el("div", { class: "msg-avatar" }, icon("logo")),
    el("div", { class: "bubble typing" }, el("span"), el("span"), el("span")));
}

function citationList(citations) {
  if (!citations || !citations.length) return null;
  return el("div", { class: "citations" },
    el("div", { class: "citations-title" }, `Sources (${citations.length})`),
    ...citations.map((c) => el("details", { class: "citation" },
      el("summary", {},
        c.source_id ? el("span", { class: "cite-id" }, c.source_id) : null,
        el("span", { class: "cite-file", title: c.filename }, c.filename),
        el("span", { class: "cite-loc" }, citationLocation(c))),
      el("blockquote", {}, c.passage))));
}

function answerMessage({ kind, text, citations, usage, onRetry }) {
  const labels = {
    answered: [icon("check"), "Answer from your documents"],
    refused: [icon("question"), "Not found in your documents"],
    error: [icon("alert"), "Could not get an answer"],
  };
  return el("div", { class: "msg" },
    el("div", { class: "msg-avatar" }, icon("logo")),
    el("div", { class: "answer", dataset: { kind } },
      el("div", { class: "answer-label" }, ...labels[kind]),
      el("div", { class: "bubble" }, text),
      kind === "answered" ? citationList(citations) : null,
      usageSummary(usage) ? el("div", { class: "usage" }, usageSummary(usage)) : null,
      onRetry ? el("div", { class: "usage" }, el("button", { class: "btn btn-sm", type: "button", onclick: onRetry }, icon("refresh"), "Try again")) : null));
}

async function ask(questionOverride) {
  if (state.asking) return;
  const input = $("question");
  const question = (questionOverride ?? input.value).trim();
  if (!question) { input.focus(); return; }
  if (question.length > MAX_QUESTION_LENGTH) { toast(`Questions are limited to ${MAX_QUESTION_LENGTH} characters.`, "bad"); return; }
  state.asking = true;
  $("ask-submit").disabled = true;
  input.value = "";
  autosize();
  appendToThread(userMessage(question));
  const typing = appendToThread(typingMessage());
  const ids = [...state.selected];
  try {
    const result = await api.ask(question, ids);
    typing.replaceWith(answerMessage({ kind: answerKind(result), text: result.answer, citations: result.citations, usage: result.usage }));
  } catch (err) {
    if (err.status === 401) return;
    typing.replaceWith(answerMessage({ kind: "error", text: err.message, onRetry: (e) => { e.currentTarget.disabled = true; ask(question); } }));
  } finally {
    state.asking = false;
    $("ask-submit").disabled = false;
  }
}

function autosize() {
  const input = $("question");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

// ---------- History ----------
function switchConsoleTab(which) {
  const chat = which === "chat";
  $("tab-chat").setAttribute("aria-selected", String(chat));
  $("tab-history").setAttribute("aria-selected", String(!chat));
  $("pane-chat").hidden = !chat;
  $("pane-history").hidden = chat;
  if (!chat) loadHistory();
}

async function loadHistory() {
  showMessage("history-error", "");
  let items;
  try {
    items = await api.history(30);
  } catch (err) {
    if (err.status !== 401) showMessage("history-error", `Could not load history: ${err.message}`);
    return;
  }
  $("history-empty").hidden = items.length > 0;
  const tones = { answered: "ok", refused: "pending", error: "bad", pending: "neutral" };
  $("history-list").replaceChildren(...items.map((h) => el("li", {},
    el("button", { class: "history-item", type: "button", onclick: () => showHistoryItem(h) },
      el("span", { class: "history-q" }, h.question),
      el("span", { class: "history-meta" },
        el("span", { class: "badge", dataset: { tone: tones[h.status] ?? "neutral" } }, h.status),
        el("span", {}, formatRelativeTime(h.created_at)),
        usageSummary(h.usage) ? el("span", {}, `· ${usageSummary(h.usage)}`) : null)))));
}

function showHistoryItem(h) {
  switchConsoleTab("chat");
  appendToThread(userMessage(h.question));
  const kind = h.status === "answered" ? "answered" : h.status === "refused" ? "refused" : "error";
  const text = h.answer || (h.status === "pending" ? "This question did not finish." : "No answer was recorded for this question.");
  appendToThread(answerMessage({ kind, text, citations: h.citations, usage: h.usage }));
}

// ---------- Wiring ----------
function wire() {
  $("tab-signin").addEventListener("click", () => switchAuthTab("signin"));
  $("tab-signup").addEventListener("click", () => switchAuthTab("signup"));
  $("form-signin").addEventListener("submit", onSignin);
  $("form-signup").addEventListener("submit", onSignup);
  document.querySelectorAll("[data-toggle-password]").forEach((b) => b.addEventListener("click", onTogglePassword));

  $("logout").addEventListener("click", () => { api.tokens.clear(); showAuth(); });
  $("health-pill").addEventListener("click", refreshHealth);
  $("refresh-docs").addEventListener("click", () => loadDocs());
  $("select-all").addEventListener("change", onSelectAll);

  const zone = $("dropzone");
  const input = $("file-input");
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { uploadFiles(input.files); input.value = ""; });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("is-dragging"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("is-dragging"); uploadFiles(e.dataTransfer.files); });

  $("ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
  $("question").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
  $("question").addEventListener("input", autosize);
  $("tab-chat").addEventListener("click", () => switchConsoleTab("chat"));
  $("tab-history").addEventListener("click", () => switchConsoleTab("history"));
}

async function start() {
  wire();
  if (!api.tokens.get()) { showAuth(); return; }
  try {
    showApp(await api.me());
  } catch {
    api.tokens.clear();
    showAuth();
  }
}

start();
