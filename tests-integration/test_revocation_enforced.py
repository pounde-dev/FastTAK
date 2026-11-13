"""A revoked cert is refused by TAK Server itself (DD-063).

Every other revocation test stops at "the serial is in ca.crl". This one
connects, because the list only matters if TAK Server reads it.
"""

import time

import pytest

pytestmark = pytest.mark.integration

# stdin stays open for a moment so a refusal that arrives after the handshake
# (TLS 1.3) is still read before s_client exits.
HANDSHAKE = (
    'sleep 3 | openssl s_client -connect 127.0.0.1:8089 -cert "$1.pem" -key "$1.key"'
    ' -pass pass:atakatak 2>&1; echo "exit=$?"'
)


def _accepted(compose_exec, cert_path: str) -> tuple[bool, str]:
    output = compose_exec("tak-server", ["sh", "-c", HANDSHAKE, "sh", cert_path]).stdout
    return "exit=0" in output and "alert" not in output, output


@pytest.fixture
def user_cert(api, run_id, user_group):
    username = f"tstrev_{run_id}"
    status, data = api(
        "POST",
        "/api/users",
        {"username": username, "name": "Revocation test", "groups": [user_group]},
    )
    assert status == 201, data
    uid = data["id"]
    status, data = api("POST", f"/api/users/{uid}/certs/generate", {"name": "dev"})
    assert status == 200, data
    yield uid, f"/opt/tak/certs/files/{username}-dev"
    api("DELETE", f"/api/users/{uid}")


def test_tak_server_refuses_a_revoked_cert(api, compose_exec, user_cert):
    uid, cert_path = user_cert

    accepted, output = _accepted(compose_exec, cert_path)
    assert accepted, f"a valid cert should connect before it is revoked:\n{output}"

    status, data = api("POST", f"/api/users/{uid}/certs/revoke", {"cert_name": "dev"})
    assert status == 200, data

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        accepted, output = _accepted(compose_exec, cert_path)
        if not accepted:
            return
        time.sleep(10)
    pytest.fail(
        "TAK Server still accepts the cert two minutes after it was revoked. Either it "
        "does not read ca.crl, or it reads it only at startup; restart tak-server and "
        f"connect again to tell which.\n{output}"
    )
