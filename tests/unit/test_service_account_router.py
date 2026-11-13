"""Tests for service account management API routes."""

from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from fastapi.testclient import TestClient

# service_accounts_router is admin-gated (issue #52); send an admin group on every request.
client = TestClient(app, headers={"Remote-Groups": "monitor_admin"})


@pytest.fixture(autouse=True)
def mock_clients(monkeypatch):
    """Mock the identity and TAK Server clients used by the service accounts router."""
    mock_ak = MagicMock()
    mock_tak = MagicMock()
    # Default: common test groups exist
    mock_ak.list_groups.return_value = [
        {"id": "g1", "name": "field_ops"},
        {"id": "g2", "name": "ops"},
        {"id": "g3", "name": "AIR_OPS"},
        {"id": "g4", "name": "INTEG_TEST"},
        {"id": "g5", "name": "g"},
    ]
    monkeypatch.setattr("app.api.service_accounts.router._identity", mock_ak)
    monkeypatch.setattr("app.api.service_accounts.router._tak_server", mock_tak)
    return mock_ak, mock_tak


@pytest.fixture(autouse=True)
def mock_nodered_pems(monkeypatch):
    """Mock the Node-RED PEM extraction/cleanup — they exec into the tak-server
    container, which isn't available in unit tests. The router treats these as
    best-effort and logs on failure, so we just stub them out.
    """
    monkeypatch.setattr(
        "app.api.service_accounts.router.write_nodered_pems",
        lambda name: {"success": True},
    )
    monkeypatch.setattr(
        "app.api.service_accounts.router.remove_nodered_pems",
        lambda name: {"success": True},
    )


# ── Create ───────────────────────────────────────────────────────


