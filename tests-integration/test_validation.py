"""Validation and error case integration tests.

Covers invalid input (422), bad references (400), duplicates (409),
not-found (404), and removed endpoints (404/405).
"""

import pytest

pytestmark = pytest.mark.integration


class TestValidation:
    def test_data_mode_without_groups_422(self, api):
        status, _data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": "val_nodata",
                "display_name": "No Groups",
                "mode": "data",
            },
        )
        assert status == 422

    def test_data_mode_bad_group_400(self, api):
        status, _data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": "val_nogrp",
                "display_name": "Bad Group",
                "mode": "data",
                "groups": ["NONEXISTENT_GROUP_XYZ"],
            },
        )
        assert status == 400

    def test_admin_mode_with_groups_422(self, api, svc_test_group_name):
        status, _data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": "val_admingrp",
                "display_name": "Admin With Groups",
                "mode": "admin",
                "groups": [svc_test_group_name],
            },
        )
        assert status == 422

    def test_duplicate_cert_409(self, api, webadmin_id, test_dup_cert_name):
        # Generate first cert
        api(
            "POST",
            f"/api/users/{webadmin_id}/certs/generate",
            {"name": test_dup_cert_name},
        )
        # Attempt duplicate
        status, _data = api(
            "POST",
            f"/api/users/{webadmin_id}/certs/generate",
            {"name": test_dup_cert_name},
        )
        assert status == 409
        # Cleanup: revoke the cert
        api(
            "POST",
            f"/api/users/{webadmin_id}/certs/revoke",
            {"cert_name": test_dup_cert_name},
        )

    def test_nonexistent_account_404(self, api):
        status, _data = api("GET", "/api/service-accounts/999999/certs/download")
        assert status == 404

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/ops/certs/create-client/test"),
            ("POST", "/api/ops/certs/create-server/test"),
            ("GET", "/api/ops/certs/list"),
            ("POST", "/api/ops/certs/revoke"),
        ],
    )
    def test_removed_endpoint(self, api, method, path):
        status, _data = api(method, path)
        assert status in (404, 405), f"{method} {path} should return 404/405, got {status}"


class TestNameCollisions:
    def test_creating_an_existing_user_is_a_conflict(self, api, user_group, run_id):
        body = {"username": f"tstdup_{run_id}", "name": "Dup", "groups": [user_group]}
        status, created = api("POST", "/api/users", body)
        assert status == 201, created
        try:
            status, data = api("POST", "/api/users", body)
            assert status == 409, data
        finally:
            api("DELETE", f"/api/users/{created['id']}")

    def test_the_monitors_own_service_account_cannot_be_recreated(self, api):
        status, data = api(
            "POST",
            "/api/service-accounts",
            {"name": "fasttakapi", "display_name": "x", "mode": "admin"},
        )
        assert status == 409, data
