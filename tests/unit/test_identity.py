"""Tests for LLDAP/Proxy identity client."""

import time
from unittest.mock import MagicMock, patch

import pytest
from app.api.users.identity import IdentityClient, _username_to_numeric_id


@pytest.fixture
def client():
    return IdentityClient(
        lldap_url="http://lldap-test:17170",
        proxy_url="http://ldap-proxy-test:8081",
        admin_password="test-password",
        hidden_prefixes=["ak-", "adm_", "svc_", "ma-"],
    )


@pytest.fixture
def mock_http(client):
    """Patch the client's internal HTTP methods for clean testing."""
    with (
        patch.object(client, "_graphql") as mock_graphql,
        patch.object(client, "_proxy_request") as mock_proxy,
    ):
        yield {
            "graphql": mock_graphql,
            "proxy": mock_proxy,
        }


class TestIsHidden:
    def test_hidden_user(self, client):
        assert client.is_hidden("svc_fasttakapi") is True

    def test_visible_user(self, client):
        assert client.is_hidden("jsmith") is False

    def test_case_insensitive(self, client):
        assert client.is_hidden("AK-admin") is True

    def test_adm_prefix(self, client):
        assert client.is_hidden("adm_ldapservice") is True

    def test_ma_prefix(self, client):
        assert client.is_hidden("ma-user") is True


class TestListUsers:
    def test_filters_hidden_prefixes(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [{"id": 1, "displayName": "tak_ROLE_ADMIN"}],
                },
                {
                    "id": "svc_fasttakapi",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Service",
                    "attributes": [],
                    "groups": [],
                },
                {
                    "id": "ak-admin",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Admin",
                    "attributes": [],
                    "groups": [],
                },
            ],
        }
        users = client.list_users()
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"

    def test_strips_tak_prefix_from_groups(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [
                        {"id": 1, "displayName": "tak_ROLE_ADMIN"},
                        {"id": 2, "displayName": "tak_Team Alpha"},
                        {"id": 3, "displayName": "lldap_admin"},  # non-tak, excluded
                    ],
                },
            ],
        }
        users = client.list_users()
        assert users[0]["groups"] == ["ROLE_ADMIN", "Team Alpha"]

    def test_search_filters_client_side(self, client, mock_http):
        # Search is done client-side (LLDAP only supports exact-match filters)
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "displayName": "John Smith",
                    "creationDate": "2026-01-01",
                    "attributes": [],
                    "groups": [],
                },
                {
                    "id": "bjones",
                    "displayName": "Bob Jones",
                    "creationDate": "2026-01-01",
                    "attributes": [],
                    "groups": [],
                },
            ],
        }
        users = client.list_users(search="john")
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"

    def test_returns_numeric_id(self, client, mock_http):
        """LLDAP users have both a string username (id) and a numeric ID.
        The _format_user must extract the numeric creationDate-based ID or
        use the LLDAP numeric UUID. For now we map via _user_id_map."""
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [],
                    "uuid": "abc-123",
                },
            ],
        }
        users = client.list_users()
        # ID should be a stable integer
        assert isinstance(users[0]["id"], int)


class TestGetUser:
    def test_returns_user(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "user": {
                "id": "jsmith",
                "creationDate": "2024-01-01T00:00:00Z",
                "displayName": "John",
                "attributes": [
                    {"name": "fastak_expires", "value": ["9999999999"]},
                    {"name": "fastak_certs_revoked", "value": ["false"]},
                ],
                "groups": [{"id": 1, "displayName": "tak_ROLE_ADMIN"}],
            },
        }
        # We need the user_id_map to resolve numeric ID -> username
        client._user_id_map = {1: "jsmith"}
        user = client.get_user(1)
        assert user["username"] == "jsmith"
        assert user["fastak_expires"] == 9999999999
        assert user["groups"] == ["ROLE_ADMIN"]

    def test_returns_none_for_hidden_user(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "user": {
                "id": "svc_fasttakapi",
                "creationDate": "2024-01-01T00:00:00Z",
                "displayName": "Service",
                "attributes": [],
                "groups": [],
            },
        }
        client._user_id_map = {2: "svc_fasttakapi"}
        assert client.get_user(2) is None

    def test_returns_none_for_missing_user(self, client, mock_http):
        mock_http["graphql"].return_value = {"user": None}
        client._user_id_map = {}
        assert client.get_user(999) is None