class TestCreateServiceAccount:
    def test_create_data_account_with_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 10,
            "username": "svc_sensor1",
            "name": "Sensor 1",
            "is_active": True,
            "groups": ["field_ops"],
        }
        with patch(
            "app.api.service_accounts.router.generate_client_cert",
            return_value={"success": True},
        ):
            resp = client.post(
                "/api/service-accounts",
                json={
                    "name": "sensor1",
                    "display_name": "Sensor 1",
                    "mode": "data",
                    "groups": ["field_ops"],
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "svc_sensor1"
        assert data["mode"] == "data"
        assert data["validity_days"] == 365
        assert "cert_download_url" in data
        mock_ak.create_user.assert_called_once_with(
            username="svc_sensor1",
            name="Sensor 1",
            groups=["field_ops"],
            user_type="svc_data",
        )

    def test_create_admin_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 11,
            "username": "svc_admin_bot",
            "name": "Admin Bot",
            "is_active": True,
            "groups": [],
        }
        with (
            patch(
                "app.api.service_accounts.router.generate_client_cert",
                return_value={"success": True},
            ),
            patch(
                "app.api.service_accounts.router.register_admin_cert",
                return_value={"success": True, "output": "OK"},
            ) as mock_certmod,
        ):
            resp = client.post(
                "/api/service-accounts",
                json={
                    "name": "admin_bot",
                    "display_name": "Admin Bot",
                    "mode": "admin",
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["mode"] == "admin"
        assert data["validity_days"] == 730
        mock_certmod.assert_called_once_with("svc_admin_bot")
        # Admin mode should not pass groups
        mock_ak.create_user.assert_called_once_with(
            username="svc_admin_bot",
            name="Admin Bot",
            groups=None,
            user_type="svc_admin",
        )

    def test_data_mode_requires_groups(self, mock_clients):
        resp = client.post(
            "/api/service-accounts",
            json={
                "name": "sensor1",
                "display_name": "Sensor 1",
                "mode": "data",
            },
        )
        assert resp.status_code == 422

    def test_data_mode_requires_nonempty_groups(self, mock_clients):
        resp = client.post(
            "/api/service-accounts",
            json={
                "name": "sensor1",
                "display_name": "Sensor 1",
                "mode": "data",
                "groups": [],
            },
        )
        assert resp.status_code == 422

    def test_admin_mode_forbids_groups(self, mock_clients):
        resp = client.post(
            "/api/service-accounts",
            json={
                "name": "admin_bot",
                "display_name": "Admin Bot",
                "mode": "admin",
                "groups": ["field_ops"],
            },
        )
        assert resp.status_code == 422

    def test_data_mode_rejects_nonexistent_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "field_ops"}]
        resp = client.post(
            "/api/service-accounts",
            json={
                "name": "sensor1",
                "display_name": "Sensor 1",
                "mode": "data",
                "groups": ["DOES_NOT_EXIST"],
            },
        )
        assert resp.status_code == 400
        assert "do not exist" in resp.json()["detail"].lower()
        # Should NOT have created the user
        mock_ak.create_user.assert_not_called()

    def test_auto_prepend_svc_prefix(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 12,
            "username": "svc_mybot",
            "name": "My Bot",
            "is_active": True,
            "groups": ["ops"],
        }
        with patch(
            "app.api.service_accounts.router.generate_client_cert",
            return_value={"success": True},
        ):
            resp = client.post(
                "/api/service-accounts",
                json={
                    "name": "mybot",
                    "display_name": "My Bot",
                    "mode": "data",
                    "groups": ["ops"],
                },
            )
        assert resp.status_code == 201
        mock_ak.create_user.assert_called_once()
        assert mock_ak.create_user.call_args[1]["username"] == "svc_mybot"

    def test_no_double_prefix(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 13,
            "username": "svc_already",
            "name": "Already Prefixed",
            "is_active": True,
            "groups": ["ops"],
        }
        with patch(
            "app.api.service_accounts.router.generate_client_cert",
            return_value={"success": True},
        ):
            resp = client.post(
                "/api/service-accounts",
                json={
                    "name": "svc_already",
                    "display_name": "Already Prefixed",
                    "mode": "data",
                    "groups": ["ops"],
                },
            )
        assert resp.status_code == 201
        # Should NOT be svc_svc_already
        assert mock_ak.create_user.call_args[1]["username"] == "svc_already"

    def test_default_validity_data(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 14,
            "username": "svc_d",
            "name": "D",
            "is_active": True,
            "groups": ["g"],
        }
        with patch(
            "app.api.service_accounts.router.generate_client_cert",
            return_value={"success": True},
        ) as mock_gen:
            client.post(
                "/api/service-accounts",
                json={
                    "name": "d",
                    "display_name": "D",
                    "mode": "data",
                    "groups": ["g"],
                },
            )
        mock_gen.assert_called_once_with("svc_d", 365)

    def test_default_validity_admin(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 15,
            "username": "svc_a",
            "name": "A",
            "is_active": True,
            "groups": [],
        }
        with (
            patch(
                "app.api.service_accounts.router.generate_client_cert",
                return_value={"success": True},
            ) as mock_gen,
            patch(
                "app.api.service_accounts.router.register_admin_cert",
                return_value={"success": True, "output": "OK"},
            ),
        ):
            client.post(
                "/api/service-accounts",
                json={"name": "a", "display_name": "A", "mode": "admin"},
            )
        mock_gen.assert_called_once_with("svc_a", 730)

    def test_cert_failure_triggers_rollback(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 20,
            "username": "svc_fail",
            "name": "Fail",
            "is_active": True,
            "groups": ["g"],
        }
        with patch(
            "app.api.service_accounts.router.generate_client_cert",
            return_value={"success": False, "error": "container not found"},
        ):
            resp = client.post(
                "/api/service-accounts",
                json={
                    "name": "fail",
                    "display_name": "Fail",
                    "mode": "data",
                    "groups": ["g"],
                },
            )
        assert resp.status_code == 500
        assert "container not found" in resp.json()["detail"]
        mock_ak.deactivate_user.assert_called_once_with(20)


# ── List ─────────────────────────────────────────────────────────


class TestListServiceAccounts:
    def test_list_returns_svc_accounts(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_users.return_value = [
            {"id": 1, "username": "svc_bot1", "name": "Bot 1", "is_active": True, "groups": []},
        ]
        resp = client.get("/api/service-accounts")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1
        mock_ak.list_users.assert_called_once_with(search="svc_")


# ── Get ──────────────────────────────────────────────────────────


class TestGetServiceAccount:
    def test_get_returns_account_with_certs(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Bot 1",
            "is_active": True,
            "groups": [],
        }
        mock_tak.list_user_certs.return_value = [{"id": 42}]
        resp = client.get("/api/service-accounts/1")
        assert resp.status_code == 200
        assert "certs" in resp.json()

    def test_404_for_missing_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.get("/api/service-accounts/999")
        assert resp.status_code == 404

    def test_404_for_non_svc_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "regular_user",
            "name": "Regular",
            "is_active": True,
            "groups": [],
        }
        resp = client.get("/api/service-accounts/1")
        assert resp.status_code == 404


# ── Delete ───────────────────────────────────────────────────────


class TestDeleteServiceAccount:
    def test_deactivates_and_revokes(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Bot 1",
            "is_active": True,
            "groups": [],
        }
        mock_tak.revoke_all_user_certs.return_value = True
        resp = client.delete("/api/service-accounts/1")
        assert resp.status_code == 200
        mock_ak.deactivate_user.assert_called_once_with(1)
        mock_tak.revoke_all_user_certs.assert_called_once_with("svc_bot1")
        mock_ak.mark_certs_revoked.assert_called_once_with(1)

    @pytest.mark.parametrize("unregistered", [True, False])
    def test_admin_account_loses_its_admin_role(self, mock_clients, unregistered):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_ops",
            "fastak_user_type": "svc_admin",
        }
        mock_tak.revoke_all_user_certs.return_value = True
        with patch(
            "app.api.service_accounts.router.unregister_admin_cert",
            return_value={"success": unregistered},
        ) as unregister:
            resp = client.delete("/api/service-accounts/1")
        unregister.assert_called_once_with("svc_ops")
        assert resp.json()["certs_revoked"] is unregistered

    def test_admin_role_goes_even_when_the_credential_cut_fails(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_ops",
            "fastak_user_type": "svc_admin",
        }
        mock_ak.deactivate_user.side_effect = RuntimeError("lldap down")
        mock_tak.revoke_all_user_certs.return_value = True
        with patch(
            "app.api.service_accounts.router.unregister_admin_cert",
            return_value={"success": True},
        ) as unregister:
            assert client.delete("/api/service-accounts/1").status_code == 502
        unregister.assert_called_once_with("svc_ops")

    def test_refuses_the_monitors_own_account(self, mock_clients):
        """Revoking this cert would cut the monitor off from TAK Server."""
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "svc_fasttakapi"}
        resp = client.delete("/api/service-accounts/1")
        assert resp.status_code == 409
        mock_ak.deactivate_user.assert_not_called()
        mock_tak.revoke_all_user_certs.assert_not_called()

    def test_failed_cut_still_removes_nodered_pems(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {"id": 1, "username": "svc_bot1"}
        mock_ak.deactivate_user.side_effect = RuntimeError("lldap down")
        mock_tak.revoke_all_user_certs.return_value = True
        with patch("app.api.service_accounts.router.remove_nodered_pems") as remove:
            assert client.delete("/api/service-accounts/1").status_code == 502
        remove.assert_called_once_with("svc_bot1")

    def test_404_for_missing(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.delete("/api/service-accounts/999")
        assert resp.status_code == 404

    def test_404_for_non_svc_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "regularuser",
            "name": "Regular",
            "is_active": True,
            "groups": [],
        }
        resp = client.delete("/api/service-accounts/1")
        assert resp.status_code == 404
        mock_ak.deactivate_user.assert_not_called()


# ── Update ──────────────────────────────────────────────────────


class TestUpdateServiceAccount:
    def test_update_display_name(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Old Name",
            "is_active": True,
            "groups": [],
        }
        resp = client.patch("/api/service-accounts/1", json={"display_name": "New Name"})
        assert resp.status_code == 200
        mock_ak.update_user.assert_called_once_with(1, name="New Name")

    def test_update_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Bot",
            "is_active": True,
            "groups": [],
        }
        resp = client.patch("/api/service-accounts/1", json={"groups": ["field_ops"]})
        assert resp.status_code == 200
        mock_ak.set_user_groups.assert_called_once_with(1, ["field_ops"])

    def test_rejects_nonexistent_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Bot",
            "is_active": True,
            "groups": [],
        }
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "field_ops"}]
        resp = client.patch("/api/service-accounts/1", json={"groups": ["NOPE"]})
        assert resp.status_code == 400
        assert "do not exist" in resp.json()["detail"].lower()
        # Should NOT have updated anything
        mock_ak.update_user.assert_not_called()
        mock_ak.set_user_groups.assert_not_called()

    def test_404_for_missing_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.patch("/api/service-accounts/999", json={"display_name": "X"})
        assert resp.status_code == 404

    def test_404_for_non_svc_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "regularuser",
            "name": "Regular",
            "is_active": True,
            "groups": [],
        }
        resp = client.patch("/api/service-accounts/1", json={"display_name": "X"})
        assert resp.status_code == 404

    def test_admin_mode_rejects_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_admin_bot",
            "name": "Admin Bot",
            "is_active": True,
            "groups": [],
            "fastak_user_type": "svc_admin",
        }
        resp = client.patch("/api/service-accounts/1", json={"groups": ["field_ops"]})
        assert resp.status_code == 400
        assert "admin" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()

    def test_data_mode_rejects_empty_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_sensor1",
            "name": "Sensor 1",
            "is_active": True,
            "groups": ["field_ops"],
            "fastak_user_type": "svc_data",
        }
        resp = client.patch("/api/service-accounts/1", json={"groups": []})
        assert resp.status_code == 400
        assert "at least one group" in resp.json()["detail"].lower()
        mock_ak.set_user_groups.assert_not_called()

    def test_data_mode_allows_group_update(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_sensor1",
            "name": "Sensor 1",
            "is_active": True,
            "groups": ["field_ops"],
            "fastak_user_type": "svc_data",
        }
        resp = client.patch("/api/service-accounts/1", json={"groups": ["ops"]})
        assert resp.status_code == 200
        mock_ak.set_user_groups.assert_called_once_with(1, ["ops"])


