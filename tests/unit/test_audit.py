"""Tests for the audit / event-store module."""

from unittest.mock import patch

import pytest


def test_init_schema_runs_create_table_idempotently():
    from app.audit import init_schema

    with patch("app.audit.execute") as mock_exec:
        init_schema()
    sqls = [c.args[0] for c in mock_exec.call_args_list]
    create_calls = [s for s in sqls if "CREATE TABLE IF NOT EXISTS fastak_events" in s]
    assert len(create_calls) == 1
    # Must include agency_id (forward-compat with #21)
    assert "agency_id" in create_calls[0]
    # Must include the four required indexes
    index_calls = [s for s in sqls if "CREATE INDEX IF NOT EXISTS" in s]
    assert len(index_calls) >= 4


def test_record_event_inserts_row():
    from app.audit import record_event

    with patch("app.audit.execute") as mock_exec:
        record_event(
            source="audit",
            actor="alice",
            action="user.create",
            target_type="user",
            target_id="42",
            detail={"name": "Alice", "groups": ["tak_Blue"]},
            ip="10.0.0.5",
        )
    assert mock_exec.called
    sql, params = mock_exec.call_args.args
    assert "INSERT INTO fastak_events" in sql
    # Column-order matches signature: (source, actor, action, target_type,
    # target_id, detail, ip, agency_id) — guards against transposition.
    assert len(params) == 8
    assert params[0] == "audit"
    assert params[1] == "alice"
    assert params[2] == "user.create"
    assert params[3] == "user"
    assert params[4] == "42"
    # detail is JSON-encoded for the JSONB column
    import json

    assert json.loads(params[5]) == {"name": "Alice", "groups": ["tak_Blue"]}
    assert params[6] == "10.0.0.5"
    assert params[7] is None


def test_record_event_swallows_db_errors():
    from app.audit import record_event

    with patch("app.audit.execute", side_effect=ValueError("FASTAK_DB_PASSWORD must be set")):
        # Audit writes are best-effort: a config error here must NOT break the
        # caller. Configuration problems surface via init_schema() at startup.
        record_event("audit", "alice", "user.create")


