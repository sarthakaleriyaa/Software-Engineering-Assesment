// The only module that talks to the backend. Same origin, so no base URL or
// CORS is needed; API_BASE exists for a split frontend deployment.

export const API_BASE = "";
const TOKEN_KEY = "documind_token";

export class ApiError extends Error {
  constructor(message, status = 0, retryAfter = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function detailMessage(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => (d && d.msg ? d.msg : String(d))).join("; ");
  }
  return `Request failed (${status}).`;
}

function safeStorage(storage) {
  return {
    get() { try { return storage.getItem(TOKEN_KEY); } catch { return null; } },
    set(value) { try { storage.setItem(TOKEN_KEY, value); } catch { /* storage unavailable */ } },
    clear() { try { storage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
  };
}

export function createApi({ fetchImpl, storage, xhrFactory, onUnauthorized = () => {} }) {
  const tokens = safeStorage(storage);

  function authHeaders() {
    const token = tokens.get();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function parse(response) {
    if (response.status === 204) return null;
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (response.ok) return body;
    if (response.status === 401 && tokens.get()) {
      tokens.clear();
      onUnauthorized();
      throw new ApiError("Your session has expired. Please sign in again.", 401);
    }
    const retry = response.headers && response.headers.get ? response.headers.get("Retry-After") : null;
    throw new ApiError(detailMessage(body, response.status), response.status, retry ? Number(retry) : null);
  }

  async function request(method, path, { json } = {}) {
    const headers = { ...authHeaders() };
    const init = { method, headers };
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    }
    let response;
    try {
      response = await fetchImpl(API_BASE + path, init);
    } catch {
      throw new ApiError("Cannot reach the DocuMind server. Check your connection and try again.", 0);
    }
    return parse(response);
  }

  // XMLHttpRequest because fetch cannot report upload progress.
  function upload(path, file, onProgress = () => {}) {
    return new Promise((resolve, reject) => {
      const xhr = xhrFactory();
      xhr.open("POST", API_BASE + path);
      const token = tokens.get();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      };
      xhr.onerror = () => reject(new ApiError("Upload failed: the server could not be reached.", 0));
      xhr.onload = () => {
        const headers = { get: (name) => xhr.getResponseHeader(name) };
        parse({
          status: xhr.status,
          ok: xhr.status >= 200 && xhr.status < 300,
          headers,
          json: async () => JSON.parse(xhr.responseText),
        }).then(resolve, reject);
      };
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }

  return {
    tokens,
    request,
    upload,
    signup: (email, password) => request("POST", "/auth/signup", { json: { email, password } }),
    login: (email, password) => request("POST", "/auth/login", { json: { email, password } }),
    me: () => request("GET", "/auth/me"),
    health: async () => {
      // /health answers 503 with a JSON body when degraded; keep that body.
      try {
        const response = await fetchImpl(API_BASE + "/health", { method: "GET", headers: {} });
        let body = null;
        try { body = await response.json(); } catch { body = null; }
        return { status: response.status, body };
      } catch {
        return { unreachable: true };
      }
    },
    listDocuments: () => request("GET", "/documents"),
    uploadDocument: (file, onProgress) => upload("/documents", file, onProgress),
    deleteDocument: (id) => request("DELETE", `/documents/${id}`),
    ask: (question, documentIds) =>
      request("POST", "/chat", { json: documentIds && documentIds.length ? { question, document_ids: documentIds } : { question } }),
    history: (limit = 20) => request("GET", `/chat/history?limit=${limit}`),
  };
}
