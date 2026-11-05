"""Deactivating a user cuts their access, checked against real LLDAP and ldap-proxy (DD-061).

Each test makes its own user because deactivation is what is under test.
"""

import time
from urllib.parse import parse_qs, urlparse

import pytest
from test_nodered_gate import PROBE

pytestmark = pytest.mark.integration

PASSWORD = "DeactivateTestPass123!"


def _verify(compose_exec, username: str, password: str) -> str:
    result = compose_exec("monitor", ["python3", "-c", PROBE, username, password, ""])
    return result.stdout.strip()


@pytest.fixture
def user(api, run_id, user_group, request):
    suffix = abs(hash(request.node.name)) % 100000
    username = f"tstd_{run_id}_{suffix}"
    status, data = api(
        "POST",
        "/api/users",
        {"username": username, "name": "Deactivate test", "groups": [user_group]},
    )
    assert status == 201, data
    uid = data["id"]
    status, data = api("POST", f"/api/users/{uid}/password", {"password": PASSWORD})
    assert status == 200, data
    yield uid, username
    api("DELETE", f"/api/users/{uid}")


def _deactivation_routes(api, uid):
    return {
        "delete": lambda: api("DELETE", f"/api/users/{uid}"),
        "patch": lambda: api("PATCH", f"/api/users/{uid}", {"is_active": False}),
    }


@pytest.mark.parametrize("route", ["delete", "patch"])
def test_password_stops_working(api, compose_exec, user, route):
    uid, username = user
    assert _verify(compose_exec, username, PASSWORD) == "200"
    status, data = _deactivation_routes(api, uid)[route]()
    assert status == 200, data
    assert _verify(compose_exec, username, PASSWORD) == "401"


def test_enrollment_token_stops_working(api, compose_exec, user):
    uid, username = user
    status, data = api("POST", f"/api/users/{uid}/enroll")
    assert status == 200, data
    token = parse_qs(urlparse(data["enrollment_url"]).query)["token"][0]
    assert _verify(compose_exec, username, token) == "200"
    status, data = api("DELETE", f"/api/users/{uid}")
    assert status == 200, data
    assert _verify(compose_exec, username, token) == "401"


def test_groups_are_removed(api, user):
    uid, _ = user
    status, data = api("DELETE", f"/api/users/{uid}")
    assert status == 200, data
    status, data = api("GET", f"/api/users/{uid}")
    assert status == 200, data
    assert data["groups"] == []


def test_reactivated_user_needs_a_new_password(api, compose_exec, user):
    uid, username = user
    api("DELETE", f"/api/users/{uid}")
    status, data = api("PATCH", f"/api/users/{uid}", {"is_active": True})
    assert status == 200, data
    assert _verify(compose_exec, username, PASSWORD) == "401"
    status, data = api("POST", f"/api/users/{uid}/password", {"password": PASSWORD})
    assert status == 200, data
    assert _verify(compose_exec, username, PASSWORD) == "200"


def test_deactivating_again_is_safe(api, user):
    uid, _ = user
    assert api("DELETE", f"/api/users/{uid}")[0] == 200
    assert api("DELETE", f"/api/users/{uid}")[0] == 200


def test_inactive_account_that_still_has_groups_is_cut_by_the_scheduler(api, user, user_group):
    """Stands in for an account deactivated before deactivation cut anything:
    inactive, yet still in a group. The TTL job runs every 60 seconds."""
    uid, _ = user
    assert api("DELETE", f"/api/users/{uid}")[0] == 200
    status, data = api("PUT", f"/api/users/{uid}/groups", {"groups": [user_group]})
    assert status == 200, data

    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        _, data = api("GET", f"/api/users/{uid}")
        if data["groups"] == []:
            return
        time.sleep(5)
    pytest.fail("the scheduler left an inactive account in its group")