def test_auth_context_middleware_extracts_user_and_groups():
    from app.audit import AuthContextMiddleware
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthContextMiddleware)

    @app.get("/who")
    def who(request: Request):
        return {
            "user": getattr(request.state, "username", None),
            "groups": list(getattr(request.state, "groups", [])),
        }

    client = TestClient(app)
    r = client.get(
        "/who",
        headers={
            "Remote-User": "alice",
            "Remote-Groups": "tak_Blue,agency_red",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == "alice"
    assert "tak_Blue" in body["groups"]
    assert "agency_red" in body["groups"]


def test_auth_context_defaults_to_unknown_when_headers_missing():
    from app.audit import AuthContextMiddleware
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthContextMiddleware)

    @app.get("/who")
    def who(request: Request):
        return {"user": getattr(request.state, "username", None)}

    client = TestClient(app)
    r = client.get("/who")
    assert r.json()["user"] == "unknown"


def test_audit_middleware_records_mutating_requests(monkeypatch):
    from app.audit import AuditMiddleware, AuthContextMiddleware
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    recorded = []
    monkeypatch.setattr("app.audit.record_event", lambda **kw: recorded.append(kw))

    app = FastAPI()
    app.add_middleware(AuditMiddleware)  # registered first → runs second
    app.add_middleware(AuthContextMiddleware)  # registered second → runs first

    @app.post("/api/users")
    def create_user(request: Request):
        return {"id": 42, "username": "alice"}

    client = TestClient(app)
    r = client.post(
        "/api/users",
        json={"username": "alice", "password": "secret"},
        headers={"Remote-User": "operator"},
    )
    assert r.status_code == 200
    assert len(recorded) == 1
    ev = recorded[0]
    assert ev["actor"] == "operator"
    assert ev["action"] == "POST /api/users"
    assert ev["source"] == "audit"
    body_in_detail = ev["detail"].get("request_body", {})
    assert body_in_detail.get("username") == "alice"
    assert body_in_detail.get("password") in ("[redacted]", None)


def test_audit_middleware_skips_reads():
    from unittest.mock import patch as _patch

    from app.audit import AuditMiddleware
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    recorded = []
    with _patch("app.audit.record_event", lambda **kw: recorded.append(kw)):
        app = FastAPI()
        app.add_middleware(AuditMiddleware)

        @app.get("/api/users")
        def list_users():
            return []

        client = TestClient(app)
        client.get("/api/users")
    assert recorded == []


def test_audit_middleware_skips_failed_requests():
    from unittest.mock import patch as _patch

    from app.audit import AuditMiddleware
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    recorded = []
    with _patch("app.audit.record_event", lambda **kw: recorded.append(kw)):
        app = FastAPI()
        app.add_middleware(AuditMiddleware)

        @app.post("/api/users")
        def create_user():
            raise HTTPException(400, "bad")

        client = TestClient(app)
        client.post("/api/users", json={})
    assert recorded == []


def test_sanitise_redacts_substring_field_names():
    """Substring match catches `smtp_password`, `auth_token`, etc."""
    from app.audit import _sanitise

    out = _sanitise(
        {
            "username": "alice",
            "password": "p1",
            "smtp_password": "p2",
            "auth_token": "t1",
            "api_key": "k1",
            "p12_password": "p3",
            "private_key_path": "/etc/k.pem",
            "config": {"bind_secret": "s1", "label": "ok"},
            "items": [{"passcode": "x"}, {"name": "y"}],
        }
    )
    assert out["username"] == "alice"
    assert out["password"] == "[redacted]"
    assert out["smtp_password"] == "[redacted]"
    assert out["auth_token"] == "[redacted]"
    assert out["api_key"] == "[redacted]"
    assert out["p12_password"] == "[redacted]"
    assert out["private_key_path"] == "[redacted]"
    assert out["config"]["bind_secret"] == "[redacted]"
    assert out["config"]["label"] == "ok"
    assert out["items"][0]["passcode"] == "[redacted]"
    assert out["items"][1]["name"] == "y"


def test_audit_middleware_does_not_break_request_when_db_fails():
    """Negative path: a failing audit DB write must not propagate to the user.

    Exercises the full middleware chain with a real AuditMiddleware on top of
    a real route handler, with `execute` configured to raise. The mutation
    should still succeed (200) and the response body should be intact.
    """
    from app.audit import AuditMiddleware
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuditMiddleware)

    @app.post("/api/users")
    def create_user():
        return {"id": 42}

    with patch("app.audit.execute", side_effect=RuntimeError("app-db unavailable")):
        client = TestClient(app)
        r = client.post("/api/users", json={"name": "alice"})

    assert r.status_code == 200
    assert r.json() == {"id": 42}


def _identity_app(monkeypatch, secret):
    from app.audit import AuthContextMiddleware
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    monkeypatch.setattr("app.config.settings.monitor_proxy_secret", secret)
    app = FastAPI()
    app.add_middleware(AuthContextMiddleware)

    @app.get("/who")
    def who(request: Request):
        return {"user": request.state.username, "groups": request.state.groups}

    return TestClient(app)


FORGED = {"Remote-User": "alice", "Remote-Groups": "monitor_admin"}
NO_IDENTITY = {"user": "unknown", "groups": []}


@pytest.mark.real_proxy_check
def test_identity_headers_count_with_the_proxy_secret(monkeypatch):
    client = _identity_app(monkeypatch, "s3cret")
    r = client.get("/who", headers={**FORGED, "X-FastTAK-Proxy-Secret": "s3cret"})
    assert r.json() == {"user": "alice", "groups": ["monitor_admin"]}


@pytest.mark.real_proxy_check
def test_identity_headers_without_the_secret_are_ignored(monkeypatch):
    client = _identity_app(monkeypatch, "s3cret")
    assert client.get("/who", headers=FORGED).json() == NO_IDENTITY


