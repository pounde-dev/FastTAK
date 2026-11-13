"""Shared test fixtures for FastTAK monitor tests.

Mocking layer convention for fastak_events tests:
- Mock `app.audit.execute` when asserting the SQL string or params (lowest layer
  with observable contract — the actual psycopg call).
- Mock `app.audit.record_event` when testing callers of the audit API
  (engine.record_event, AuditMiddleware) — treats record_event as opaque.
- Mock `app.fastak_db.fetch` when testing read paths (events router,
  engine.get_activity_log).
End-to-end wiring is exercised by `tests-integration/test_audit_persistence.py`,
not duplicated here.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add monitor/ to sys.path so tests can `from app.* import ...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monitor"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_proxy_check: run the monitor's Caddy check instead of bypassing it"
    )


@pytest.fixture(autouse=True)
def _requests_count_as_coming_through_caddy(request, monkeypatch):
    """TestClient has no Caddy in front, and suites send only Remote-* headers.

    Tests of the check itself carry the `real_proxy_check` marker.
    """
    if request.node.get_closest_marker("real_proxy_check"):
        return
    monkeypatch.setattr("app.audit.request_came_through_caddy", lambda _request: True)


@pytest.fixture
def mock_settings(monkeypatch):
    """Patch app.config.settings with test defaults."""
    from app.config import Settings

    test_settings = Settings(
        server_address="test.example.com",
        tak_db_password="testpass",
        tak_db_url="",
        # ── new for #13 ───────────────────────────────
        fastak_db_password="testpass",
        fastak_db_url="",
        app_db_host="app-db-test",
        app_db_user="fastak",
        # ──────────────────────────────────────────────
        lldap_url="http://lldap-test:17170",
        ldap_proxy_url="http://ldap-proxy-test:8080",
        ldap_admin_password="test-password",
        tak_server_url="https://tak-test:8443",
        tak_api_cert_path="/tmp/test.p12",
        tak_api_cert_password="testpass",
        users_hidden_prefixes="adm_,svc_,ma-",
        user_expiry_check_interval=60,
        enrollment_token_ttl_minutes=15,
        tak_enrollment_port=8446,
    )
    monkeypatch.setattr("app.config.settings", test_settings)
    monkeypatch.setattr("app.api.users.router.settings", test_settings)
    return test_settings


@pytest.fixture
def mock_docker_client(monkeypatch):
    """Patch docker.DockerClient with a configurable fake."""
    client = MagicMock()
    monkeypatch.setattr("app.docker_client._client", client)
    monkeypatch.setattr("app.docker_client._cached_services", [])
    monkeypatch.setattr("app.docker_client._cache_time", 0)
    return client


def make_fake_container(
    name, status="running", health="healthy", image_tag="test:latest", labels=None
):
    """Create a fake Docker container object for testing."""
    container = MagicMock()
    container.name = f"fastak-{name}-1"
    container.status = status
    container.labels = labels or {
        "com.docker.compose.service": name,
        "com.docker.compose.project": "fastak",
    }
    container.attrs = {
        "State": {
            "Health": {"Status": health} if health != "none" else {},
        }
    }
    image = MagicMock()
    image.tags = [image_tag] if image_tag else []
    container.image = image
    return container
