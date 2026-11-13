"""Tests for TAK Server certadmin/groups API client."""

from unittest.mock import patch

import pytest
from app.api.users.tak_server import TakServerClient


@pytest.fixture
def client():
    """Client with mocked SSL — no actual mTLS in tests."""
    with patch.object(TakServerClient, "_init_ssl", return_value=None):
        c = TakServerClient(
            base_url="https://tak-test:8443",
            cert_path="/tmp/test.p12",
            cert_password="testpass",
        )
    return c


@pytest.fixture
def mock_http(client):
    """Patch the client's internal HTTP methods."""
    with patch.object(client, "_get") as mock_get, patch.object(client, "_delete") as mock_delete:
        yield {"get": mock_get, "delete": mock_delete}


class TestListUserCerts:
    def test_returns_certs_for_user(self, client, mock_http):
        mock_http["get"].return_value = {
            "version": "3",
            "data": [
                {
                    "id": 42,
                    "subjectDn": "CN=jsmith",
                    "hash": "abc123",
                    "issuanceDate": "2026-03-27T12:00:00Z",
                    "expirationDate": "2027-03-27T12:00:00Z",
                    "revocationDate": None,
                    "serialNumber": "1234567890",
                },
            ],
        }
        certs = client.list_user_certs("jsmith")
        assert len(certs) == 1
        assert certs[0]["id"] == 42
        assert certs[0]["hash"] == "abc123"
        assert certs[0]["issuance_date"] == "2026-03-27T12:00:00Z"

    def test_returns_empty_for_unknown_user(self, client, mock_http):
        mock_http["get"].return_value = {"version": "3", "data": []}
        certs = client.list_user_certs("nobody")
        assert certs == []

    def test_returns_empty_on_http_error(self, client, mock_http):
        import httpx

        mock_http["get"].side_effect = httpx.HTTPError("connection failed")
        certs = client.list_user_certs("jsmith")
        assert certs == []


class TestRevokeAllUserCertsReportsFailures:
    PEM_CERT = {
        "id": 42,
        "certificate": "PEM",
        "revocationDate": None,
    }

    @pytest.fixture
    def crl(self):
        with (
            patch(
                "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
                return_value={"success": True, "revoked": 0, "errors": []},
            ),
            patch(
                "app.api.service_accounts.cert_gen.revoke_cert_by_pem",
                return_value={"success": True},
            ) as by_pem,
        ):
            yield by_pem

    def test_failed_listing_is_not_success(self, client, mock_http, crl):
        """Empty results from an outage used to read as "every cert revoked"."""
        import httpx

        mock_http["get"].side_effect = httpx.HTTPError("connection failed")
        assert client.revoke_all_user_certs("jsmith") is False

    def test_marks_certadmin_after_the_crl(self, client, mock_http, crl):
        mock_http["get"].return_value = {"data": [self.PEM_CERT]}
        assert client.revoke_all_user_certs("jsmith") is True
        mock_http["delete"].assert_called_once_with("/Marti/api/certadmin/cert/revoke/42")

    def test_certadmin_is_untouched_when_the_crl_fails(self, client, mock_http, crl):
        crl.return_value = {"success": False, "error": "no"}
        mock_http["get"].return_value = {"data": [self.PEM_CERT]}
        assert client.revoke_all_user_certs("jsmith") is False
        mock_http["delete"].assert_not_called()


class TestRevokeCert:
    def test_revokes_by_id(self, client, mock_http):
        mock_http["delete"].return_value = None
        result = client.revoke_cert(42)
        assert result is True
        mock_http["delete"].assert_called_once_with("/Marti/api/certadmin/cert/revoke/42")

    def test_returns_false_on_failure(self, client, mock_http):
        import httpx

        mock_http["delete"].side_effect = httpx.HTTPError("failed")
        result = client.revoke_cert(42)
        assert result is False


