"""Service account management API routes — /api/service-accounts."""

import logging
import re
from enum import Enum
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.service_accounts.cert_gen import (
    generate_client_cert,
    get_revoked_serials,
    register_admin_cert,
    remove_nodered_pems,
    unregister_admin_cert,
    write_nodered_pems,
)
from app.api.users.identity import IdentityClient
from app.api.users.offboarding import offboard, refuse_system_account
from app.api.users.tak_server import TakServerClient
from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["service-accounts"])

CERT_FILES_PATH = Path("/opt/tak/certs/files")

_VALID_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")

# ── Client singletons ────────────────────────────────────────────

_identity: IdentityClient | None = None
_tak_server: TakServerClient | None = None


def _get_identity() -> IdentityClient:
    global _identity
    if _identity is None:
        if not settings.ldap_admin_password:
            raise HTTPException(503, "Identity provider not configured")
        _identity = IdentityClient(
            lldap_url=settings.lldap_url,
            proxy_url=settings.ldap_proxy_url,
            admin_password=settings.ldap_admin_password,
            hidden_prefixes=[],
            proxy_secret=settings.ldap_proxy_secret,
        )
    return _identity


def _get_tak_server() -> TakServerClient | None:
    global _tak_server
    if _tak_server is None and settings.tak_api_cert_path:
        _tak_server = TakServerClient(
            base_url=settings.tak_server_url,
            cert_path=settings.tak_api_cert_path,
            cert_password=settings.tak_api_cert_password,
        )
    return _tak_server


# ── Request / response models ────────────────────────────────────


class ServiceAccountMode(str, Enum):
    data = "data"
    admin = "admin"


class CreateServiceAccountRequest(BaseModel):
    name: str
    display_name: str
    mode: ServiceAccountMode
    groups: list[str] | None = None
    validity_days: int | None = Field(default=None, gt=0, le=3650)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        # Strip svc_ prefix for validation, it gets added back later
        raw = v[4:] if v.startswith("svc_") else v
        if not _VALID_NAME.match(raw):
            raise ValueError("Name must contain only letters, numbers, dots, hyphens, underscores")
        if len(v) > 64:
            raise ValueError("Name must be 64 characters or fewer")
        return v

    @model_validator(mode="after")
    def validate_groups_for_mode(self):
        if self.mode == ServiceAccountMode.data:
            if not self.groups:
                raise ValueError("Groups are required for data mode service accounts")
        elif self.mode == ServiceAccountMode.admin:
            if self.groups:
                raise ValueError("Groups are forbidden for admin mode service accounts")
        return self


class UpdateServiceAccountRequest(BaseModel):
    display_name: str | None = None
    groups: list[str] | None = None


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/api/service-accounts", summary="List service accounts")
def list_service_accounts():
    """List all service accounts (svc_ prefix users).

    Only returns LLDAP users whose username starts with ``svc_``.
    Regular users and other hidden-prefix accounts are excluded.

    Returns:
        Dict with ``results`` list of service account objects.
    """
    ak = _get_identity()
    accounts = ak.list_users(search="svc_")
    return {"results": accounts}