class TestCreateUser:
    def test_creates_passwordless_user(self, client, mock_http):
        mock_http["graphql"].side_effect = [
            # createUser mutation
            {"createUser": {"id": "newuser", "creationDate": "2024-01-01T00:00:00Z"}},
            # updateUser mutation (set attributes)
            {"updateUser": {"ok": True}},
            # list users to get numeric ID
            {
                "users": [
                    {
                        "id": "newuser",
                        "creationDate": "2024-01-01T00:00:00Z",
                        "displayName": "New User",
                        "attributes": [{"name": "fastak_certs_revoked", "value": ["false"]}],
                        "groups": [],
                    }
                ]
            },
        ]
        client.create_user("newuser", "New User")
        # First graphql call should be the createUser mutation
        create_call = mock_http["graphql"].call_args_list[0]
        query = create_call[0][0]
        assert "createUser" in query
        # No password in the create payload
        variables = create_call[0][1] if len(create_call[0]) > 1 else {}
        user_input = variables.get("user", {})
        assert "password" not in user_input

    def test_creates_user_with_ttl(self, client, mock_http):
        mock_http["graphql"].side_effect = [
            {"createUser": {"id": "temp", "creationDate": "2024-01-01T00:00:00Z"}},
            {"updateUser": {"ok": True}},
            {
                "users": [
                    {
                        "id": "temp",
                        "creationDate": "2024-01-01T00:00:00Z",
                        "displayName": "Temp",
                        "attributes": [
                            {
                                "name": "fastak_expires",
                                "value": [str(int(time.time() + 168 * 3600))],
                            },
                            {"name": "fastak_certs_revoked", "value": ["false"]},
                        ],
                        "groups": [],
                    }
                ]
            },
        ]
        client.create_user("temp", "Temp", ttl_hours=168)
        # Second call is updateUser with attributes
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        attr_names = [a["name"] for a in attrs]
        assert "fastak_expires" in attr_names
        assert "fastak_certs_revoked" in attr_names

    def test_creates_user_without_ttl_sets_revoked_false(self, client, mock_http):
        mock_http["graphql"].side_effect = [
            {"createUser": {"id": "perm", "creationDate": "2024-01-01T00:00:00Z"}},
            {"updateUser": {"ok": True}},
            {
                "users": [
                    {
                        "id": "perm",
                        "creationDate": "2024-01-01T00:00:00Z",
                        "displayName": "Perm",
                        "attributes": [{"name": "fastak_certs_revoked", "value": ["false"]}],
                        "groups": [],
                    }
                ]
            },
        ]
        client.create_user("perm", "Perm")
        # Second call sets attributes with fastak_certs_revoked = false
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        revoked_attr = next((a for a in attrs if a["name"] == "fastak_certs_revoked"), None)
        assert revoked_attr is not None
        assert revoked_attr["value"] == ["false"]


class TestDeactivateUser:
    def test_only_sets_inactive(self, client, mock_http):
        """deactivate_user should NOT set fastak_certs_revoked.
        The flag is managed by the caller after confirming cert revocation."""
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].return_value = {"updateUser": {"ok": True}}
        client.deactivate_user(1)
        call_args = mock_http["graphql"].call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else {}
        # Should NOT contain fastak_certs_revoked in the mutation
        assert "fastak_certs_revoked" not in str(variables)