class TestRevokeAllUserCerts:
    """Tests for the CRL-based revocation path.

    `revoke_all_user_certs` covers two cert sources:
    - On-disk .pem files (monitor-generated) — handled by
      `revoke_certs_on_disk_for_user` which runs openssl ca -revoke + gencrl.
    - certadmin-only certs (QR-enrolled) — handled by fetching PEM from
      certadmin and feeding to `revoke_cert_by_pem` (also CRL-based).
    """

    def test_returns_true_when_no_certs_anywhere(self, client, mock_http):
        mock_http["get"].return_value = {"data": []}
        with patch(
            "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
            return_value={"success": True, "revoked": 0, "errors": []},
        ):
            assert client.revoke_all_user_certs("jsmith") is True

    def test_revokes_on_disk_certs(self, client, mock_http):
        mock_http["get"].return_value = {"data": []}
        with patch(
            "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
            return_value={"success": True, "revoked": 2, "errors": []},
        ) as mock_disk:
            assert client.revoke_all_user_certs("jsmith") is True
            mock_disk.assert_called_once_with("jsmith")

    def test_revokes_certadmin_certs_via_pem(self, client, mock_http):
        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "1",
                    "certificate": "-----BEGIN CERTIFICATE-----\nPEM1\n-----END CERTIFICATE-----",
                },
            ],
        }
        with (
            patch(
                "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
                return_value={"success": True, "revoked": 0, "errors": []},
            ),
            patch(
                "app.api.service_accounts.cert_gen.revoke_cert_by_pem",
                return_value={"success": True},
            ) as mock_pem,
        ):
            assert client.revoke_all_user_certs("jsmith") is True
            mock_pem.assert_called_once()

    def test_skips_already_revoked_certadmin_certs(self, client, mock_http):
        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": "2026-03-27T00:00:00Z",
                    "serialNumber": "1",
                    "certificate": "-----BEGIN CERTIFICATE-----\nPEM1\n-----END CERTIFICATE-----",
                },
            ],
        }
        with (
            patch(
                "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
                return_value={"success": True, "revoked": 0, "errors": []},
            ),
            patch(
                "app.api.service_accounts.cert_gen.revoke_cert_by_pem",
            ) as mock_pem,
        ):
            assert client.revoke_all_user_certs("jsmith") is True
            mock_pem.assert_not_called()

    def test_returns_false_on_disk_failure(self, client, mock_http):
        mock_http["get"].return_value = {"data": []}
        with patch(
            "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
            return_value={"success": False, "revoked": 0, "errors": ["boom"]},
        ):
            assert client.revoke_all_user_certs("jsmith") is False

    def test_returns_false_when_certadmin_pem_missing(self, client, mock_http):
        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "1",
                    "certificate": "",
                },
            ],
        }
        with patch(
            "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
            return_value={"success": True, "revoked": 0, "errors": []},
        ):
            assert client.revoke_all_user_certs("jsmith") is False

    def test_returns_false_when_pem_revocation_fails(self, client, mock_http):
        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "1",
                    "certificate": "-----BEGIN CERTIFICATE-----\nPEM1\n-----END CERTIFICATE-----",
                },
            ],
        }
        with (
            patch(
                "app.api.service_accounts.cert_gen.revoke_certs_on_disk_for_user",
                return_value={"success": True, "revoked": 0, "errors": []},
            ),
            patch(
                "app.api.service_accounts.cert_gen.revoke_cert_by_pem",
                return_value={"success": False, "error": "crl regen failed"},
            ),
        ):
            assert client.revoke_all_user_certs("jsmith") is False


class TestListClients:
    def test_returns_connected_clients(self, client, mock_http):
        # Fixture matches /Marti/api/subscriptions/all (TAK Server 5.x).
        # The wrapper normalises clientUid -> uid; passes other fields through.
        mock_http["get"].return_value = {
            "version": "3",
            "type": "SubscriptionInfo",
            "data": [
                {
                    "callsign": "ALPHA-1",
                    "clientUid": "ANDROID-abc123",
                    "lastReportMilliseconds": 1719500000000,
                    "takClient": "ATAK-CIV",
                    "takVersion": "5.4.0",
                    "username": "alpha",
                    "groups": [
                        {"name": "Blue", "direction": "IN", "active": True},
                        {"name": "Blue", "direction": "OUT", "active": True},
                    ],
                    "role": "Team Lead",
                    "team": "Blue",
                    "protocol": "tls",
                },
            ],
        }
        clients = client.list_clients()
        assert len(clients) == 1
        assert clients[0]["callsign"] == "ALPHA-1"
        assert clients[0]["uid"] == "ANDROID-abc123"  # normalised from clientUid
        assert clients[0]["team"] == "Blue"
        assert clients[0]["takVersion"] == "5.4.0"
        # groups passed through as list of dicts (kept structured for #21 filtering)
        assert any(g["name"] == "Blue" for g in clients[0]["groups"])
        mock_http["get"].assert_called_once_with("/Marti/api/subscriptions/all")

    def test_returns_empty_on_http_error(self, client, mock_http):
        import httpx

        mock_http["get"].side_effect = httpx.HTTPError("boom")
        assert client.list_clients() == []


class TestListContacts:
    def test_returns_bare_list(self, client, mock_http):
        # Matches /Marti/api/contacts/all on TAK Server 5.x: bare list, fields
        # uid, callsign, team, role, takv, filterGroups (often null), notes.
        # No lastReportTime field is returned by TAK Server.
        mock_http["get"].return_value = [
            {
                "uid": "ANDROID-abc123",
                "callsign": "ALPHA-1",
                "team": "Blue",
                "role": "Team Member",
                "takv": "ATAK-CIV:5.4.0",
                "filterGroups": None,
                "notes": "alpha",
            },
        ]
        contacts = client.list_contacts()
        assert len(contacts) == 1
        assert contacts[0]["uid"] == "ANDROID-abc123"

    def test_handles_envelope_response(self, client, mock_http):
        # Forward-compatible: some TAK versions wrap contacts/all in {"data": [...]}.
        mock_http["get"].return_value = {
            "version": "3",
            "data": [{"uid": "X", "callsign": "X", "team": "Cyan"}],
        }
        contacts = client.list_contacts()
        assert len(contacts) == 1
        assert contacts[0]["uid"] == "X"

    def test_returns_empty_on_http_error(self, client, mock_http):
        import httpx

        mock_http["get"].side_effect = httpx.HTTPError("boom")
        assert client.list_contacts() == []


class TestListMissions:
    def test_returns_mission_list(self, client, mock_http):
        mock_http["get"].return_value = {
            "version": "3",
            "data": [
                {
                    "name": "ops-2026-04",
                    "creatorUid": "abc",
                    "createTime": "2026-04-01T00:00:00Z",
                    "groupVector": "Blue",
                    "uids": [],
                    "contents": [],
                    "expiration": -1,
                    "passwordProtected": False,
                },
            ],
        }
        missions = client.list_missions()
        assert len(missions) == 1
        assert missions[0]["name"] == "ops-2026-04"

    def test_returns_empty_on_http_error(self, client, mock_http):
        import httpx

        mock_http["get"].side_effect = httpx.HTTPError("boom")
        assert client.list_missions() == []