# ── Download ─────────────────────────────────────────────────────


class TestDownloadCert:
    def test_download_returns_p12(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Bot 1",
            "is_active": True,
            "groups": [],
        }
        # Create a fake .p12 file
        p12_file = tmp_path / "svc_bot1.p12"
        p12_file.write_bytes(b"fake-p12-data")

        with patch(
            "app.api.service_accounts.router.CERT_FILES_PATH",
            tmp_path,
        ):
            resp = client.get("/api/service-accounts/1/certs/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-pkcs12"
        assert resp.content == b"fake-p12-data"

    def test_download_404_when_cert_missing(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "svc_bot1",
            "name": "Bot 1",
            "is_active": True,
            "groups": [],
        }
        with patch(
            "app.api.service_accounts.router.CERT_FILES_PATH",
            tmp_path,
        ):
            resp = client.get("/api/service-accounts/1/certs/download")
        assert resp.status_code == 404

    def test_download_404_for_missing_account(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.get("/api/service-accounts/999/certs/download")
        assert resp.status_code == 404

    def test_download_404_for_non_svc_user(self, mock_clients, tmp_path):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "regularuser",
            "name": "Regular",
            "is_active": True,
            "groups": [],
        }
        with patch("app.api.service_accounts.router.CERT_FILES_PATH", tmp_path):
            resp = client.get("/api/service-accounts/1/certs/download")
        assert resp.status_code == 404


class TestNameCollisions:
    def test_creating_an_existing_account_is_a_conflict_and_writes_no_cert(self, mock_clients):
        """Carrying on would regenerate the existing account's key, e.g. the
        monitor's own svc_fasttakapi."""
        from app.api.users.identity import UserExistsError

        mock_ak, _ = mock_clients
        mock_ak.create_user.side_effect = UserExistsError("svc_fasttakapi")
        with patch("app.api.service_accounts.router.generate_client_cert") as generate:
            resp = client.post(
                "/api/service-accounts",
                json={"name": "fasttakapi", "display_name": "x", "mode": "admin"},
            )
        assert resp.status_code == 409
        generate.assert_not_called()
