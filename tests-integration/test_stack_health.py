"""Stack-level health assertions ported from start.sh --test.

These ran only under `./start.sh --test`, which CI never invoked and which
tore down the deployment on any failure. They belong here, against the
isolated test stack.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CERT_FILES = [
    "root-ca.pem",
    "ca.pem",
    "takserver.jks",
    "svc_fasttakapi.p12",
    "ca-signing.jks",
]


def _ps(compose_cmd, compose_env) -> dict[str, dict]:
    out = subprocess.run(
        [*compose_cmd, "ps", "-a", "--format", "json"],
        capture_output=True,
        text=True,
        env=compose_env,
        check=True,
    ).stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {row["Service"]: row for row in rows}


@pytest.mark.parametrize("service", ["init-config", "init-identity"])
def test_bootstrap_container_exited_zero(compose_cmd, compose_env, service):
    """A failed one-shot never shows as unhealthy anywhere. init-identity
    exiting non-zero means nobody is in monitor_admin — the lockout case."""
    row = _ps(compose_cmd, compose_env)[service]
    assert row["State"] == "exited", row
    assert row["ExitCode"] == 0, row


@pytest.mark.parametrize("name", CERT_FILES)
def test_cert_file_present(stack_info, name):
    path = Path(stack_info.tak_host_path) / "certs" / "files" / name
    assert path.is_file(), f"{path} missing — enrollment will fail later, not now"


def _log_count(compose_exec, pattern: str) -> int:
    result = compose_exec(
        "tak-server", ["sh", "-c", f"grep -c '{pattern}' /opt/tak/logs/takserver.log || true"]
    )
    return int((result.stdout or "0").strip() or 0)


def test_no_out_of_memory_errors(compose_exec):
    assert _log_count(compose_exec, "OutOfMemoryError") == 0


def test_db_auth_failures_are_bounded(compose_exec):
    """A couple can happen while the database comes up; more means the
    password in CoreConfig.xml disagrees with the one the database was
    initialised with."""
    assert _log_count(compose_exec, "password authentication failed") <= 2


def test_tak_server_started_once(compose_exec):
    """'Security status' logs once per start. Repeats mean a restart loop —
    invisible to docker compose ps, which reports healthy after each one."""
    assert _log_count(compose_exec, "Security status") <= 4


def test_healthcheck_probe_answers_without_a_server_error(compose_exec):
    """healthcheck.sh goes unhealthy on a 5xx from this URL; prove a healthy
    server answers it with anything else, so the check cannot false-trip."""
    result = compose_exec(
        "tak-server",
        [
            "curl",
            "-sk",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            "5",
            "https://localhost:8446/Marti/api/version",
        ],
    )
    code = (result.stdout or "").strip()
    assert code and code != "000" and not code.startswith("5"), f"probe answered {code!r}"


def test_healthcheck_passes_and_has_no_open_incident(compose_exec):
    # A healthy run clears the marker itself (state-mutating, harmless) — this
    # assertion holds regardless of what the earlier calls in this run left behind.
    result = compose_exec("tak-server", ["/opt/tak/healthcheck.sh"])
    assert result.returncode == 0, result.stdout
    marker = compose_exec(
        "tak-server",
        ["sh", "-c", "test -f /opt/tak/logs/incident/.tripped && echo tripped || echo clear"],
    )
    assert "clear" in (marker.stdout or "")


def test_monitor_ignores_a_forged_admin_header_from_the_docker_network(compose_exec):
    """The attack DD-059 closes: another container claiming to be an admin."""
    result = compose_exec(
        "nodered",
        [
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-H",
            "Remote-User: forged",
            "-H",
            "Remote-Groups: monitor_admin",
            "http://monitor:8080/api/users",
        ],
    )
    assert result.stdout.strip() == "403"


def test_lldap_is_not_on_its_default_jwt_secret(compose_cmd, compose_env):
    logs = subprocess.run(
        [*compose_cmd, "logs", "--no-log-prefix", "lldap"],
        capture_output=True,
        text=True,
        env=compose_env,
        timeout=30,
        check=True,
    )
    assert "Default JWT secret used" not in logs.stdout + logs.stderr