@pytest.mark.real_proxy_check
def test_identity_headers_with_a_wrong_secret_are_ignored(monkeypatch):
    client = _identity_app(monkeypatch, "s3cret")
    r = client.get("/who", headers={**FORGED, "X-FastTAK-Proxy-Secret": "nope"})
    assert r.json() == NO_IDENTITY


@pytest.mark.real_proxy_check
def test_a_non_ascii_secret_is_refused_rather_than_raising(monkeypatch):
    client = _identity_app(monkeypatch, "s3cret")
    r = client.get("/who", headers={**FORGED, "X-FastTAK-Proxy-Secret": b"caf\xc3\xa9"})
    assert r.status_code == 200
    assert r.json() == NO_IDENTITY


@pytest.mark.real_proxy_check
def test_an_unconfigured_secret_trusts_nobody(monkeypatch):
    client = _identity_app(monkeypatch, "")
    assert (
        client.get("/who", headers={**FORGED, "X-FastTAK-Proxy-Secret": ""}).json() == NO_IDENTITY
    )


def _app_with_middleware(monkeypatch):
    from app.audit import AuditMiddleware, AuthContextMiddleware
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from pydantic import BaseModel

    recorded = []
    monkeypatch.setattr("app.audit.record_event", lambda **kw: recorded.append(kw))

    class Body(BaseModel):
        name: str

    app = FastAPI()
    app.add_middleware(AuditMiddleware)
    app.add_middleware(AuthContextMiddleware)

    @app.post("/echo")
    def echo(body: Body):
        return {"name": body.name}

    @app.post("/vacuum")
    def vacuum():
        return {"ran": True}

    @app.get("/ip")
    def ip(request: Request):
        return {"ip": request.state.client_ip}

    return TestClient(app), recorded


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_change_requested_by_another_site_is_refused(monkeypatch, site):
    """The browser adds the admin's Basic credentials to any request for this
    origin, and a route with no body needs nothing else."""
    client, recorded = _app_with_middleware(monkeypatch)
    r = client.post("/vacuum", headers={"Sec-Fetch-Site": site})
    assert r.status_code == 403
    assert recorded == []


@pytest.mark.parametrize(
    "headers", [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}]
)
def test_the_dashboard_and_command_line_clients_still_work(monkeypatch, headers):
    client, _ = _app_with_middleware(monkeypatch)
    assert client.post("/vacuum", headers=headers).status_code == 200


def test_reads_from_another_site_are_not_refused(monkeypatch):
    """The browser's same-origin policy already keeps the response from that site."""
    client, _ = _app_with_middleware(monkeypatch)
    assert client.get("/ip", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_the_handler_still_receives_the_body(monkeypatch):
    client, recorded = _app_with_middleware(monkeypatch)
    r = client.post("/echo", json={"name": "alice"})
    assert r.json() == {"name": "alice"}
    assert recorded[0]["detail"]["request_body"] == {"name": "alice"}


def test_an_oversized_body_is_refused_before_it_is_read(monkeypatch):
    from app import audit

    monkeypatch.setattr(audit, "MAX_BODY_BYTES", 64)
    client, recorded = _app_with_middleware(monkeypatch)
    r = client.post("/echo", json={"name": "x" * 100})
    assert r.status_code == 413
    assert recorded == []


def test_an_oversized_body_without_a_declared_length_is_refused(monkeypatch):
    from app import audit

    monkeypatch.setattr(audit, "MAX_BODY_BYTES", 64)
    client, _ = _app_with_middleware(monkeypatch)

    def chunks():
        for _ in range(10):
            yield b"x" * 32

    assert client.post("/echo", content=chunks()).status_code == 413


def test_the_recorded_address_is_the_one_caddy_saw(monkeypatch):
    """Through Caddy the peer is Caddy's container. Caddy writes the last entry."""
    client, _ = _app_with_middleware(monkeypatch)
    r = client.get("/ip", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert r.json() == {"ip": "203.0.113.9"}


@pytest.mark.real_proxy_check
def test_a_forwarded_address_without_the_proxy_secret_is_ignored(monkeypatch):
    client, _ = _app_with_middleware(monkeypatch)
    r = client.get("/ip", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.json() == {"ip": "testclient"}