@router.post("/api/service-accounts", status_code=201, summary="Create service account")
def create_service_account(body: CreateServiceAccountRequest):
    """Create a new service account with a client certificate.

    Two modes are supported:

    - **data**: Receives TAK data via assigned groups. Groups are required and
      must already exist.
    - **admin**: Gets a TAK Server admin cert. Groups are forbidden.

    The ``svc_`` prefix is auto-prepended to the name if not already present.
    Certificate validity is 1-3650 days (defaults to 365 for data, 730 for
    admin).

    If certificate generation fails after the LLDAP user is created, the
    user is automatically rolled back (deactivated) to avoid orphaned accounts.

    Args:
        body: Service account creation parameters.

    Returns:
        Created account object with mode, validity_days, and cert_download_url.

    Raises:
        HTTPException(400): If required groups don't exist, or groups are
            provided for admin mode.
        HTTPException(500): If certificate generation or admin registration
            fails (LLDAP user is rolled back).
        HTTPException(503): If identity provider is not configured.
    """
    ak = _get_identity()

    # Auto-prepend svc_ if not already present
    username = body.name if body.name.startswith("svc_") else f"svc_{body.name}"

    # Determine validity
    if body.validity_days is not None:
        validity_days = body.validity_days
    else:
        validity_days = 365 if body.mode == ServiceAccountMode.data else 730

    # Validate groups exist before creating anything
    if body.groups and body.mode == ServiceAccountMode.data:
        existing_groups = ak.list_groups()
        existing_names = {g["name"] for g in existing_groups}
        missing = [g for g in body.groups if g not in existing_names]
        if missing:
            msg = f"Groups do not exist: {', '.join(missing)}. Create them first."
            raise HTTPException(400, msg)

    # Create LLDAP user
    user_type = "svc_data" if body.mode == ServiceAccountMode.data else "svc_admin"
    user = ak.create_user(
        username=username,
        name=body.display_name,
        groups=body.groups if body.mode == ServiceAccountMode.data else None,
        user_type=user_type,
    )

    # Generate certificate
    cert_result = generate_client_cert(username, validity_days)
    if not cert_result.get("success"):
        # Rollback: deactivate the orphaned LLDAP user
        log.warning(
            "Cert generation failed for %s, rolling back LLDAP user %s: %s",
            username,
            user["id"],
            cert_result.get("error"),
        )
        try:
            ak.deactivate_user(user["id"])
        except Exception:
            log.exception("Failed to rollback LLDAP user %s", user["id"])
        raise HTTPException(
            500,
            f"Certificate generation failed: {cert_result.get('error', 'unknown error')}",
        )

    # For data-mode accounts, also extract unencrypted PEM cert+key into the
    # Node-RED-bound subdirectory so flows can use the account without a
    # container restart. Best-effort — a failure here is non-fatal (the .p12
    # is still usable for manual WinTAK/ATAK import), but log it.
    if body.mode == ServiceAccountMode.data:
        pem_result = write_nodered_pems(username)
        if not pem_result.get("success"):
            log.warning(
                "Node-RED PEM extraction failed for %s: %s",
                username,
                pem_result.get("error"),
            )

    # Register as admin if admin mode
    if body.mode == ServiceAccountMode.admin:
        admin_result = register_admin_cert(username)
        if not admin_result.get("success"):
            log.warning(
                "Admin cert registration failed for %s: %s",
                username,
                admin_result.get("error") or admin_result.get("output"),
            )
            try:
                ak.deactivate_user(user["id"])
            except Exception:
                log.exception("Failed to rollback LLDAP user %s", user["id"])
            detail = admin_result.get("error") or admin_result.get("output", "unknown error")
            raise HTTPException(500, f"Admin cert registration failed: {detail}")

    return {
        **user,
        "mode": body.mode.value,
        "validity_days": validity_days,
        "cert_download_url": f"/api/service-accounts/{user['id']}/certs/download",
    }


@router.patch("/api/service-accounts/{account_id}", summary="Update service account")
def update_service_account(account_id: int, body: UpdateServiceAccountRequest):
    """Update a service account's display name or group membership.

    Validation is atomic: all groups are checked for existence before any
    changes are applied. If any group is missing, the entire request is
    rejected without side effects.

    Args:
        account_id: LLDAP user ID.
        body: Fields to update (display_name, groups).

    Returns:
        ``{"success": true}`` on success.

    Raises:
        HTTPException(400): If any specified groups don't exist.
        HTTPException(404): If account_id doesn't exist or isn't a svc_ account.
    """
    ak = _get_identity()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")

    # Validate groups before making any changes (atomic validation)
    if body.groups is not None:
        user_type = user.get("fastak_user_type")
        if user_type == "svc_admin":
            raise HTTPException(400, "Admin mode service accounts cannot have groups")
        if user_type == "svc_data" and body.groups == []:
            raise HTTPException(400, "Data mode service accounts require at least one group")

        existing_groups = ak.list_groups()
        existing_names = {g["name"] for g in existing_groups}
        missing = [g for g in body.groups if g not in existing_names]
        if missing:
            msg = f"Groups do not exist: {', '.join(missing)}. Create them first."
            raise HTTPException(400, msg)

    if body.display_name is not None:
        ak.update_user(account_id, name=body.display_name)

    if body.groups is not None:
        ak.set_user_groups(account_id, body.groups)

    return {"success": True}