class TestUpdateUser:
    def test_reactivation_resets_certs_revoked(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            # First: get user for existing attributes
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_certs_revoked", "value": ["true"]},
                    ],
                    "groups": [],
                },
            },
            # Second: updateUser mutation
            {"updateUser": {"ok": True}},
            # Third: get_user for return value (via _resolve_username -> get user)
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            },
        ]
        client.update_user(1, is_active=True)
        # The update mutation should include fastak_certs_revoked = false
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        revoked_attr = next((a for a in attrs if a["name"] == "fastak_certs_revoked"), None)
        assert revoked_attr is not None
        assert revoked_attr["value"] == ["false"]

    def test_clear_ttl_clears_both_attrs(self, client, mock_http):
        """Sending ttl_hours=None should clear fastak_expires AND fastak_certs_revoked."""
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            # First: get user for existing attributes
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_expires", "value": ["999"]},
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            },
            # Second: updateUser mutation
            {"updateUser": {"ok": True}},
            # Third: get_user for return
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [],
                },
            },
        ]
        client.update_user(1, ttl_hours=None)
        # The update mutation should clear both attributes
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        # Both should be present but with empty values (LLDAP clears by setting to [])
        expires_attr = next((a for a in attrs if a["name"] == "fastak_expires"), None)
        revoked_attr = next((a for a in attrs if a["name"] == "fastak_certs_revoked"), None)
        assert expires_attr is not None
        assert expires_attr["value"] == []
        assert revoked_attr is not None
        assert revoked_attr["value"] == []


class TestEnrollmentTokens:
    def test_get_or_create_creates_when_none_exist(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["proxy"].side_effect = [
            # GET tokens — none active
            MagicMock(status_code=200, json=lambda: {"tokens": []}),
            # POST create token
            MagicMock(
                status_code=201,
                json=lambda: {
                    "token": "secret-token-value",
                    "expires_at": 9999999999,
                    "one_time": True,
                    "username": "jsmith",
                },
            ),
        ]
        key, expires = client.get_or_create_enrollment_token(1, ttl_minutes=15)
        assert key == "secret-token-value"

    def test_get_or_create_returns_existing(self, client, mock_http):
        """If an active token exists, return it without creating a new one."""
        client._user_id_map = {1: "jsmith"}
        mock_http["proxy"].return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokens": [
                    {
                        "id": 1,
                        "token": "existing-token",
                        "expires_at": 9999999999,
                        "one_time": True,
                        "created_at": 1000,
                    }
                ]
            },
        )
        key, expires = client.get_or_create_enrollment_token(1, ttl_minutes=15)
        assert key == "existing-token"

    def test_delete_enrollment_tokens(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].return_value = {
            "user": {
                "id": "jsmith",
                "creationDate": "2024-01-01T00:00:00Z",
                "displayName": "John",
                "attributes": [],
                "groups": [],
            },
        }
        mock_http["proxy"].return_value = MagicMock(status_code=200, json=lambda: {"deleted": 3})
        count = client.delete_enrollment_tokens(1)
        assert count == 3


