"""FastTAK Monitor — health monitoring, alerting, and operations console.

This is the application entrypoint. It wires together:
- API routers (app.api) — JSON endpoints for health, ops, alerts
- Dashboard router (app.dashboard) — HTML UI that consumes the API
- Background scheduler — periodic health checks and alerting
"""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.auth_deps import require_admin
from app.api.backup.router import router as backup_router
from app.api.events.router import router as events_router
from app.api.health.config_drift import init_config_hash
from app.api.health.router import router as health_router
from app.api.ops.router import router as ops_router
from app.api.service_accounts.router import router as service_accounts_router
from app.api.tak.router import router as tak_router
from app.api.users.router import router as users_router
from app.audit import AuditMiddleware, AuthContextMiddleware, init_schema
from app.config import settings
from app.dashboard.routes import router as dashboard_router
from app.dashboard.routes import templates
from app.scheduler import start_scheduler, stop_scheduler

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Without the shared secret the monitor still starts, but every call to the
    # ldap-proxy REST API is sent without the bearer header and comes back 401,
    # so user management fails one operation at a time with no obvious cause.
    # ldap-proxy refuses to start in the same situation; say so here too rather
    # than leaving the asymmetry to be diagnosed from HTTP status codes.
    if not settings.ldap_proxy_secret:
        log.error(
            "LDAP_PROXY_SECRET is empty — ldap-proxy will reject every request "
            "from the monitor (401). Set TOKENS_API_SECRET in .env and recreate "
            "the stack; ./scripts/ensure-secrets.sh .env will generate one."
        )
    if not settings.monitor_proxy_secret:
        log.error(
            "MONITOR_PROXY_SECRET is empty — every signed-in user will get 403, "
            "because the monitor cannot tell a request from Caddy from any other. "
            "./scripts/ensure-secrets.sh .env will generate one; then recreate "
            "the stack."
        )
    init_config_hash()
    try:
        init_schema()
    except Exception:
        log.exception("Could not initialise fastak_events schema")
    try:
        from app.backup.config import ensure_backup_dir
        from app.backup.runner import log_postgres_version_skew

        ensure_backup_dir()
        log_postgres_version_skew()
    except Exception:
        log.exception("Backup startup checks failed")
    start_scheduler()
    yield
    stop_scheduler()


# The built-in schema routes are disabled and re-served below behind the admin
# gate: the OpenAPI document is the full route inventory, and FastAPI mounts
# it ungated by default (issue #82). ReDoc is not re-served — nothing uses it.
app = FastAPI(
    title="FastTAK Monitor",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _add_middleware_in_execution_order(app: FastAPI, *middlewares: type) -> None:
    """Register middlewares so they run in the listed order at request time.

    Starlette runs middleware in reverse-of-registration order: the last
    `add_middleware` call wraps everything and runs first. This helper hides
    that footgun — pass middlewares in the conceptual execution order
    (outer-most first) and registration order is reversed for you.
    """
    for mw in reversed(middlewares):
        app.add_middleware(mw)


# Execution order: AuthContext sets request.state.username/groups/client_ip,
# then Audit reads them to record the row, then the route handler runs.
_add_middleware_in_execution_order(app, AuthContextMiddleware, AuditMiddleware)

# API (JSON)
# Every JSON surface is admin-only: users/service-accounts issue and revoke certs
# (privilege escalation path), ops runs destructive DB operations, and health,
# events, and the TAK proxies are operator telemetry — container inventory, cert
# expiry, config drift, the audit trail, and the connected-client roster with
# last-known positions. Gated at include time so the whole router is protected
# regardless of per-route decorators. backup_router carries its own
# require_group dependency internally. `/api/ping` (below) stays open as the
# unauthenticated liveness probe.
_admin = [Depends(require_admin())]


@app.get("/api/openapi.json", include_in_schema=False, dependencies=_admin)
def openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/api/docs", include_in_schema=False, dependencies=_admin)
def swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="FastTAK Monitor")


app.include_router(health_router, dependencies=_admin)
app.include_router(ops_router, dependencies=_admin)
app.include_router(users_router, dependencies=_admin)
app.include_router(service_accounts_router, dependencies=_admin)
app.include_router(tak_router, dependencies=_admin)
app.include_router(events_router, dependencies=_admin)
app.include_router(backup_router)

# Dashboard (HTML)
app.include_router(dashboard_router)


# Jinja2 filters
def _format_timestamp(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


templates.env.filters["ts"] = _format_timestamp


@app.get("/api/ping")
async def ping():
    return {"status": "ok"}
