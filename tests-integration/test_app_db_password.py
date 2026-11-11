"""Rotating APP_DB_PASSWORD reaches the database on the next app-db start.

The sync used to pass the password to `psql -c`, which does not substitute
variables, and sent the error to /dev/null, so rotation silently did nothing.
"""

import json
import subprocess
import time

import pytest

pytestmark = pytest.mark.integration

# -h app-db goes over TCP, where pg_hba asks for a password. The Unix socket is
# trusted, so it would pass whatever the password.
LOGIN = 'PGPASSWORD="$1" psql -h app-db -U "$POSTGRES_USER" -d postgres -tAc "SELECT 1"'
MAKE_STALE = (
    'psql -U "$POSTGRES_USER" -d postgres'
    " -c \"ALTER ROLE $POSTGRES_USER PASSWORD 'stale-password'\""
)


def _login(compose_exec, password: str) -> subprocess.CompletedProcess:
    return compose_exec("app-db", ["sh", "-c", LOGIN, "sh", password])


def _wait_until_healthy(compose_cmd, compose_env, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ps = subprocess.run(
            [*compose_cmd, "ps", "--format", "json"],
            capture_output=True, text=True, env=compose_env,
        )  # fmt: skip
        services = [json.loads(line) for line in ps.stdout.splitlines() if line.strip()]
        if services and all(
            s.get("Health") in ("", "healthy")
            and (s.get("State") == "running" or s.get("ExitCode") == 0)
            for s in services
        ):
            return
        time.sleep(3)
    pytest.fail("stack did not return to healthy after restarting app-db")


def test_restart_applies_the_password_from_env(compose_exec, compose_cmd, compose_env):
    password = compose_exec("app-db", ["printenv", "POSTGRES_PASSWORD"]).stdout.strip()
    assert password

    # A volume that still holds an older password, as after rotating .env.
    stale = compose_exec("app-db", ["sh", "-c", MAKE_STALE])
    assert stale.returncode == 0, stale.stderr
    assert _login(compose_exec, password).returncode != 0

    subprocess.run([*compose_cmd, "restart", "app-db"], check=True, env=compose_env)
    _wait_until_healthy(compose_cmd, compose_env)

    result = _login(compose_exec, password)
    assert result.returncode == 0, result.stderr
