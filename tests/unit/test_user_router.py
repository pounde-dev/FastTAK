"""Tests for user management API routes."""

from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from fastapi.testclient import TestClient

# users_router is admin-gated (issue #52); send an admin group on every request.
client = TestClient(app, headers={"Remote-Groups": "monitor_admin"})


def _make_test_ca_pem() -> bytes:
    """Generate a minimal self-signed CA cert for testing truststore generation."""
    from datetime import datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "Test CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(autouse=True)
def mock_clients(monkeypatch):
    """Mock the identity and TAK Server clients used by the router."""
    mock_ak = MagicMock()
    mock_tak = MagicMock()
    monkeypatch.setattr("app.api.users.router._identity", mock_ak)
    monkeypatch.setattr("app.api.users.router._tak_server", mock_tak)
    return mock_ak, mock_tak


# ── Users ─────────────────────────────────────────────────────────


class TestListUsers:
    def test_returns_paginated_users(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_users.return_value = [
            {"id": i, "username": f"user{i}", "name": f"U{i}", "is_active": True, "groups": []}
            for i in range(60)
        ]
        resp = client.get("/api/users?page=2&page_size=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["count"] == 60
        assert len(data["results"]) == 10

    def test_search_param(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_users.return_value = []
        client.get("/api/users?search=john")
        mock_ak.list_users.assert_called_once_with(search="john")

    def test_include_certs_enriches_response(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.list_users.return_value = [
            {"id": 1, "username": "jsmith", "name": "John", "is_active": True, "groups": []},
        ]
        mock_tak.list_user_certs.return_value = [
            {
                "id": 42,
                "hash": "abc",
                "issuance_date": "2026-03-27",
                "expiration_date": "2027-03-27",
                "revocation_date": None,
            },
        ]
        resp = client.get("/api/users?include=certs")
        assert resp.status_code == 200
        assert "certs" in resp.json()["results"][0]
        mock_tak.list_user_certs.assert_called_once_with("jsmith")

    def test_no_certs_key_without_include(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.list_users.return_value = [
            {"id": 1, "username": "jsmith", "name": "John", "is_active": True, "groups": []},
        ]
        resp = client.get("/api/users")
        assert "certs" not in resp.json()["results"][0]
        mock_tak.list_user_certs.assert_not_called()

    def test_503_when_no_token(self, monkeypatch):
        monkeypatch.setattr("app.api.users.router._identity", None)
        from app.config import Settings

        monkeypatch.setattr("app.config.settings", Settings(ldap_admin_password=""))
        resp = client.get("/api/users")
        assert resp.status_code == 503


class TestGetUser:
    def test_returns_user_with_certs(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.list_user_certs.return_value = [{"id": 42}]
        resp = client.get("/api/users/1")
        assert resp.status_code == 200
        assert "certs" in resp.json()

    def test_404_for_missing_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.get("/api/users/999")
        assert resp.status_code == 404


class TestCreateUser:
    def test_creates_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "ops"}]
        mock_ak.create_user.return_value = {
            "id": 10,
            "username": "newuser",
            "name": "New",
            "is_active": True,
            "groups": ["ops"],
            "fastak_user_type": "user",
        }
        resp = client.post(
            "/api/users",
            json={"username": "newuser", "name": "New", "groups": ["ops"]},
        )
        assert resp.status_code == 201

    def test_creates_user_with_ttl(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "ops"}]
        mock_ak.create_user.return_value = {
            "id": 10,
            "username": "temp",
            "name": "Temp",
            "is_active": True,
            "groups": ["ops"],
            "fastak_user_type": "user",
            "fastak_expires": 9999999999,
        }
        resp = client.post(
            "/api/users",
            json={"username": "temp", "name": "Temp", "ttl_hours": 168, "groups": ["ops"]},
        )
        assert resp.status_code == 201

    def test_rejects_invalid_username(self, mock_clients):
        resp = client.post("/api/users", json={"username": "bad user!", "name": "Bad"})
        assert resp.status_code == 422

    def test_requires_groups_on_create(self, mock_clients):
        resp = client.post("/api/users", json={"username": "newuser", "name": "New"})
        assert resp.status_code == 422

    def test_requires_nonempty_groups_on_create(self, mock_clients):
        resp = client.post("/api/users", json={"username": "newuser", "name": "New", "groups": []})
        assert resp.status_code == 422

    def test_creates_user_with_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [
            {"id": "g1", "name": "field_ops"},
        ]
        mock_ak.create_user.return_value = {
            "id": 10,
            "username": "newuser",
            "name": "New",
            "is_active": True,
            "groups": ["field_ops"],
            "fastak_user_type": "user",
        }
        resp = client.post(
            "/api/users",
            json={"username": "newuser", "name": "New", "groups": ["field_ops"]},
        )
        assert resp.status_code == 201
        mock_ak.create_user.assert_called_once_with(
            username="newuser",
            name="New",
            ttl_hours=None,
            groups=["field_ops"],
            user_type="user",
        )

    def test_rejects_nonexistent_groups_on_create(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "field_ops"}]
        resp = client.post(
            "/api/users",
            json={"username": "newuser", "name": "New", "groups": ["NOPE"]},
        )
        assert resp.status_code == 400
        assert "do not exist" in resp.json()["detail"].lower()
        mock_ak.create_user.assert_not_called()


class TestDeleteUser:
    def test_deactivates_and_revokes(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.revoke_all_user_certs.return_value = True
        resp = client.delete("/api/users/1")
        assert resp.status_code == 200
        mock_ak.deactivate_user.assert_called_once_with(1)
        mock_tak.revoke_all_user_certs.assert_called_once_with("jsmith")
        mock_ak.mark_certs_revoked.assert_called_once_with(1)

    def test_does_not_mark_revoked_on_failure(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.revoke_all_user_certs.return_value = False
        resp = client.delete("/api/users/1")
        assert resp.status_code == 200
        mock_ak.deactivate_user.assert_called_once()
        mock_ak.mark_certs_revoked.assert_not_called()

    def test_404_for_hidden_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.delete("/api/users/1")
        assert resp.status_code == 404

    def test_reports_certs_not_revoked(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "jsmith"}
        mock_tak.revoke_all_user_certs.return_value = False
        assert client.delete("/api/users/1").json()["certs_revoked"] is False

    def test_failed_cut_is_an_error_but_certs_are_still_revoked(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "jsmith"}
        mock_ak.deactivate_user.side_effect = RuntimeError("lldap down")
        mock_tak.revoke_all_user_certs.return_value = True
        resp = client.delete("/api/users/1")
        assert resp.status_code == 502
        mock_tak.revoke_all_user_certs.assert_called_once_with("jsmith")

    def test_refuses_webadmin(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "webadmin"}
        resp = client.delete("/api/users/1")
        assert resp.status_code == 409
        assert "TAK_WEBADMIN_PASSWORD" in resp.json()["detail"]
        mock_ak.deactivate_user.assert_not_called()


class TestUpdateUser:
    def test_patch_inactive_cuts_access_like_delete(self, mock_clients):
        """A PATCH that only wrote the attribute would show a live user as inactive."""
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "jsmith", "is_active": True}
        mock_tak.revoke_all_user_certs.return_value = True
        resp = client.patch("/api/users/1", json={"is_active": False})
        assert resp.status_code == 200
        mock_ak.deactivate_user.assert_called_once_with(1)
        mock_tak.revoke_all_user_certs.assert_called_once_with("jsmith")

    def test_patch_refuses_to_deactivate_webadmin(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "webadmin"}
        resp = client.patch("/api/users/1", json={"is_active": False})
        assert resp.status_code == 409
        mock_ak.deactivate_user.assert_not_called()

    def test_patch_refuses_an_expiry_on_webadmin(self, mock_clients):
        """Expiry deactivates the account, and the TTL job has no one to ask."""
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "webadmin"}
        resp = client.patch("/api/users/1", json={"ttl_hours": 1})
        assert resp.status_code == 409
        mock_ak.update_user.assert_not_called()

    def test_patch_can_clear_an_expiry_on_webadmin(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "webadmin"}
        mock_ak.update_user.return_value = {"id": 1, "username": "webadmin"}
        assert client.patch("/api/users/1", json={"ttl_hours": None}).status_code == 200

    def test_patch_active_does_not_deactivate(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "jsmith", "is_active": False}
        mock_ak.update_user.return_value = {"id": 1, "username": "jsmith", "is_active": True}
        assert client.patch("/api/users/1", json={"is_active": True}).status_code == 200
        mock_ak.update_user.assert_called_once_with(1, is_active=True)
        mock_ak.deactivate_user.assert_not_called()

    def test_patch_ttl_null_clears_ttl(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_ak.update_user.return_value = {"id": 1, "username": "jsmith", "is_active": True}
        resp = client.patch("/api/users/1", json={"ttl_hours": None})
        assert resp.status_code == 200
        mock_ak.update_user.assert_called_once()
        kwargs = mock_ak.update_user.call_args[1]
        assert kwargs["ttl_hours"] is None

    def test_patch_name_only_does_not_touch_ttl(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_ak.update_user.return_value = {"id": 1, "username": "jsmith"}
        resp = client.patch("/api/users/1", json={"name": "New Name"})
        assert resp.status_code == 200
        kwargs = mock_ak.update_user.call_args[1]
        assert "ttl_hours" not in kwargs


# ── Password ──────────────────────────────────────────────────────


class TestSetPassword:
    def test_sets_password(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        resp = client.post("/api/users/1/password", json={"password": "secret123"})
        assert resp.status_code == 200
        mock_ak.set_password.assert_called_once_with(1, "secret123")

    def test_400_for_deactivated_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": False,
            "groups": [],
        }
        resp = client.post("/api/users/1/password", json={"password": "secret"})
        assert resp.status_code == 400

    def test_404_for_hidden_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.post("/api/users/1/password", json={"password": "secret"})
        assert resp.status_code == 404


# ── Enrollment ────────────────────────────────────────────────────


class TestEnrollUser:
    def test_generates_enrollment_url(self, mock_clients, mock_settings):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_ak.get_or_create_enrollment_token.return_value = ("abc123", "2026-03-27T12:15:00Z")
        resp = client.post("/api/users/1/enroll")
        assert resp.status_code == 200
        data = resp.json()
        assert "tak://" in data["enrollment_url"]
        assert data["expires_at"] == "2026-03-27T12:15:00Z"

    def test_400_for_deactivated_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": False,
            "groups": [],
        }
        resp = client.post("/api/users/1/enroll")
        assert resp.status_code == 400


# ── Certs ─────────────────────────────────────────────────────────


class TestUserCerts:
    def test_list_certs(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.list_user_certs.return_value = [
            {"id": 42, "hash": "abc123", "expiration_date": "2027-03-30", "revocation_date": None}
        ]
        resp = client.get("/api/users/1/certs")
        assert resp.status_code == 200
        data = resp.json()
        # Unified list — TAK Server cert with no on-disk file shows as non-downloadable
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["cert_id"] == 42
        assert data[0]["downloadable"] is False

    def test_revoke_cert(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.list_user_certs.return_value = [
            {
                "id": 42,
                "hash": "abc",
                "certificate_pem": "---PEM---",
                "serial_number": "1234",
                "expiration_date": None,
                "revocation_date": None,
                "issuance_date": None,
            },
        ]
        mock_tak.revoke_cert.return_value = True
        with patch(
            "app.api.service_accounts.cert_gen.revoke_cert_by_pem", return_value={"success": True}
        ):
            resp = client.post("/api/users/1/certs/revoke", json={"cert_id": 42})
        assert resp.status_code == 200


class TestUserCertDownload:
    def test_returns_p12_file(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        p12_content = b"fake p12 data"
        p12_file = tmp_path / "jsmith-tablet.p12"
        p12_file.write_bytes(p12_content)

        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/1/certs/download/tablet")

        assert r.status_code == 200
        assert r.content == p12_content
        assert "application/x-pkcs12" in r.headers["content-type"]

    def test_404_when_cert_missing(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/1/certs/download/tablet")

        assert r.status_code == 404

    def test_404_when_user_not_found(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None

        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/999/certs/download/tablet")

        assert r.status_code == 404

    def test_400_for_invalid_cert_name(self, mock_clients):
        r = client.get("/api/users/1/certs/download/../../etc/passwd")
        # FastAPI rejects this at the URL routing level (/ in path segment)
        # but even if it got through, the validation would catch it
        assert r.status_code in (400, 404, 422)


class TestUserCertDataPackage:
    def test_returns_valid_zip(self, mock_clients, mock_settings, tmp_path):
        """Happy path: returns a zip with the expected 4 entries."""
        import zipfile
        from io import BytesIO

        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        # Create client .p12 and ca.pem on disk
        (tmp_path / "jsmith-tablet.p12").write_bytes(b"fake-p12")
        (tmp_path / "ca.pem").write_bytes(
            # Minimal self-signed cert for truststore generation
            _make_test_ca_pem()
        )

        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/1/certs/download_data_package/tablet")

        assert r.status_code == 200
        assert "application/zip" in r.headers["content-type"]
        assert "jsmith-tablet.zip" in r.headers["content-disposition"]

        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            names = zf.namelist()
            assert "certs/jsmith-tablet.p12" in names
            assert "certs/truststore.p12" in names
            assert "config.pref" in names
            assert "MANIFEST/manifest.xml" in names

            # Verify config.pref contains the server address
            config = zf.read("config.pref").decode()
            assert "test.example.com:8089:ssl" in config
            assert "cert/truststore.p12" in config
            assert "cert/jsmith-tablet.p12" in config

            # Verify manifest references all zip entries
            manifest = zf.read("MANIFEST/manifest.xml").decode()
            assert "certs/jsmith-tablet.p12" in manifest
            assert "certs/truststore.p12" in manifest
            assert "config.pref" in manifest

    def test_truststore_has_named_trusted_entry(self, mock_clients, mock_settings, tmp_path):
        """Java-family loaders (ATAK's truststore validator, keytool) skip cert
        bags without a friendlyName, so an unnamed CA loads as an empty store."""
        import zipfile
        from io import BytesIO

        from cryptography.hazmat.primitives.serialization import pkcs12

        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        (tmp_path / "jsmith-tablet.p12").write_bytes(b"fake-p12")
        (tmp_path / "ca.pem").write_bytes(_make_test_ca_pem())

        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/1/certs/download_data_package/tablet")

        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            truststore = zf.read("certs/truststore.p12")

        loaded = pkcs12.load_pkcs12(truststore, b"atakatak")
        assert loaded.key is None
        assert loaded.cert is None
        assert [c.friendly_name for c in loaded.additional_certs] == [b"truststore"]

    def test_404_when_cert_missing(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        # No .p12 file on disk
        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/1/certs/download_data_package/tablet")
        assert r.status_code == 404

    def test_404_when_user_not_found(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/999/certs/download_data_package/tablet")
        assert r.status_code == 404

    def test_400_for_invalid_cert_name(self, mock_clients):
        r = client.get("/api/users/1/certs/download_data_package/../../etc/passwd")
        assert r.status_code in (400, 404, 422)

    def test_403_for_revoked_cert(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        (tmp_path / "jsmith-tablet.p12").write_bytes(b"fake-p12")
        (tmp_path / "jsmith-tablet.pem").write_bytes(b"fake-pem")

        with (
            patch("app.api.users.router.CERT_FILES_PATH", tmp_path),
            patch("app.api.users.router._get_cert_serial", return_value="abc123"),
            patch("app.api.users.router._get_revoked_serials", return_value={"abc123"}),
        ):
            r = client.get("/api/users/1/certs/download_data_package/tablet")
        assert r.status_code == 403

    def test_500_when_ca_pem_missing(self, mock_clients, mock_settings, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        (tmp_path / "jsmith-tablet.p12").write_bytes(b"fake-p12")
        # No ca.pem — should 500

        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.get("/api/users/1/certs/download_data_package/tablet")
        assert r.status_code == 500
        assert "CA certificate" in r.json()["detail"]


class TestUserCertGeneration:
    def test_generates_named_cert(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        with (
            patch("app.api.users.router.CERT_FILES_PATH", tmp_path),
            patch("app.api.service_accounts.cert_gen.generate_client_cert") as mock_gen,
        ):
            mock_gen.return_value = {"success": True}
            r = client.post("/api/users/1/certs/generate", json={"name": "tablet"})
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "tablet"
        assert data["filename"] == "jsmith-tablet.p12"
        mock_gen.assert_called_once_with("jsmith-tablet", validity_days=365, cn_override="jsmith")

    def test_409_duplicate_cert_name(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        # Create existing cert file
        (tmp_path / "jsmith-tablet.p12").write_bytes(b"exists")
        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.post("/api/users/1/certs/generate", json={"name": "tablet"})
        assert r.status_code == 409

    def test_409_when_the_name_lands_on_any_existing_file(self, mock_clients, tmp_path):
        """User "ca" with cert name "do-not-share" names the CA's private key."""
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "ca", "is_active": True}
        (tmp_path / "ca-do-not-share.key").write_bytes(b"the CA key")
        with (
            patch("app.api.users.router.CERT_FILES_PATH", tmp_path),
            patch("app.api.service_accounts.cert_gen.generate_client_cert") as mock_gen,
        ):
            r = client.post("/api/users/1/certs/generate", json={"name": "do-not-share"})
        assert r.status_code == 409
        mock_gen.assert_not_called()

    def test_400_for_deactivated_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": False,
            "groups": [],
        }
        r = client.post("/api/users/1/certs/generate", json={"name": "tablet"})
        assert r.status_code == 400

    def test_400_for_expired_user(self, mock_clients, tmp_path):
        import time

        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
            "fastak_expires": int(time.time()) - 3600,  # expired 1 hour ago
        }
        with patch("app.api.users.router.CERT_FILES_PATH", tmp_path):
            r = client.post("/api/users/1/certs/generate", json={"name": "tablet"})
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()

    def test_validity_capped_by_user_expiry(self, mock_clients, tmp_path):
        import time

        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
            "fastak_expires": int(time.time()) + 86400 * 30,  # expires in 30 days
        }
        with (
            patch("app.api.users.router.CERT_FILES_PATH", tmp_path),
            patch("app.api.service_accounts.cert_gen.generate_client_cert") as mock_gen,
        ):
            mock_gen.return_value = {"success": True}
            r = client.post("/api/users/1/certs/generate", json={"name": "tablet"})
        assert r.status_code == 201
        # Validity should be capped at ~30 days, not 365
        call_args = mock_gen.call_args
        assert call_args[1]["validity_days"] <= 31

    def test_invalid_cert_name(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        r = client.post("/api/users/1/certs/generate", json={"name": "bad name!"})
        assert r.status_code == 422

    def test_404_for_missing_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        r = client.post("/api/users/999/certs/generate", json={"name": "tablet"})
        assert r.status_code == 404


# ── Groups ────────────────────────────────────────────────────────


class TestGroups:
    def test_list_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "OPS"}]
        resp = client.get("/api/groups")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "g1", "name": "OPS"}]

    def test_create_group(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_group.return_value = {"id": "g2", "name": "FDNY Robotics"}
        resp = client.post("/api/groups", json={"name": "FDNY Robotics"})
        assert resp.status_code == 201

    def test_get_group_detail(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_group.return_value = {
            "id": "g1",
            "name": "ROLE_ADMIN",
            "members": [{"id": 1, "username": "jsmith"}],
        }
        resp = client.get("/api/groups/g1")
        assert resp.status_code == 200
        assert "members" in resp.json()

    def test_delete_group(self, mock_clients):
        mock_ak, _ = mock_clients
        resp = client.delete("/api/groups/g1")
        assert resp.status_code == 200
        mock_ak.delete_group.assert_called_once_with("g1")

    def test_set_user_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
            "fastak_user_type": "user",
        }
        mock_ak.list_groups.return_value = [
            {"id": "g1", "name": "ROLE_ADMIN"},
            {"id": "g2", "name": "FDNY Robotics"},
        ]
        resp = client.put(
            "/api/users/1/groups",
            json={"groups": ["ROLE_ADMIN", "FDNY Robotics"]},
        )
        assert resp.status_code == 200
        mock_ak.set_user_groups.assert_called_once_with(1, ["ROLE_ADMIN", "FDNY Robotics"])

    def test_rejects_nonexistent_groups_on_set(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": ["ops"],
            "fastak_user_type": "user",
        }
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "field_ops"}]
        resp = client.put("/api/users/1/groups", json={"groups": ["NOPE"]})
        assert resp.status_code == 400
        assert "do not exist" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()

    def test_rejects_empty_groups_for_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": ["field_ops"],
            "fastak_user_type": "user",
        }
        resp = client.put("/api/users/1/groups", json={"groups": []})
        assert resp.status_code == 400
        assert "at least one group" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()

    def test_rejects_groups_for_admin_service_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_admin_bot",
            "name": "Admin Bot",
            "is_active": True,
            "groups": [],
            "fastak_user_type": "svc_admin",
        }
        resp = client.put("/api/users/1/groups", json={"groups": ["field_ops"]})
        assert resp.status_code == 400
        assert "admin" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()

    def test_rejects_empty_groups_for_data_service_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_sensor1",
            "name": "Sensor 1",
            "is_active": True,
            "groups": ["field_ops"],
            "fastak_user_type": "svc_data",
        }
        resp = client.put("/api/users/1/groups", json={"groups": []})
        assert resp.status_code == 400
        assert "at least one group" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()

    def test_allows_groups_for_data_service_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_sensor1",
            "name": "Sensor 1",
            "is_active": True,
            "groups": ["field_ops"],
            "fastak_user_type": "svc_data",
        }
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "ops"}]
        resp = client.put("/api/users/1/groups", json={"groups": ["ops"]})
        assert resp.status_code == 200
        mock_ak.set_user_groups.assert_called_once_with(1, ["ops"])

    def test_untyped_user_defaults_to_user_rules(self, mock_clients):
        """Accounts created outside the API (no fastak_user_type) get 'user' enforcement."""
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": ["ops"],
            # No fastak_user_type key
        }
        resp = client.put("/api/users/1/groups", json={"groups": []})
        assert resp.status_code == 400
        assert "at least one group" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()


