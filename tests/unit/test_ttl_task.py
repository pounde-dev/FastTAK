"""Tests for TTL enforcement scheduler task."""

import time
from unittest.mock import MagicMock


class TestTtlEnforcement:
    def test_deactivates_expired_active_user(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "id": 1,
                "username": "tempuser",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 3600,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_tak.revoke_all_user_certs.return_value = True

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_called_once_with(1)
        mock_tak.revoke_all_user_certs.assert_called_once_with("tempuser")
        mock_ak.mark_certs_revoked.assert_called_once_with(1)

    def test_reconciles_already_deactivated_user(self):
        """User deactivated but certs not yet revoked."""
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "id": 2,
                "username": "olduser",
                "is_active": False,
                "attributes": {
                    "fastak_expires": int(time.time()) - 86400,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_tak.revoke_all_user_certs.return_value = True

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_not_called()  # Already inactive
        mock_tak.revoke_all_user_certs.assert_called_once_with("olduser")
        mock_ak.mark_certs_revoked.assert_called_once_with(2)

    def test_skips_marking_on_revocation_failure(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "id": 3,
                "username": "failuser",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_tak.revoke_all_user_certs.return_value = False

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_called_once_with(3)
        mock_ak.mark_certs_revoked.assert_not_called()

    def test_continues_on_individual_user_error(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "id": 4,
                "username": "error_user",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
            {
                "id": 5,
                "username": "ok_user",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
        ]
        mock_ak.deactivate_user.side_effect = [Exception("API error"), None]
        mock_tak.revoke_all_user_certs.return_value = True

        _check_user_expiry(mock_ak, mock_tak)

        # Should still process second user
        assert mock_ak.deactivate_user.call_count == 2
        # ok_user's certs should be revoked
        mock_tak.revoke_all_user_certs.assert_called_with("ok_user")
        mock_ak.mark_certs_revoked.assert_called_once_with(5)

    def test_defers_revocation_when_tak_unavailable(self):
        """When no TAK client is configured, certs cannot be revoked — the user
        must NOT be marked revoked, so a later tick retries once TAK is available
        (issue #55). The account is still deactivated in LLDAP."""
        from unittest.mock import patch

        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {
                "id": 6,
                "username": "orphan",
                "is_active": True,
                "attributes": {
                    "fastak_expires": int(time.time()) - 60,
                    "fastak_certs_revoked": False,
                },
            },
        ]

        # tak=None arg makes the function resolve the scheduler singleton; force it None.
        with patch("app.scheduler._get_scheduler_tak", return_value=None):
            _check_user_expiry(mock_ak, None)

        mock_ak.deactivate_user.assert_called_once_with(6)
        mock_ak.mark_certs_revoked.assert_not_called()

    def test_deferred_revocation_completes_on_a_later_tick(self):
        """The point of deferring (#55): the user stays pending, so the next tick
        with a TAK client available revokes the certs and marks them revoked."""
        from unittest.mock import patch

        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        pending_user = {
            "id": 7,
            "username": "retried",
            "is_active": True,
            "attributes": {
                "fastak_expires": int(time.time()) - 60,
                "fastak_certs_revoked": False,
            },
        }
        mock_ak.get_users_pending_expiry.return_value = [pending_user]

        with patch("app.scheduler._get_scheduler_tak", return_value=None):
            _check_user_expiry(mock_ak, None)

        # Still pending — deactivated, but not marked revoked.
        mock_ak.mark_certs_revoked.assert_not_called()
        pending_user["is_active"] = False

        mock_tak = MagicMock()
        mock_tak.revoke_all_user_certs.return_value = True
        _check_user_expiry(mock_ak, mock_tak)

        mock_tak.revoke_all_user_certs.assert_called_once_with("retried")
        mock_ak.mark_certs_revoked.assert_called_once_with(7)
        # Already deactivated on the first tick; the retry must not repeat it.
        mock_ak.deactivate_user.assert_called_once_with(7)

    def test_noop_when_no_expired_users(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = []

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_not_called()
        mock_tak.revoke_all_user_certs.assert_not_called()


class TestSystemAccounts:
    def test_an_expired_ttl_never_deactivates_an_account_the_stack_needs(self):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock()
        mock_tak = MagicMock()
        mock_ak.get_users_pending_expiry.return_value = [
            {"id": 9, "username": "webadmin", "is_active": True},
            {"id": 10, "username": "adm_ldapservice", "is_active": True},
        ]

        _check_user_expiry(mock_ak, mock_tak)

        mock_ak.deactivate_user.assert_not_called()
        mock_tak.revoke_all_user_certs.assert_not_called()


class TestLeftoverAccess:
    """Accounts deactivated before deactivation cut anything (DD-061)."""

    def _run(self, leftover, **ak_config):
        from app.scheduler import _check_user_expiry

        mock_ak = MagicMock(**ak_config)
        mock_ak.get_users_pending_expiry.return_value = []
        mock_ak.get_inactive_users_with_groups.return_value = leftover
        _check_user_expiry(mock_ak, MagicMock())
        return mock_ak

    def test_inactive_account_with_groups_is_cut(self):
        mock_ak = self._run([{"id": 3, "username": "left_last_year"}])
        mock_ak.deactivate_user.assert_called_once_with(3)

    def test_one_failure_does_not_stop_the_rest(self):
        mock_ak = self._run(
            [{"id": 3, "username": "a"}, {"id": 4, "username": "b"}],
            **{"deactivate_user.side_effect": [RuntimeError("down"), None]},
        )
        assert mock_ak.deactivate_user.call_count == 2

    def test_system_accounts_are_left_alone(self):
        mock_ak = self._run([{"id": 5, "username": "svc_fasttakapi"}])
        mock_ak.deactivate_user.assert_not_called()

    def test_a_failed_listing_is_not_fatal(self):
        self._run([], **{"get_inactive_users_with_groups.side_effect": RuntimeError("down")})
