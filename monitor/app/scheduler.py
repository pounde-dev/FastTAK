"""Background scheduler for periodic health checks and alerting.

Uses BackgroundScheduler (thread-based), not AsyncIOScheduler, because the
health check functions call blocking Docker SDK / subprocess operations.
AsyncIOScheduler would run these on the event loop and stall all async
endpoints. BackgroundScheduler runs each job in its own thread.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app import store
from app.api.alerts.engine import check_and_alert
from app.api.health.autovacuum import get_autovacuum_health
from app.api.health.certs import get_cert_status
from app.api.health.config_drift import check_config_drift
from app.api.health.containers import get_all_container_health
from app.api.health.database import get_cot_db_size
from app.api.health.disk import get_disk_usage
from app.api.health.tls import get_tls_status
from app.api.health.updates import check_updates
from app.api.users.identity import IdentityClient
from app.api.users.offboarding import is_system_account
from app.api.users.tak_server import TakServerClient
from app.config import settings
from app.evaluator import evaluate
from app.monitoring_config import load_config

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

_HEALTH_FUNCTIONS = {
    "database": get_cot_db_size,
    "autovacuum": get_autovacuum_health,
    "disk": get_disk_usage,
    "certs": get_cert_status,
    "tls": get_tls_status,
    "containers": get_all_container_health,
    "config": check_config_drift,
    "updates": check_updates,
}

# ── Lazy singletons for scheduler-owned clients ───────────────────────────────
# Created once on first use and reused across ticks to avoid the overhead of
# re-initialising SSL contexts / HTTP connections every check interval.

_scheduler_ak: IdentityClient | None = None
_scheduler_tak: TakServerClient | None = None


def _get_scheduler_ak() -> IdentityClient | None:
    global _scheduler_ak
    if _scheduler_ak is None and settings.ldap_admin_password:
        _scheduler_ak = IdentityClient(
            lldap_url=settings.lldap_url,
            proxy_url=settings.ldap_proxy_url,
            admin_password=settings.ldap_admin_password,
            hidden_prefixes=settings.users_hidden_prefixes.split(","),
            proxy_secret=settings.ldap_proxy_secret,
        )
    return _scheduler_ak


def _get_scheduler_tak() -> TakServerClient | None:
    global _scheduler_tak
    if _scheduler_tak is None and settings.tak_api_cert_path:
        _scheduler_tak = TakServerClient(
            base_url=settings.tak_server_url,
            cert_path=settings.tak_api_cert_path,
            cert_password=settings.tak_api_cert_password,
        )
    return _scheduler_tak


def _poll(service_name, health_fn, service_config):
    """Generic health poll: run health_fn, evaluate against thresholds, alert."""
    try:
        raw = health_fn()
    except Exception as e:
        raw = {"error": str(e)[:200]}

    if "error" in raw:
        error_eval = {"status": "critical", "message": raw["error"], "should_alert": True}
        store.update(service_name, raw, error_eval, None)
        check_and_alert(service_name, "critical", raw["error"], should_alert=True)
        return

    evaluated = evaluate(service_name, raw, service_config)
    store.update(service_name, raw, evaluated, service_config.get("thresholds"))

    # Every poll reaches the engine so it can track recoveries and repeats;
    # the evaluator's verdict says whether this state warrants a notification (#77).
    check_and_alert(
        service_name,
        evaluated["status"],
        evaluated.get("message", ""),
        should_alert=evaluated.get("should_alert", False),
    )


def _check_user_expiry(
    ak: IdentityClient | None = None,
    tak: TakServerClient | None = None,
):
    """Check for expired TTL users and deactivate + revoke certs, then cut any
    access an inactive account still holds.

    When called by the scheduler (no args), module-level singletons are reused
    across ticks. Explicit arguments are accepted for test injection.
    """
    if ak is None:
        ak = _get_scheduler_ak()
        if ak is None:
            return
    if tak is None:
        tak = _get_scheduler_tak()

    try:
        pending = ak.get_users_pending_expiry()
    except Exception:
        log.warning("TTL check: failed to query LLDAP for expired users")
        return

    for user in pending:
        user_id = user["id"]
        username = user["username"]
        is_active = user.get("is_active", True)

        if is_system_account(username):
            log.warning(
                "TTL expired on %s, which the stack depends on; leaving it alone", username
            )
            continue

        try:
            if is_active:
                ak.deactivate_user(user_id)
                log.info("TTL expired: deactivated user %s (id=%d)", username, user_id)

            if tak is None:
                # No TAK client configured: certs cannot be revoked. Do NOT mark
                # the user revoked, or get_users_pending_expiry() would filter
                # them out forever, leaving a valid cert on TAK Server. Leave the
                # flag unset so a later tick retries once TAK is available (#55).
                log.warning(
                    "TTL expired: no TAK client available, deferring cert "
                    "revocation for %s (id=%d)",
                    username,
                    user_id,
                )
            elif tak.revoke_all_user_certs(username):
                ak.mark_certs_revoked(user_id)
                log.info("TTL expired: revoked certs for %s (id=%d)", username, user_id)
            else:
                log.warning("TTL expired: cert revocation incomplete for %s, will retry", username)

        except Exception:
            log.exception("TTL check: error processing user %s (id=%d)", username, user_id)
            continue

    _cut_leftover_access(ak)


def _cut_leftover_access(ak: IdentityClient) -> None:
    """Finish deactivating inactive accounts that still belong to a group.

    Before DD-061, deactivating only set a flag, so accounts deactivated then
    kept their password and groups. This applies the cut to them without an
    operator having to find each one. It costs one user listing per tick and
    does nothing once every inactive account has no groups.
    """
    try:
        leftover = ak.get_inactive_users_with_groups()
    except Exception:
        log.warning("Could not list inactive users to check for leftover access")
        return

    for user in leftover:
        username = user["username"]
        if is_system_account(username):
            continue
        try:
            ak.deactivate_user(user["id"])
            log.info("Cut leftover access for inactive user %s", username)
        except Exception:
            log.exception("Could not cut leftover access for inactive user %s", username)


def start_scheduler():
    config = load_config()

    # Set global alert cooldown from config
    from app.api.alerts import engine

    engine.alert_cooldown = config.get("alert_cooldown", 300)

    for service_name, health_fn in _HEALTH_FUNCTIONS.items():
        service_config = config.get(service_name, {})
        interval = service_config.get("interval", 60)
        scheduler.add_job(
            _poll,
            "interval",
            seconds=interval,
            id=service_name,
            args=[service_name, health_fn, service_config],
        )

    # TTL enforcement (only if identity provider is configured)
    if settings.ldap_admin_password:
        scheduler.add_job(
            _check_user_expiry,
            "interval",
            seconds=settings.user_expiry_check_interval,
            id="user_expiry",
        )

    scheduler.start()

    # Populate cache immediately — best-effort, one failure doesn't block the rest
    for service_name, health_fn in _HEALTH_FUNCTIONS.items():
        service_config = config.get(service_name, {})
        try:
            _poll(service_name, health_fn, service_config)
        except Exception:
            log.warning("Initial health poll failed for %s", service_name, exc_info=True)


def stop_scheduler():
    scheduler.shutdown(wait=False)