class TestSetPassword:
    def test_calls_lldap_set_password_binary(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        client._jwt = "fake-jwt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            client.set_password(1, "new-pass-123")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "/usr/local/bin/lldap_set_password"
            assert "--username" in cmd
            assert cmd[cmd.index("--username") + 1] == "jsmith"
            assert "--password" in cmd
            assert cmd[cmd.index("--password") + 1] == "new-pass-123"

    def test_raises_on_binary_failure(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        client._jwt = "fake-jwt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="connection refused")
            with pytest.raises(RuntimeError, match="lldap_set_password failed"):
                client.set_password(1, "pass")

    def test_raises_for_unknown_user(self, client, mock_http):
        client._user_id_map = {}
        mock_http["graphql"].return_value = {"users": []}
        with pytest.raises(ValueError, match="not found"):
            client.set_password(999, "pass")


class TestMarkCertsRevoked:
    def test_sets_revoked_true_preserving_other_attrs(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            # get_user for existing attributes
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_expires", "value": ["1000"]},
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            },
            # updateUser mutation
            {"updateUser": {"ok": True}},
        ]
        client.mark_certs_revoked(1)
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        revoked = next(a for a in attrs if a["name"] == "fastak_certs_revoked")
        assert revoked["value"] == ["true"]
        # fastak_expires should still be present
        expires = next(a for a in attrs if a["name"] == "fastak_expires")
        assert expires["value"] == ["1000"]

    def test_raises_for_unknown_user(self, client, mock_http):
        client._user_id_map = {}
        mock_http["graphql"].return_value = {"users": []}
        with pytest.raises(ValueError, match="not found"):
            client.mark_certs_revoked(999)


class TestListGroups:
    def test_returns_tak_groups_without_prefix(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "groups": [
                {"id": 1, "displayName": "tak_FDNY Robotics"},
                {"id": 2, "displayName": "tak_Team Alpha"},
                {"id": 3, "displayName": "lldap_admin"},  # non-tak, excluded
            ],
        }
        groups = client.list_groups()
        assert len(groups) == 2
        names = [g["name"] for g in groups]
        assert "FDNY Robotics" in names
        assert "Team Alpha" in names

    def test_excludes_tak_role_admin(self, client, mock_http):
        """tak_ROLE_ADMIN is a system group, not shown in user-facing group list."""
        mock_http["graphql"].return_value = {
            "groups": [
                {"id": 1, "displayName": "tak_ROLE_ADMIN"},
                {"id": 2, "displayName": "tak_FDNY Robotics"},
            ],
        }
        groups = client.list_groups()
        assert len(groups) == 1
        assert groups[0]["name"] == "FDNY Robotics"

    def test_empty_when_no_tak_groups(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "groups": [
                {"id": 1, "displayName": "lldap_admin"},
                {"id": 2, "displayName": "lldap_password_manager"},
            ],
        }
        assert client.list_groups() == []


class TestGetGroup:
    def test_returns_group_with_members(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "group": {
                "id": 5,
                "displayName": "tak_FDNY Robotics",
                "users": [
                    {"id": "jsmith", "displayName": "John"},
                    {"id": "bjones", "displayName": "Bob"},
                ],
            },
        }
        group = client.get_group("5")
        assert group["name"] == "FDNY Robotics"
        assert len(group["members"]) == 2
        usernames = [m["username"] for m in group["members"]]
        assert "jsmith" in usernames
        assert "bjones" in usernames

    def test_filters_hidden_members(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "group": {
                "id": 5,
                "displayName": "tak_FDNY Robotics",
                "users": [
                    {"id": "jsmith", "displayName": "John"},
                    {"id": "svc_fasttakapi", "displayName": "Service"},
                ],
            },
        }
        group = client.get_group("5")
        assert len(group["members"]) == 1
        assert group["members"][0]["username"] == "jsmith"

    def test_returns_none_for_non_tak_group(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "group": {
                "id": 1,
                "displayName": "lldap_admin",
                "users": [],
            },
        }
        assert client.get_group("1") is None

    def test_returns_none_for_invalid_id(self, client, mock_http):
        assert client.get_group("not-a-number") is None

    def test_returns_none_for_missing_group(self, client, mock_http):
        mock_http["graphql"].return_value = {"group": None}
        assert client.get_group("999") is None

    def test_returns_none_when_lldap_reports_entity_not_found(self, client, mock_http):
        """What LLDAP really sends for a missing group: an error, not null."""
        mock_http["graphql"].side_effect = RuntimeError(
            "GraphQL error: [{'message': 'Entity not found: `8`', 'path': ['group']}]"
        )
        assert client.get_group("8") is None

    def test_other_lldap_errors_still_raise(self, client, mock_http):
        mock_http["graphql"].side_effect = RuntimeError("GraphQL error: [{'message': 'boom'}]")
        with pytest.raises(RuntimeError, match="boom"):
            client.get_group("8")


class TestCreateGroup:
    def test_prepends_tak_prefix(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "createGroup": {"id": 10, "displayName": "tak_FDNY Robotics"},
        }
        group = client.create_group("FDNY Robotics")
        assert group["name"] == "FDNY Robotics"
        assert group["id"] == 10
        # Verify the mutation sent tak_-prefixed name
        call_args = mock_http["graphql"].call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else {}
        assert variables["name"] == "tak_FDNY Robotics"

    def test_does_not_double_prefix(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "createGroup": {"id": 10, "displayName": "tak_Team Alpha"},
        }
        client.create_group("tak_Team Alpha")
        call_args = mock_http["graphql"].call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else {}
        assert variables["name"] == "tak_Team Alpha"


class TestDeleteGroup:
    def test_deletes_by_int_id(self, client, mock_http):
        mock_http["graphql"].return_value = {"deleteGroup": {"ok": True}}
        client.delete_group("5")
        call_args = mock_http["graphql"].call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else {}
        assert variables["id"] == 5

    def test_noop_for_invalid_id(self, client, mock_http):
        client.delete_group("not-a-number")
        mock_http["graphql"].assert_not_called()


class TestSetUserGroups:
    def test_adds_missing_and_removes_extra_tak_groups(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            # list all groups
            {
                "groups": [
                    {"id": 10, "displayName": "tak_FDNY Robotics"},
                    {"id": 11, "displayName": "tak_Team Alpha"},
                    {"id": 12, "displayName": "tak_Team Bravo"},
                ],
            },
            # get user's current groups
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [
                        {"id": 10, "displayName": "tak_FDNY Robotics"},
                        {"id": 11, "displayName": "tak_Team Alpha"},
                        {"id": 99, "displayName": "lldap_admin"},  # non-tak, untouched
                    ],
                },
            },
            # removeUserFromGroup (tak_Team Alpha)
            {"removeUserFromGroup": {"ok": True}},
            # addUserToGroup (tak_Team Bravo)
            {"addUserToGroup": {"ok": True}},
        ]
        # Keep FDNY Robotics, drop Team Alpha, add Team Bravo
        client.set_user_groups(1, ["FDNY Robotics", "Team Bravo"])

        calls = mock_http["graphql"].call_args_list
        # Should have 4 calls: list groups, get user, remove, add
        assert len(calls) == 4
        # The remove call should be for Team Alpha (id=11)
        remove_vars = calls[2][0][1] if len(calls[2][0]) > 1 else {}
        assert remove_vars["groupId"] == 11
        # The add call should be for Team Bravo (id=12)
        add_vars = calls[3][0][1] if len(calls[3][0]) > 1 else {}
        assert add_vars["groupId"] == 12

    def test_preserves_non_tak_groups(self, client, mock_http):
        """Non-tak groups (like lldap_admin) should not be removed."""
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            {"groups": [{"id": 10, "displayName": "tak_FDNY Robotics"}]},
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [
                        {"id": 99, "displayName": "lldap_admin"},
                    ],
                },
            },
            # addUserToGroup for FDNY Robotics
            {"addUserToGroup": {"ok": True}},
        ]
        client.set_user_groups(1, ["FDNY Robotics"])
        calls = mock_http["graphql"].call_args_list
        # No removeUserFromGroup call — lldap_admin is not tak_
        for call in calls:
            query = call[0][0]
            assert "removeUserFromGroup" not in query

    def test_raises_for_unknown_user(self, client, mock_http):
        client._user_id_map = {}
        mock_http["graphql"].return_value = {"users": []}
        with pytest.raises(ValueError, match="not found"):
            client.set_user_groups(999, ["FDNY Robotics"])


