"""`/auth/verify?group=` end to end against real LLDAP group membership (DD-058).

Caddy's handling of the parameter is checked in tests/test_caddyfile.py; the
test stack publishes Node-RED directly, so this goes straight to ldap-proxy.
"""

import pytest

pytestmark = pytest.mark.integration

PROBE = """
import base64, sys, urllib.error, urllib.request
username, password, query = sys.argv[1:4]
credential = base64.b64encode(f'{username}:{password}'.encode()).decode()
request = urllib.request.Request(
    'http://ldap-proxy:8080/auth/verify' + query,
    headers={'Authorization': 'Basic ' + credential},
)
try:
    print(urllib.request.urlopen(request, timeout=5).status)
except urllib.error.HTTPError as error:
    print(error.code)
"""

PASSWORD = "GateTestPass123!"


@pytest.fixture(scope="module")
def plain_user(api, run_id, user_group):
    """A user with a password, in a group that is not an admin group."""
    username = f"tstg_{run_id}"
    status, data = api(
        "POST",
        "/api/users",
        {"username": username, "name": f"Gate {run_id}", "groups": [user_group]},
    )
    assert status == 201, data
    uid = data["id"]
    status, data = api("POST", f"/api/users/{uid}/password", {"password": PASSWORD})
    assert status == 200, data
    yield username
    api("DELETE", f"/api/users/{uid}")


def _verify(compose_exec, username: str, query: str) -> str:
    result = compose_exec("monitor", ["python3", "-c", PROBE, username, PASSWORD, query])
    return result.stdout.strip()


def test_user_passes_without_a_group_requirement(compose_exec, plain_user):
    assert _verify(compose_exec, plain_user, "") == "200"


def test_user_outside_the_group_is_refused(compose_exec, plain_user):
    assert _verify(compose_exec, plain_user, "?group=monitor_admin") == "403"


def test_user_inside_the_group_passes(compose_exec, plain_user, user_group):
    # The monitor stores TAK groups in LLDAP with a tak_ prefix.
    assert _verify(compose_exec, plain_user, f"?group=tak_{user_group}") == "200"
