"""Cutting off an account's access, shared by the user and service account routes.

See DD-061. `IdentityClient.deactivate_user` cuts every credential in LLDAP and
the ldap-proxy; this module adds cert revocation and reports what really happened.
"""

import logging

from fastapi import HTTPException

log = logging.getLogger(__name__)

# Deactivating these would not do what the operator expects. svc_fasttakapi's cert
# is the monitor's own client identity for TAK Server, so revoking it blinds the
# monitor. webadmin is re-created with its .env password on every restart.
# adm_ldapservice is the account the monitor and TAK Server use to reach LLDAP.
_SYSTEM_ACCOUNTS = {
    "svc_fasttakapi": "the monitor uses this account's cert to reach TAK Server",
    "webadmin": "it comes back on restart; clear TAK_WEBADMIN_PASSWORD in .env instead",
    "adm_ldapservice": "the stack uses this account to reach the directory",
}


def is_system_account(username: str) -> bool:
    return username in _SYSTEM_ACCOUNTS


def refuse_system_account(username: str, action: str = "deactivate") -> None:
    """Raise 409 when `username` is an account the stack itself depends on."""
    reason = _SYSTEM_ACCOUNTS.get(username)
    if reason:
        raise HTTPException(409, f"Cannot {action} {username}: {reason}")


def offboard(ak, tak, user_id: int, username: str) -> bool:
    """Cut an account's credentials and revoke its certs. Returns certs_revoked.

    Certs are revoked even when the credential step fails: each step closes a
    separate way in, and a partial cut beats none. Both steps are safe to repeat,
    so an operator can retry after a failure.

    Raises:
        HTTPException(502): If a credential could not be cut.
    """
    cut_failed = False
    try:
        ak.deactivate_user(user_id)
    except Exception:
        log.exception("Failed to cut credentials for %s", username)
        cut_failed = True

    certs_revoked = False
    if tak:
        certs_revoked = tak.revoke_all_user_certs(username)
        if certs_revoked:
            ak.mark_certs_revoked(user_id)

    if cut_failed:
        raise HTTPException(
            502,
            f"{username} may still be able to log in: deactivation did not finish. "
            "Check the identity services and try again.",
        )
    return certs_revoked
