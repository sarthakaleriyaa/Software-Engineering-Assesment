
from datetime import datetime, timezone

from app.models.worker_heartbeat import WorkerHeartbeat


def test_liveness_endpoint_returns_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_each_dependency(client, db_session):
    db_session.add(
        WorkerHeartbeat(worker_id="w1", last_seen=datetime.now(timezone.utc))
    )
    db_session.commit()

    response = client.get("/health")
    body = response.json()

    assert set(body["checks"]) == {"database", "vector_store", "worker", "queue"}
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["worker"]["status"] == "ok"
    assert body["checks"]["queue"] == {"status": "ok", "queued": 0}
    # SQLite has no pgvector, so the overall status must be degraded (503).
    assert body["checks"]["vector_store"]["status"] == "error"
    assert response.status_code == 503


def test_health_without_worker_is_degraded(client):
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"]["worker"]["status"] == "error"


def test_responses_carry_request_id(client):
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"

    generated = client.get("/health/live", headers={"X-Request-ID": "bad id!"})
    assert generated.headers["X-Request-ID"] != "bad id!"
    assert len(generated.headers["X-Request-ID"]) == 32


def test_embedded_worker_starts_and_stops_with_the_api(monkeypatch):
    from fastapi.testclient import TestClient

    from app import worker
    from app.main import app

    calls = []
    monkeypatch.setenv("RUN_EMBEDDED_WORKER", "true")
    monkeypatch.setattr(worker, "start_embedded", lambda: calls.append("start"))
    monkeypatch.setattr(worker, "stop", lambda: calls.append("stop"))

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert calls == ["start"]
    assert calls == ["start", "stop"]


def test_embedded_worker_is_off_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    from app import worker
    from app.main import app

    calls = []
    monkeypatch.delenv("RUN_EMBEDDED_WORKER", raising=False)
    monkeypatch.setattr(worker, "start_embedded", lambda: calls.append("start"))

    with TestClient(app):
        pass
    assert calls == []