class TestGetUsersPendingExpiry:
    def test_returns_expired_non_revoked_users(self, client, mock_http):
        past = int(time.time()) - 3600
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "expired_user",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Expired",
                    "attributes": [
                        {"name": "fastak_expires", "value": [str(past)]},
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            ],
        }
        pending = client.get_users_pending_expiry()
        assert len(pending) == 1
        assert pending[0]["username"] == "expired_user"

    def test_excludes_already_revoked(self, client, mock_http):
        past = int(time.time()) - 3600
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "revoked_user",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Revoked",
                    "attributes": [
                        {"name": "fastak_expires", "value": [str(past)]},
                        {"name": "fastak_certs_revoked", "value": ["true"]},
                    ],
                    "groups": [],
                },
            ],
        }
        assert client.get_users_pending_expiry() == []

    def test_excludes_not_yet_expired(self, client, mock_http):
        future = int(time.time()) + 86400
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "future_user",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Future",
                    "attributes": [
                        {"name": "fastak_expires", "value": [str(future)]},
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            ],
        }
        assert client.get_users_pending_expiry() == []

    def test_excludes_users_without_ttl(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "permanent_user",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Permanent",
                    "attributes": [],
                    "groups": [],
                },
            ],
        }
        assert client.get_users_pending_expiry() == []


