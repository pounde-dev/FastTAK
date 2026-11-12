"""Group management API integration tests."""

import pytest

pytestmark = pytest.mark.integration


class TestGroupManagement:
    @pytest.mark.dependency(name="group_list")
    def test_list_groups(self, api):
        status, data = api("GET", "/api/groups")
        assert status == 200
        assert isinstance(data, list)

    @pytest.mark.dependency(name="group_create")
    def test_create_group(self, api, test_group_name, created_resources):
        status, data = api("POST", "/api/groups", {"name": test_group_name})
        assert status == 201
        assert data["name"] == test_group_name
        created_resources["test_group_id"] = data["id"]

    @pytest.mark.dependency(name="group_in_list", depends=["group_create"])
    def test_group_appears_in_list(self, api, test_group_name):
        status, data = api("GET", "/api/groups")
        assert status == 200
        names = [g["name"] for g in data]
        assert test_group_name in names

    @pytest.mark.dependency(name="group_delete", depends=["group_in_list"])
    def test_delete_group(self, api, created_resources):
        gid = created_resources["test_group_id"]
        status, data = api("DELETE", f"/api/groups/{gid}")
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(depends=["group_delete"])
    def test_deleted_group_gone(self, api, test_group_name):
        status, data = api("GET", "/api/groups")
        assert status == 200
        names = [g["name"] for g in data]
        assert test_group_name not in names

    @pytest.mark.dependency(depends=["group_delete"])
    def test_deleted_group_is_404_not_a_server_error(self, api, created_resources):
        """LLDAP answers a missing group with an error; the monitor must turn
        that into the 404 these routes document. The session cleanup deletes
        this group a second time and relies on it."""
        gid = created_resources["test_group_id"]
        assert api("GET", f"/api/groups/{gid}")[0] == 404
        assert api("DELETE", f"/api/groups/{gid}")[0] == 404
