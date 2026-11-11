"""Service account lifecycle (admin mode) integration tests."""

import pytest

pytestmark = pytest.mark.integration


class TestServiceAccountAdmin:
    @pytest.mark.dependency(name="svc_admin_create")
    def test_create_admin_account(self, api, svc_admin_name, run_id, created_resources):
        status, data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": svc_admin_name,
                "display_name": f"Admin {run_id}",
                "mode": "admin",
            },
        )
        assert status == 201
        assert data["username"] == f"svc_{svc_admin_name}"
        assert data["mode"] == "admin"
        created_resources["svc_admin_id"] = data["id"]

    @pytest.mark.dependency(depends=["svc_admin_create"])
    def test_download_cert(self, api, created_resources):
        sid = created_resources["svc_admin_id"]
        status, _data = api("GET", f"/api/service-accounts/{sid}/certs/download")
        assert status == 200

    @pytest.mark.dependency(depends=["svc_admin_create"])
    def test_deactivate(self, api, created_resources):
        sid = created_resources["svc_admin_id"]
        status, data = api("DELETE", f"/api/service-accounts/{sid}")
        assert status == 200
        assert data.get("success") is True
        assert data.get("certs_revoked") is True, "the dashboard reports this to the admin"

    @pytest.mark.dependency(depends=["svc_admin_create"])
    def test_cert_is_on_the_crl(self, api, compose_exec, created_resources):
        sid = created_resources["svc_admin_id"]
        _, account = api("GET", f"/api/service-accounts/{sid}")
        pem = f"/opt/tak/certs/files/{account['username']}.pem"
        serial = compose_exec(
            "tak-server", ["openssl", "x509", "-in", pem, "-noout", "-serial"]
        ).stdout.strip()
        crl = compose_exec(
            "tak-server",
            ["openssl", "crl", "-in", "/opt/tak/certs/files/ca.crl", "-noout", "-text"],
        ).stdout
        assert serial.removeprefix("serial=") in crl.replace(" ", "")
