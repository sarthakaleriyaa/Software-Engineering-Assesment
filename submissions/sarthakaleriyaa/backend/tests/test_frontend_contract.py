"""Static contract between the UI (app/static) and the API: every call the
UI makes exists, every field it reads exists, and the page stays safe to
serve under a strict Content-Security-Policy."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.main import app

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
API_JS = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
APP_JS = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
ALL_JS = API_JS + APP_JS + (STATIC / "js" / "ui-logic.js").read_text(encoding="utf-8")

# request("METHOD", "/path") or request("METHOD", `/path/${id}`), plus upload("/path")
_REQUEST = re.compile(r'request\("(\w+)",\s*[`"]([^`"?]+)')
_UPLOAD = re.compile(r'upload\("([^"]+)"')
_HEALTH = re.compile(r'API_BASE \+ "(/health)"')


def ui_calls() -> set[tuple[str, str]]:
    calls = {(m.lower(), re.sub(r"\$\{[^}]+\}", "{param}", p)) for m, p in _REQUEST.findall(API_JS)}
    calls |= {("post", p) for p in _UPLOAD.findall(API_JS) if p.startswith("/")}
    calls |= {("get", p) for p in _HEALTH.findall(API_JS)}
    return calls


def _matches(template: str, path: str) -> bool:
    pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(template))
    return re.fullmatch(pattern, path.replace("{param}", "1")) is not None


def test_ui_makes_the_expected_calls():
    assert ui_calls() == {
        ("post", "/auth/signup"),
        ("post", "/auth/login"),
        ("get", "/auth/me"),
        ("get", "/health"),
        ("get", "/documents"),
        ("post", "/documents"),
        ("delete", "/documents/{param}"),
        ("post", "/chat"),
        ("get", "/chat/history"),
    }


@pytest.mark.parametrize("method,path", sorted(ui_calls()))
def test_every_ui_call_exists_in_openapi(method, path):
    schema = app.openapi()["paths"]
    matching = [t for t in schema if _matches(t, path)]

    assert matching, f"{path} is not an API route"
    assert any(method in schema[t] for t in matching), f"{method.upper()} {path} not allowed"


def _fields(model: str) -> set[str]:
    return set(app.openapi()["components"]["schemas"][model]["properties"])


def test_fields_read_by_ui_exist_in_response_models():
    assert {"access_token", "user"} <= _fields("TokenResponse")
    assert {"id", "email"} <= _fields("UserResponse")
    assert {"id", "filename", "status", "size_bytes", "failure_reason", "page_count", "created_at"} <= _fields(
        "DocumentResponse"
    )
    assert {"answer", "refused", "citations", "usage"} <= _fields("ChatResponse")
    assert {"source_id", "filename", "page_number", "chunk_index", "passage"} <= _fields("Citation")
    assert {"latency_ms", "input_tokens", "output_tokens", "estimated_cost_usd"} <= _fields("Usage")
    assert {"question", "answer", "status", "created_at", "citations", "usage"} <= _fields(
        "QuestionHistoryItem"
    )


def test_ui_never_injects_html_or_evaluates_code():
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in ALL_JS, forbidden


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements: list[tuple[str, dict]] = []
        self.inline_script = False

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        if self.elements and self.elements[-1][0] == "script" and data.strip():
            self.inline_script = True


def _page() -> _Collector:
    collector = _Collector()
    collector.feed(HTML)
    return collector


def test_page_has_no_inline_code_so_the_strict_csp_holds():
    page = _page()
    assert not page.inline_script
    assert all("src" in attrs for tag, attrs in page.elements if tag == "script")
    assert not any(tag == "style" for tag, _ in page.elements)
    assert not any("style" in attrs for _, attrs in page.elements)
    assert not any(k.startswith("on") for _, attrs in page.elements for k in attrs)


def test_form_controls_and_icon_buttons_have_accessible_names():
    page = _page()
    label_targets = {attrs.get("for") for tag, attrs in page.elements if tag == "label"}
    for tag, attrs in page.elements:
        if tag in ("input", "textarea") and attrs.get("type") != "checkbox":
            assert attrs.get("id") in label_targets or "aria-label" in attrs, attrs
        if tag == "button" and "btn-icon" in attrs.get("class", ""):
            assert "aria-label" in attrs, attrs
    # Document checkboxes are created in JS and named after the file.
    assert '"aria-label": `Search only in ${doc.filename}`' in APP_JS


def test_ui_is_served_at_root_with_strict_csp(client):
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>DocuMind</title>" in response.text
    assert "script-src 'self';" in csp
    assert "style-src 'self';" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.parametrize(
    "asset,content_type",
    [("/static/js/app.js", "javascript"), ("/static/js/api.js", "javascript"), ("/static/css/app.css", "text/css")],
)
def test_ui_assets_are_served_and_revalidated(client, asset, content_type):
    response = client.get(asset)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert response.headers["Cache-Control"] == "no-cache"


def test_static_mount_does_not_expose_other_files(client):
    assert client.get("/static/../main.py").status_code == 404
    assert client.get("/static/%2e%2e/main.py").status_code == 404


def test_security_headers_on_api_responses(client):
    response = client.get("/health/live")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    # Swagger UI loads from a CDN, so it must not get the UI's CSP.
    assert "Content-Security-Policy" not in client.get("/docs").headers