class TestFormatUser:
    def test_includes_fastak_user_type(self, client):
        result = client._format_user(
            {
                "id": "jsmith",
                "displayName": "John",
                "attributes": [{"name": "fastak_user_type", "value": ["user"]}],
                "groups": [{"id": 1, "displayName": "tak_ops"}],
            }
        )
        assert result["fastak_user_type"] == "user"

    def test_omits_fastak_user_type_when_absent(self, client):
        result = client._format_user(
            {
                "id": "jsmith",
                "displayName": "John",
                "attributes": [],
                "groups": [],
            }
        )
        assert "fastak_user_type" not in result

    def test_parses_svc_admin_type(self, client):
        result = client._format_user(
            {
                "id": "svc_fasttakapi",
                "displayName": "FastTAK API",
                "attributes": [{"name": "fastak_user_type", "value": ["svc_admin"]}],
                "groups": [],
            }
        )
        assert result["fastak_user_type"] == "svc_admin"


class TestUsernameToNumericId:
    def test_deterministic(self):
        assert _username_to_numeric_id("jsmith") == _username_to_numeric_id("jsmith")

    def test_different_usernames_differ(self):
        assert _username_to_numeric_id("jsmith") != _username_to_numeric_id("bjones")

    def test_returns_positive_int(self):
        nid = _username_to_numeric_id("jsmith")
        assert isinstance(nid, int)
        assert nid > 0

    def test_fits_in_53_bits(self):
        """Must stay within JS Number.MAX_SAFE_INTEGER (2^53 - 1)."""
        nid = _username_to_numeric_id("jsmith")
        assert nid <= 0x1FFFFFFFFFFFFF


class TestProxyAuth:
    """The ldap-proxy /tokens API is authenticated with a shared bearer secret
    (issue #54); _proxy_request must attach it when configured."""

    def _client(self, secret):
        return IdentityClient(
            lldap_url="http://lldap-test:17170",
            proxy_url="http://ldap-proxy-test:8081",
            admin_password="test-password",
            hidden_prefixes=[],
            proxy_secret=secret,
        )

    def test_sends_bearer_header_when_configured(self):
        client = self._client("s3cret")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch.object(client._client, "request", return_value=resp) as mock_req:
            client._proxy_request("GET", "/tokens/jsmith")
        assert mock_req.call_args.kwargs["headers"]["Authorization"] == "Bearer s3cret"

    def test_no_header_when_secret_unset(self):
        client = self._client("")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch.object(client._client, "request", return_value=resp) as mock_req:
            client._proxy_request("GET", "/tokens/jsmith")
        assert "headers" not in mock_req.call_args.kwargs