@router.get("/api/service-accounts/{account_id}", summary="Get service account")
def get_service_account(account_id: int):
    """Get a single service account by ID.

    Includes TAK Server certificate info when the TAK API is configured.

    Args:
        account_id: LLDAP user ID.

    Returns:
        Service account object, optionally with ``certs`` list.

    Raises:
        HTTPException(404): If account_id doesn't exist or isn't a svc_ account.
    """
    ak = _get_identity()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")
    tak = _get_tak_server()
    if tak:
        user["certs"] = tak.list_user_certs(user["username"])
    return user


@router.delete("/api/service-accounts/{account_id}", summary="Delete service account")
def delete_service_account(account_id: int):
    """Deactivate a service account: cut its logins and revoke its certificates.

    This does **not** hard-delete the LLDAP user, which preserves the audit
    trail. Safe to repeat if revocation failed.

    Args:
        account_id: LLDAP user ID.

    Returns:
        ``{"success": true, "username": "...", "certs_revoked": bool}``. A false
        ``certs_revoked`` means a cert may still work.

    Raises:
        HTTPException(404): If account_id doesn't exist or isn't a svc_ account.
        HTTPException(409): If this is an account the stack depends on.
        HTTPException(502): If deactivation did not finish.
    """
    ak = _get_identity()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")

    refuse_system_account(user["username"])

    # First, and on its own: offboard raises when a credential cannot be cut,
    # and an admin cert must not keep its role because LLDAP was unreachable.
    admin_role_removed = True
    if user.get("fastak_user_type") == "svc_admin":
        result = unregister_admin_cert(user["username"])
        if not result.get("success"):
            log.error(
                "Failed to remove admin role from %s: %s",
                user["username"],
                result.get("error") or result.get("output"),
            )
            admin_role_removed = False

    try:
        certs_revoked = offboard(ak, _get_tak_server(), account_id, user["username"])
    finally:
        # Clean up the Node-RED PEM pair if it was provisioned. Idempotent —
        # admin-mode accounts never had one, rm -f handles that case.
        remove_nodered_pems(user["username"])

    return {
        "success": True,
        "username": user["username"],
        "certs_revoked": certs_revoked and admin_role_removed,
    }


@router.get(
    "/api/service-accounts/{account_id}/certs/download", summary="Download service account cert"
)
def download_cert(account_id: int):
    """Download the .p12 certificate for a service account.

    The .p12 password is always ``atakatak`` (DD-025). Revoked certificates
    are blocked from download (DD-028) — the serial is checked against the
    CRL before serving.

    Args:
        account_id: LLDAP user ID.

    Returns:
        .p12 file as ``application/x-pkcs12`` download.

    Raises:
        HTTPException(403): If the certificate has been revoked.
        HTTPException(404): If account not found, not a svc_ account, or
            cert file missing.
    """
    ak = _get_identity()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")

    username = user["username"]
    p12_path = CERT_FILES_PATH / f"{username}.p12"
    if not p12_path.exists():
        raise HTTPException(404, "Certificate file not found")

    # Block download of revoked certs
    pem_path = CERT_FILES_PATH / f"{username}.pem"
    if pem_path.exists():
        import subprocess

        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", str(pem_path), "-noout", "-serial"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                serial = result.stdout.strip().split("=", 1)[1].lower()
                if serial in get_revoked_serials():
                    raise HTTPException(403, "Certificate has been revoked")
        except HTTPException:
            raise
        except Exception:
            pass  # If we can't check, allow download (DD-028)

    return FileResponse(
        path=str(p12_path),
        media_type="application/x-pkcs12",
        filename=f"{username}.p12",
    )
