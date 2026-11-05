"""Audit event store: schema bootstrap, recording, and middleware.

Schema (fastak_events):
- id            BIGSERIAL PRIMARY KEY
- timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW()
- source        TEXT NOT NULL  -- 'audit' or 'health'
- actor         TEXT NOT NULL  -- username, 'system', or 'unknown'
- action        TEXT NOT NULL  -- 'POST /api/users', 'unhealthy', etc.
- target_type   TEXT           -- 'user', 'group', 'cert', 'service'
- target_id     TEXT           -- string id of the affected resource
- detail        JSONB          -- structured context (sanitised request body)
- ip            INET           -- source IP for audit events
- agency_id     UUID           -- nullable; populated once #21 lands
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware

from app import config
from app.fastak_db import execute

log = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fastak_events (
    id            BIGSERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source        TEXT NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    target_type   TEXT,
    target_id     TEXT,
    detail        JSONB,
    ip            INET,
    agency_id     UUID
)
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_timestamp ON fastak_events (timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_source_ts "
    "ON fastak_events (source, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_actor_ts "
    "ON fastak_events (actor, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_agency_ts "
    "ON fastak_events (agency_id, timestamp DESC) WHERE agency_id IS NOT NULL",
]


def init_schema() -> None:
    """Idempotent — safe to run on every startup."""
    execute(SCHEMA_SQL)
    for sql in INDEX_SQL:
        execute(sql)


def record_event(
    source: str,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
    agency_id: str | None = None,  # UUID as string; psycopg casts to UUID column
) -> None:
    """Insert one row into fastak_events. Swallows DB errors.

    Audit writes are best-effort; a failure here must not block the
    user-facing request that triggered them.
    """
    sql = """
        INSERT INTO fastak_events
            (source, actor, action, target_type, target_id, detail, ip, agency_id)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
    """
    detail_json = json.dumps(detail) if detail is not None else None
    try:
        execute(
            sql,
            (source, actor, action, target_type, target_id, detail_json, ip, agency_id),
        )
    except Exception:
        log.exception("Failed to record audit event %s/%s/%s", source, actor, action)


PROXY_SECRET_HEADER = "X-FastTAK-Proxy-Secret"


def request_came_through_caddy(request) -> bool:
    """True only when the request carries the secret Caddy adds on the monitor route.

    Reaching this port proves nothing: every container on the Docker network
    can, so the identity headers mean nothing without this (DD-050, DD-059).
    An empty configured secret never matches.
    """
    expected = config.settings.monitor_proxy_secret
    sent = request.headers.get(PROXY_SECRET_HEADER, "")
    return bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())


class AuthContextMiddleware(BaseHTTPMiddleware):
    """Pull Remote-User / Remote-Groups headers into request.state.

    Caddy's forward_auth flow sets these headers (ldap-proxy generates them).
    A request that did not come through Caddy has no identity: it falls through
    to 'unknown' with no groups, which every admin gate refuses.
    """

    async def dispatch(self, request, call_next):
        request.state.username = "unknown"
        request.state.groups = []
        if request_came_through_caddy(request):
            request.state.username = request.headers.get("Remote-User") or "unknown"
            groups_header = request.headers.get("Remote-Groups", "")
            request.state.groups = [g for g in (s.strip() for s in groups_header.split(",")) if g]
        elif "Remote-Groups" in request.headers or "Remote-User" in request.headers:
            log.warning(
                "Ignored identity headers from %s: no valid proxy secret",
                request.client.host if request.client else "unknown",
            )
        request.state.client_ip = request.client.host if request.client else None
        return await call_next(request)


# Substrings of body-field names whose values must be redacted before audit
# logging. Substring match is intentional: `smtp_password`, `auth_token`,
# `bind_secret`, `p12_password`, `private_key_path` all need to be redacted.
# Over-redaction (e.g. `password_strength_meter` → [redacted]) is acceptable
# in an audit log; under-redaction is not.
REDACT_FIELDS = {"password", "passcode", "token", "secret", "api_key", "p12", "private_key"}

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _sanitise(payload):
    if isinstance(payload, dict):
        return {
            k: ("[redacted]" if any(s in k.lower() for s in REDACT_FIELDS) else _sanitise(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_sanitise(x) for x in payload]
    return payload


class AuditMiddleware(BaseHTTPMiddleware):
    """Record one fastak_events row per successful mutating request.

    Skips reads (GET/HEAD/OPTIONS) and failed requests (status >= 400).
    Reads body bytes once, sanitises, attaches to detail.
    """

    async def dispatch(self, request, call_next):
        method = request.method
        if method not in MUTATING_METHODS:
            return await call_next(request)

        # Read body bytes once. await request.body() consumes the ASGI
        # stream; replaying via request._receive lets the downstream
        # handler still parse the body. (Known BaseHTTPMiddleware caveat
        # — fine for small JSON bodies, shaky for streaming uploads.)
        body_bytes = await request.body()

        async def _replay():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request._receive = _replay  # type: ignore[attr-defined]

        response = await call_next(request)
        if response.status_code >= 400:
            return response

        request_body = None
        if body_bytes:
            try:
                request_body = _sanitise(json.loads(body_bytes))
            except Exception:
                request_body = "<non-json or binary>"

        actor = getattr(request.state, "username", "unknown")
        ip = getattr(request.state, "client_ip", None)
        detail = {
            "method": method,
            "path": request.url.path,
            "status": response.status_code,
        }
        if request_body is not None:
            detail["request_body"] = request_body

        # TODO(#21): populate agency_id from request.state.agency_id once
        # AgencyContextMiddleware lands. DD-040 commits to "NULL = visible
        # to non-superadmins" so legacy rows stay queryable.
        record_event(
            source="audit",
            actor=actor,
            action=f"{method} {request.url.path}",
            target_type=None,  # populated by per-route enrichment later
            target_id=None,
            detail=detail,
            ip=ip,
        )
        return response
