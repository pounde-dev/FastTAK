"""Tests for init-identity LLDAP bootstrap functions.

These test the ensure_user() and ensure_group() functions that create
LLDAP users and groups for TAK Server x509groups resolution.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# bootstrap.py lives outside the normal package structure — add to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "init-identity"))


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Set required env vars so bootstrap module can import."""
    monkeypatch.setenv("LDAP_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("LLDAP_URL", "http://lldap:17170")


@pytest.fixture
def mock_graphql(monkeypatch):
    """Mock graphql() in the bootstrap module."""
    import bootstrap

    gql = MagicMock()
    monkeypatch.setattr(bootstrap, "graphql", gql)
    return gql


class TestEnsureUser:
    """Tests for ensure_user() — creates LLDAP users for cert CN matching."""

    def test_creates_user_when_not_found(self, mock_graphql):
        from bootstrap import ensure_user

        # First call: user query raises (not found)
        # Second call: createUser mutation succeeds
        mock_graphql.side_effect = [
            RuntimeError("GraphQL error: user not found"),
            {"createUser": {"id": "svc_fasttakapi", "displayName": "svc_fasttakapi"}},
        ]

        uid = ensure_user("http://lldap:17170", "fake-token", "svc_fasttakapi", "svc_fasttakapi")

        assert uid == "svc_fasttakapi"
        assert mock_graphql.call_count == 2

    def test_skips_creation_when_user_exists(self, mock_graphql):
        from bootstrap import ensure_user

        mock_graphql.return_value = {"user": {"id": "svc_nodered", "displayName": "svc_nodered"}}

        uid = ensure_user("http://lldap:17170", "fake-token", "svc_nodered", "svc_nodered")

        assert uid == "svc_nodered"
        # Only the query call, no create mutation
        assert mock_graphql.call_count == 1

    def test_returns_string_user_id(self, mock_graphql):
        """LLDAP user IDs are strings (the username), not integers."""
        from bootstrap import ensure_user

        mock_graphql.return_value = {"user": {"id": "webadmin", "displayName": "Web Admin"}}

        uid = ensure_user("http://lldap:17170", "fake-token", "webadmin", "Web Admin")

        assert isinstance(uid, str)
        assert uid == "webadmin"


class TestEnsureGroup:
    """Tests for ensure_group() — creates default groups like tak_ROLE_ADMIN."""

    def test_creates_group_when_not_found(self, mock_graphql):
        from bootstrap import ensure_group

        mock_graphql.side_effect = [
            {"groups": []},  # No existing groups
            {"createGroup": {"id": 1, "displayName": "tak_ROLE_ADMIN"}},
        ]

        gid = ensure_group("http://lldap:17170", "fake-token", "tak_ROLE_ADMIN")

        assert gid == 1
        assert mock_graphql.call_count == 2

    def test_returns_existing_group_id(self, mock_graphql):
        from bootstrap import ensure_group

        mock_graphql.return_value = {
            "groups": [
                {"id": 5, "displayName": "tak_ROLE_ADMIN"},
            ]
        }

        gid = ensure_group("http://lldap:17170", "fake-token", "tak_ROLE_ADMIN")

        assert gid == 5
        assert mock_graphql.call_count == 1


class TestAddToGroup:
    """Tests for add_to_group() — uses String userId and Int groupId."""

    def test_uses_correct_graphql_types(self, mock_graphql):
        """addUserToGroup(userId: String!, groupId: Int!) per LLDAP schema."""
        from bootstrap import add_to_group

        mock_graphql.return_value = {"addUserToGroup": {"ok": True}}

        add_to_group("http://lldap:17170", "fake-token", "webadmin", 1)

        call_args = mock_graphql.call_args
        variables = call_args[1].get("variables") or call_args[0][3]
        assert variables["userId"] == "webadmin"
        assert variables["groupId"] == 1
        assert isinstance(variables["userId"], str)
        assert isinstance(variables["groupId"], int)


class TestServiceAccountsHaveNoPassword:
    """Service accounts (svc_fasttakapi) are passwordless.

    They authenticate via client certs, not passwords. LDAP user exists
    only for x509groups group membership lookup.
    """

    def test_main_does_not_set_password_for_service_accounts(self, mock_graphql, monkeypatch):
        import bootstrap

        monkeypatch.setattr(bootstrap, "lldap_login", MagicMock(return_value="fake-token"))
        monkeypatch.setattr(bootstrap, "set_password", MagicMock())
        monkeypatch.setenv("TAK_WEBADMIN_PASSWORD", "")  # No webadmin

        # graphql call sequence: 4 attr schemas + 2 ensure_group (tak_ROLE_ADMIN
        # + monitor_admin) + ensure_user + set_user_attribute
        mock_graphql.side_effect = [
            {"addUserAttribute": {"ok": True}},  # fastak_expires schema
            {"addUserAttribute": {"ok": True}},  # fastak_certs_revoked schema
            {"addUserAttribute": {"ok": True}},  # is_active schema
            {"addUserAttribute": {"ok": True}},  # fastak_user_type schema
            {
                "groups": [
                    {"id": 1, "displayName": "tak_ROLE_ADMIN"},
                    {"id": 2, "displayName": "monitor_admin"},
                ]
            },  # ensure_group tak_ROLE_ADMIN
            {
                "groups": [
                    {"id": 1, "displayName": "tak_ROLE_ADMIN"},
                    {"id": 2, "displayName": "monitor_admin"},
                ]
            },  # ensure_group monitor_admin
            {
                "user": {"id": "svc_fasttakapi", "displayName": "svc_fasttakapi"}
            },  # ensure_user svc_fasttakapi
            {"updateUser": {"ok": True}},  # set_user_attribute svc_fasttakapi
        ]

        bootstrap.main()

        bootstrap.set_password.assert_not_called()


class TestGateGroupBootstrap:
    """Both monitor gate groups must be created and joined.

    ADMIN_GROUP (DD-047) gates every JSON API and dashboard page; the bootstrap
    originally knew only about BACKUP_ADMIN_GROUP. Renaming ADMIN_GROUP then
    produced a group nothing created and nobody joined, which 403s every route
    on the monitor — including for webadmin, with no way back in via the UI.
    """

    @staticmethod
    def _reload_with(monkeypatch, **env):
        """Re-import bootstrap so its module-level group constants pick up env."""
        import importlib

        import bootstrap

        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return importlib.reload(bootstrap)

    def test_defaults_collapse_to_one_group(self, monkeypatch):
        mod = self._reload_with(monkeypatch, ADMIN_GROUP=None, BACKUP_ADMIN_GROUP=None)

        assert mod.GATE_GROUPS == ["monitor_admin"], "duplicate group would be created twice"
        assert mod.DEFAULT_GROUPS == ["tak_ROLE_ADMIN", "monitor_admin"]

    def test_renamed_admin_group_is_bootstrapped(self, monkeypatch):
        mod = self._reload_with(
            monkeypatch, ADMIN_GROUP="fastak_operators", BACKUP_ADMIN_GROUP=None
        )

        assert "fastak_operators" in mod.GATE_GROUPS
        assert "monitor_admin" in mod.GATE_GROUPS
        assert mod.DEFAULT_GROUPS[0] == "tak_ROLE_ADMIN"

    def test_split_groups_are_both_bootstrapped(self, monkeypatch):
        mod = self._reload_with(
            monkeypatch,
            ADMIN_GROUP="fastak_operators",
            BACKUP_ADMIN_GROUP="fastak_backup",
            NODERED_ADMIN_GROUP="fastak_operators",
        )

        assert mod.GATE_GROUPS == ["fastak_operators", "fastak_backup"]

    def test_node_red_group_is_bootstrapped(self, monkeypatch):
        mod = self._reload_with(
            monkeypatch,
            ADMIN_GROUP=None,
            BACKUP_ADMIN_GROUP=None,
            NODERED_ADMIN_GROUP="flow_authors",
        )

        assert mod.GATE_GROUPS == ["monitor_admin", "flow_authors"]

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        mod = self._reload_with(monkeypatch, ADMIN_GROUP="   ", BACKUP_ADMIN_GROUP=None)

        assert mod.GATE_GROUPS == ["monitor_admin"]

    def test_webadmin_joins_every_gate_group(self, mock_graphql, monkeypatch):
        """A split ADMIN_GROUP/BACKUP_ADMIN_GROUP must not leave webadmin in only one."""
        # TAK_WEBADMIN_PASSWORD is captured at import time, so it has to be set
        # before the reload or main() skips the webadmin branch entirely.
        mod = self._reload_with(
            monkeypatch,
            ADMIN_GROUP="fastak_operators",
            BACKUP_ADMIN_GROUP="fastak_backup",
            NODERED_ADMIN_GROUP="fastak_operators",
            TAK_WEBADMIN_PASSWORD="pw",
        )
        monkeypatch.setattr(mod, "graphql", mock_graphql)
        monkeypatch.setattr(mod, "lldap_login", MagicMock(return_value="fake-token"))
        monkeypatch.setattr(mod, "set_password", MagicMock())
        monkeypatch.setattr(mod, "ensure_user", MagicMock(return_value="webadmin-id"))
        monkeypatch.setattr(mod, "set_user_attribute", MagicMock())
        added = MagicMock()
        monkeypatch.setattr(mod, "add_to_group", added)

        groups = [
            {"id": 1, "displayName": "tak_ROLE_ADMIN"},
            {"id": 2, "displayName": "fastak_operators"},
            {"id": 3, "displayName": "fastak_backup"},
        ]
        mock_graphql.side_effect = [
            {"addUserAttribute": {"ok": True}},
            {"addUserAttribute": {"ok": True}},
            {"addUserAttribute": {"ok": True}},
            {"addUserAttribute": {"ok": True}},
            {"groups": groups},
            {"groups": groups},
            {"groups": groups},
            {"user": {"id": "svc_fasttakapi", "displayName": "svc_fasttakapi"}},
            {"updateUser": {"ok": True}},
        ]

        mod.main()

        joined = {call.args[3] for call in added.call_args_list}
        assert joined == {1, 2, 3}, f"webadmin missing a gate group: joined ids {joined}"
