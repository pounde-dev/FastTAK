"""Fixtures for integration tests against a running FastTAK stack.

The stack must be running before tests start (via `just test-up`).
These fixtures discover the stack and provide transport for API calls.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from glob import glob
from pathlib import Path

import httpx
import pytest

# Port exposed by docker-compose.test.yml ("18180:8080")
MONITOR_HOST_PORT = 18180

# ---------------------------------------------------------------------------
# Stack discovery
# ---------------------------------------------------------------------------


def _find_state_file() -> Path | None:
    project = os.environ.get("FASTAK_TEST_PROJECT")
    if project and Path(f"/tmp/{project}/.test-state").exists():
        return Path(f"/tmp/{project}/.test-state")
    # Fall back to globbing — picks up a stack created by `--no-up`
    # mid-session even when FASTAK_TEST_PROJECT points at one that's
    # been torn down.
    candidates = sorted(glob("/tmp/fastak-test-*/.test-state"), reverse=True)
    return Path(candidates[0]) if candidates else None


def _read_key_values(path: Path) -> dict[str, str]:
    """Parse bash KEY=value lines, as in the state file and .env."""
    info = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            # Strip surrounding quotes
            info[key] = value.strip("\"'")
    return info


@dataclass
class StackInfo:
    project: str
    test_dir: str
    tak_host_path: str
    repo_dir: str
    env_file: str


@pytest.fixture
def stack_info() -> StackInfo:
    """Discover the currently-running test stack from /tmp/fastak-test-*/.test-state.

    Function-scoped on purpose: `test_backup_restore` tears the original
    stack down and brings up a fresh one mid-session, so any session-scoped
    cache here goes stale and either (silently) skips downstream tests or
    points docker compose at a project that no longer exists. The glob
    cost per test is negligible and the resilience is worth more than the
    cache hit.
    """
    state_file = _find_state_file()
    if state_file is None:
        pytest.skip("No running test stack. Run 'just test-up' first.")
    info = _read_key_values(state_file)

    return StackInfo(
        project=info["PROJECT"],
        test_dir=info["TEST_DIR"],
        tak_host_path=info["TAK_HOST_PATH"],
        repo_dir=info["REPO_DIR"],
        env_file=info["ENV_FILE"],
    )


# ---------------------------------------------------------------------------
# Docker Compose exec transport
# ---------------------------------------------------------------------------


@pytest.fixture
def compose_env(stack_info) -> dict[str, str]:
    """Environment docker compose needs to interpolate docker-compose.test.yml.

    `test-stack.sh up` exports these in its own shell; pytest runs in a different
    process and inherits nothing. `HOST_ENV_FILE` has no default in the compose
    file, so without it compose expands `${HOST_ENV_FILE}:/host/.env:ro` to
    `:/host/.env:ro` and any `up` fails with "invalid spec". The other two do
    have defaults, but they resolve relative to the repo rather than to this
    stack's directory.
    """
    return {
        **os.environ,
        "TAK_HOST_PATH": stack_info.tak_host_path,
        "BACKUP_DIR": f"{stack_info.test_dir}/backups",
        "HOST_ENV_FILE": stack_info.env_file,
    }


@pytest.fixture
def compose_cmd(stack_info) -> list[str]:
    """Return the base docker compose command for this test stack.

    Pair it with `compose_env` when running the command — see that fixture.
    """
    return [
        "docker",
        "compose",
        "-p",
        stack_info.project,
        "-f",
        f"{stack_info.repo_dir}/docker-compose.yml",
        "-f",
        f"{stack_info.repo_dir}/docker-compose.test.yml",
        "--env-file",
        stack_info.env_file,
    ]


@pytest.fixture
def compose_exec(compose_cmd, compose_env):
    """Run a command inside a container. Returns subprocess.CompletedProcess.

    Usage: compose_exec("monitor", ["curl", "-sf", "http://localhost:8080/api/ping"])
    """

    def _exec(service: str, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        full_cmd = [*compose_cmd, "exec", "-T", service, *cmd]
        kwargs.setdefault("env", compose_env)
        result = subprocess.run(full_cmd, capture_output=True, timeout=30, **kwargs)
        # Decode lossily so binary responses (e.g. P12 certs) don't crash.
        for attr in ("stdout", "stderr"):
            val = getattr(result, attr)
            if isinstance(val, bytes):
                setattr(result, attr, val.decode("utf-8", errors="replace"))
        return result

    return _exec


# ---------------------------------------------------------------------------
# Monitor API client
# ---------------------------------------------------------------------------


def admin_headers() -> dict[str, str]:
    """What Caddy sends after a successful login by an admin.

    The test stack publishes the monitor port directly, so the tests supply
    these. Every route except /api/ping is admin-gated (issue #52), and the
    monitor ignores identity headers without the proxy secret (DD-059). Read
    per call because test_backup_restore replaces the stack mid-session.
    """
    state = _read_key_values(_find_state_file())
    secret = _read_key_values(Path(state["ENV_FILE"]))["MONITOR_PROXY_SECRET"]
    return {
        "Remote-User": "integration-tester",
        "Remote-Groups": "monitor_admin",
        "X-FastTAK-Proxy-Secret": secret,
    }


@pytest.fixture(scope="session")
def api():
    """Call the Monitor API as an admin. Returns (status_code, parsed_json_or_None).

    Usage: status, data = api("GET", "/api/ping")
    """
    with httpx.Client(base_url=f"http://localhost:{MONITOR_HOST_PORT}", timeout=30) as client:

        def _call(method: str, path: str, json_data: dict | None = None):
            response = client.request(method, path, json=json_data, headers=admin_headers())
            try:
                body = response.json()
            except ValueError:
                body = None
            return response.status_code, body

        yield _call


# ---------------------------------------------------------------------------
# Shared resource names (unique per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def run_id():
    """Unique suffix for test resources, preventing name collisions."""
    return str(int(time.time()))


@pytest.fixture(scope="session")
def user_group_name(run_id):
    """Group name used for user creation tests (groups are now required)."""
    return f"USR_TST_{run_id}"


@pytest.fixture(scope="session")
def user_group(api, user_group_name, created_resources):
    """Create a shared group for user-creation tests.

    Users now require at least one group, so any test that creates a user
    needs this group to exist first.
    """
    status, data = api("POST", "/api/groups", {"name": user_group_name})
    assert status == 201, f"Failed to create user test group: {data}"
    created_resources["user_group_id"] = data["id"]
    return user_group_name


@pytest.fixture(scope="session")
def test_user_name(run_id):
    return f"tstu_{run_id}"


@pytest.fixture(scope="session")
def test_lifecycle_user_name(run_id):
    return f"tstl_{run_id}"


@pytest.fixture(scope="session")
def test_group_name(run_id):
    return f"TST_GRP_{run_id}"


@pytest.fixture(scope="session")
def test_cert_name(run_id):
    return f"tstcert_{run_id}"


@pytest.fixture(scope="session")
def test_dup_cert_name(run_id):
    return f"tstdup_{run_id}"


@pytest.fixture(scope="session")
def svc_test_group_name(run_id):
    return f"SVC_TST_{run_id}"


@pytest.fixture(scope="session")
def svc_data_name(run_id):
    return f"tstd_{run_id}"


@pytest.fixture(scope="session")
def svc_admin_name(run_id):
    return f"tsta_{run_id}"


@pytest.fixture(scope="session")
def webadmin_id(api):
    """Resolve the webadmin user ID."""
    status, data = api("GET", "/api/users?search=webadmin")
    assert status == 200
    results = data.get("results", [])
    assert len(results) > 0, "webadmin user not found"
    return results[0]["id"]


@pytest.fixture
def ldap_admin_password(stack_info):
    """Read LDAP admin password from test .env."""
    for line in Path(stack_info.env_file).read_text().splitlines():
        if line.startswith("LDAP_BIND_PASSWORD="):
            return line.split("=", 1)[1].strip()
    pytest.fail("LDAP_BIND_PASSWORD not found in test .env")


# ---------------------------------------------------------------------------
# Shared mutable state for ordered lifecycle tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def created_resources():
    """Session-wide dict for sharing resource IDs between ordered tests.

    Prefer this over self.__class__ -- it's explicit, survives across
    test files, and doesn't break under parallel execution.

    Keys are set by test functions: created_resources["user_id"] = 123
    """
    return {}


# ---------------------------------------------------------------------------
# Cleanup (session teardown)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_resources(api, created_resources):
    """Clean up test resources after all tests complete.

    Runs in reverse creation order: service accounts -> groups -> users.
    Failures are logged but don't fail the test run.
    """
    yield
    # Teardown
    for key in ("svc_data_id", "svc_admin_id", "enforce_svc_admin_id", "enforce_svc_data_id"):
        rid = created_resources.get(key)
        if rid:
            api("DELETE", f"/api/service-accounts/{rid}")

    for key in ("svc_group_id", "test_group_id", "user_group_id", "enforce_group_id"):
        rid = created_resources.get(key)
        if rid:
            api("DELETE", f"/api/groups/{rid}")

    for key in ("lifecycle_user_id", "enforce_user_id"):
        rid = created_resources.get(key)
        if rid:
            api("DELETE", f"/api/users/{rid}")