class TestNameCollisions:
    def test_creating_an_existing_user_is_a_conflict(self, mock_clients):
        from app.api.users.identity import UserExistsError

        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"name": "ops"}]
        mock_ak.create_user.side_effect = UserExistsError("jsmith")
        resp = client.post(
            "/api/users", json={"username": "jsmith", "name": "J", "groups": ["ops"]}
        )
        assert resp.status_code == 409

    @pytest.mark.parametrize(
        "name", ["a,monitor_admin", "a=b", " lead", "trail ", "two  spaces", ""]
    )
    def test_group_names_that_could_read_as_two_groups_are_refused(self, mock_clients, name):
        mock_ak, _ = mock_clients
        assert client.post("/api/groups", json={"name": name}).status_code == 422
        mock_ak.create_group.assert_not_called()

    def test_revoking_by_name_names_the_owner(self, mock_clients):
        """Alice's "b-phone" is the same file as alice-b's "phone"."""
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "alice", "is_active": True}
        with patch(
            "app.api.service_accounts.cert_gen.revoke_cert_by_name",
            return_value={"success": True},
        ) as revoke:
            resp = client.post("/api/users/1/certs/revoke", json={"cert_name": "b-phone"})
        assert resp.status_code == 200
        revoke.assert_called_once_with("alice-b-phone", owner="alice")
