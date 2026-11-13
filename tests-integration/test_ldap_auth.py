"""LDAP authentication integration tests.

Verifies that passwordless users cannot authenticate via LDAP bind.
The LDAP bind helper is extracted so it can be reused if the LDAP
backend is replaced in the future.
"""

import pytest

pytestmark = pytest.mark.integration

# Inline Python script for raw LDAP bind inside the monitor container.
# Returns the LDAP result code (49 = invalidCredentials).
LDAP_BIND_SCRIPT = """
import socket
s = socket.socket()
s.settimeout(5)
s.connect(('ldap-proxy', 3389))
dn = b'uid={username},ou=people,dc=takldap'
pw = b'{password}'
version = b'\\x02\\x01\\x03'
name = bytes([0x04, len(dn)]) + dn
auth = bytes([0x80, len(pw)]) + pw
bind_req = version + name + auth
bind_app = bytes([0x60, len(bind_req)]) + bind_req
msg_id = b'\\x02\\x01\\x01'
msg = msg_id + bind_app
envelope = bytes([0x30, len(msg)]) + msg
s.send(envelope)
resp = s.recv(1024)
s.close()
for i in range(len(resp)):
    if resp[i] == 0x61:
        j = i + 2
        if resp[j] in (0x0a, 0x02):
            print(resp[j+2])
            break
"""


def attempt_ldap_bind(compose_exec, username: str, password: str = "") -> int:
    """Attempt an LDAP bind and return the result code."""
    script = LDAP_BIND_SCRIPT.replace("{username}", username).replace("{password}", password)
    result = compose_exec("monitor", ["python3", "-c", script])
    try:
        return int(result.stdout.strip())
    except (ValueError, IndexError):
        return -1


class TestLDAPAuth:
    @pytest.fixture(autouse=True)
    def _create_test_user(self, api, user_group, run_id):
        """Create a passwordless user for the LDAP bind test, clean up after.

        The name carries run_id because cleanup deactivates rather than deletes,
        and creating a name that exists is a 409.
        """
        self.username = f"tstnp_{run_id}"
        status, data = api(
            "POST",
            "/api/users",
            {"username": self.username, "name": "Test No Password", "groups": [user_group]},
        )
        assert status == 201, f"Failed to create test user: {data}"
        self.user_id = data["id"]
        yield
        # Cleanup
        if self.user_id:
            api("DELETE", f"/api/users/{self.user_id}")

    def test_passwordless_user_rejected(self, compose_exec):
        """A user with no password should get LDAP result code 49 (invalidCredentials)."""
        result_code = attempt_ldap_bind(compose_exec, self.username, "")
        assert result_code == 49, f"Expected LDAP code 49, got {result_code}"
