"""User management API routes — /api/users and /api/groups."""

import glob
import io
import re
import time
import uuid
import zipfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.api.users.enrollment import build_enrollment_url
from app.api.users.identity import IdentityClient, UserExistsError
from app.api.users.offboarding import offboard, refuse_system_account
from app.api.users.tak_server import TakServerClient
from app.config import settings

router = APIRouter(tags=["users"])

_VALID_USERNAME = re.compile(r"^[a-zA-Z0-9._-]+$")

CERT_FILES_PATH = Path("/opt/tak/certs/files")

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
            hidden_prefixes=settings.users_hidden_prefixes.split(","),
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


# ── Request models ────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str
    name: str
    ttl_hours: int | None = None
    groups: list[str] = Field(min_length=1)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not _VALID_USERNAME.match(v):
            raise ValueError(
                "Username must contain only letters, numbers, dots, hyphens, underscores"
            )
        if len(v) > 64:
            raise ValueError("Username must be 64 characters or fewer")
        return v


class UpdateUserRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    ttl_hours: int | None = None  # null in JSON = clear TTL; omit field = leave unchanged


# Passwords are accepted on TAK Server's port 8446, which is published to the
# internet, so a short one can be guessed from anywhere.
MIN_PASSWORD_LENGTH = 12


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        # Group names travel comma-joined in Remote-Groups and inside LDAP DNs,
        # so a comma in one would read as membership of a second group.
        if not re.match(r"^[a-zA-Z0-9._-]+( [a-zA-Z0-9._-]+)*$", v):
            raise ValueError(
                "Name must contain only letters, numbers, dots, hyphens, underscores "
                "and single spaces between words"
            )
        return v


class SetGroupsRequest(BaseModel):
    groups: list[str]


class RevokeCertRequest(BaseModel):
    cert_id: int | None = None
    cert_name: str | None = None


class GenerateCertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not re.match(r"^[a-zA-Z0-9._-]+$", v):
            raise ValueError("Name must contain only letters, numbers, dots, hyphens, underscores")
        return v


# ── User endpoints ────────────────────────────────────────────────


@router.get("/api/users", summary="List users")
def list_users(
    search: str | None = Query(default=None),
    include: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """List human users with optional search and pagination.

    Users with hidden prefixes (``svc_``, ``adm_``, ``ma-``) are
    automatically filtered out by the identity client. Pagination is
    client-side: the full filtered list is fetched, then sliced.

    Use ``include=certs`` to embed each user's certificate list (adds latency
    from TAK Server API calls).

    Args:
        search: Filter usernames/names by substring.
        include: Comma-separated includes. Currently only ``"certs"`` is
            supported.
        page: Page number (1-indexed).
        page_size: Results per page (1-200, default 50).

    Returns:
        Paginated dict with ``results``, ``count``, ``page``, ``page_size``.
    """
    ak = _get_identity()
    users = ak.list_users(search=search)

    total = len(users)
    start = (page - 1) * page_size
    page_users = users[start : start + page_size]

    if include and "certs" in include:
        tak = _get_tak_server()
        if tak:
            for u in page_users:
                u["certs"] = tak.list_user_certs(u["username"])

    return {"results": page_users, "count": total, "page": page, "page_size": page_size}


@router.post("/api/users", status_code=201, summary="Create user")
def create_user(body: CreateUserRequest):
    """Create a new TAK user in LLDAP.

    Users are passwordless by default — they enroll via the ``/enroll``
    endpoint and connect with client certificates. An optional ``ttl_hours``
    sets an auto-expiry so temporary users don't linger.

    Args:
        body: User creation parameters (username, name, ttl_hours, groups).

    Returns:
        Created user object.
    """
    ak = _get_identity()

    existing_groups = ak.list_groups()
    existing_names = {g["name"] for g in existing_groups}
    missing = [g for g in body.groups if g not in existing_names]
    if missing:
        msg = f"Groups do not exist: {', '.join(missing)}. Create them first."
        raise HTTPException(400, msg)

    try:
        return ak.create_user(
            username=body.username,
            name=body.name,
            ttl_hours=body.ttl_hours,
            groups=body.groups,
            user_type="user",
        )
    except UserExistsError:
        raise HTTPException(409, f"An account named '{body.username}' already exists") from None


@router.get("/api/users/{user_id}", summary="Get user")
def get_user(user_id: int):
    """Get a single user by ID, including their TAK certificates.

    Args:
        user_id: LLDAP user ID.

    Returns:
        User object with ``certs`` list when TAK Server is configured.

    Raises:
        HTTPException(404): If user_id doesn't exist.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    tak = _get_tak_server()
    if tak:
        user["certs"] = tak.list_user_certs(user["username"])
    return user


@router.patch("/api/users/{user_id}", summary="Update user")
def update_user(user_id: int, body: UpdateUserRequest):
    """Update user fields (name, is_active, ttl_hours).

    TTL semantics: sending ``ttl_hours: null`` (JSON null) **clears** the TTL.
    Omitting the field entirely leaves it unchanged. This distinction uses
    Pydantic's ``model_fields_set`` to differentiate null from absent.

    Setting ``is_active: false`` does the same as DELETE. Setting it to ``true``
    reactivates the user, who then needs a new password and groups from an admin,
    because deactivation removes both.

    Args:
        user_id: LLDAP user ID.
        body: Fields to update (all optional).

    Returns:
        Updated user object.

    Raises:
        HTTPException(404): If user_id doesn't exist.
        HTTPException(409): If deactivating an account the stack depends on.
        HTTPException(502): If deactivation did not finish.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if body.is_active is False:
        refuse_system_account(user["username"])
    if body.ttl_hours is not None:
        # Expiry deactivates the account, so a TTL is the same request.
        refuse_system_account(user["username"], action="set an expiry on")
    kwargs = {}
    # is_active and name use `is not None` because null has no meaningful
    # semantic for them — there's no "clear name" or "clear active state".
    # ttl_hours uses model_fields_set so that null (JSON null) means "clear the
    # TTL" while omitting the field entirely means "leave unchanged".
    if body.name is not None:
        kwargs["name"] = body.name
    if body.is_active:
        kwargs["is_active"] = True
    if "ttl_hours" in body.model_fields_set:
        kwargs["ttl_hours"] = body.ttl_hours
    updated = ak.update_user(user_id, **kwargs)
    if body.is_active is False:
        offboard(ak, _get_tak_server(), user_id, user["username"])
        return ak.get_user(user_id)
    return updated


@router.delete("/api/users/{user_id}", summary="Delete user")
def delete_user(user_id: int):
    """Deactivate a user: cut their logins and revoke their certificates.

    This does **not** hard-delete the LLDAP user, which preserves the audit
    trail. Their password, groups and enrollment tokens are removed. They can be
    reactivated via PATCH with ``is_active: true``, but need a new password and
    groups afterwards. Safe to repeat if revocation failed.

    Args:
        user_id: LLDAP user ID.

    Returns:
        ``{"success": true, "username": "...", "certs_revoked": bool}``. A false
        ``certs_revoked`` means logins are cut but a cert may still work.

    Raises:
        HTTPException(404): If user_id doesn't exist.
        HTTPException(409): If this is an account the stack depends on.
        HTTPException(502): If deactivation did not finish.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    refuse_system_account(user["username"])

    certs_revoked = offboard(ak, _get_tak_server(), user_id, user["username"])
    return {"success": True, "username": user["username"], "certs_revoked": certs_revoked}


@router.post("/api/users/{user_id}/password", summary="Set user password")
def set_password(user_id: int, body: SetPasswordRequest):
    """Set a password for a user.

    Most TAK users do **not** need passwords — they authenticate via client
    certificates. Passwords are only required for WebTAK browser access on
    port 8446.

    Args:
        user_id: LLDAP user ID.
        body: Password to set (at least 12 characters).

    Returns:
        ``{"success": true}`` on success.

    Raises:
        HTTPException(400): If the user is deactivated.
        HTTPException(404): If user_id doesn't exist.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if not user["is_active"]:
        raise HTTPException(400, "Cannot set password for deactivated user")
    ak.set_password(user_id, body.password)
    return {"success": True}


@router.post("/api/users/{user_id}/enroll", summary="Enroll user")
def enroll_user(user_id: int):
    """Generate a ``tak://`` enrollment URL for ATAK/iTAK provisioning.

    The URL contains a 15-minute TTL app password token. If a valid token
    already exists for this user, it is reused rather than creating a new one
    (idempotent re-enrollment).

    Args:
        user_id: LLDAP user ID.

    Returns:
        Dict with ``enrollment_url`` (tak:// format) and ``expires_at``
        timestamp.

    Raises:
        HTTPException(400): If the user is deactivated.
        HTTPException(404): If user_id doesn't exist.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if not user["is_active"]:
        raise HTTPException(400, "Cannot enroll deactivated user")
    token, expires_at = ak.get_or_create_enrollment_token(
        user_id,
        settings.enrollment_token_ttl_minutes,
        settings.enrollment_token_one_time,
    )
    url = build_enrollment_url(
        token=token, server_address=settings.server_address, username=user["username"]
    )
    return {"enrollment_url": url, "expires_at": expires_at}


# ── Cert endpoints ────────────────────────────────────────────────


def _get_revoked_serials() -> set[str]:
    """Get set of revoked cert serial numbers from the CRL."""
    from app.api.service_accounts.cert_gen import get_revoked_serials

    return get_revoked_serials()


def _get_cert_serial(pem_path: Path) -> str | None:
    """Get the serial number from a PEM cert file."""
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-serial"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "serial=AABBCCDD"
            return result.stdout.strip().split("=", 1)[1].lower()
    except Exception:
        pass
    return None


def _get_cert_expiry(pem_path: Path) -> str | None:
    """Get the expiration date from a PEM cert file as ISO string."""
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-enddate"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "notAfter=Mar 31 12:00:00 2027 GMT"
            from datetime import UTC, datetime

            date_str = result.stdout.strip().split("=", 1)[1]
            expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
            return expiry.replace(tzinfo=UTC).isoformat()
    except Exception:
        pass
    return None


def _compute_cert_hash(pem_path: Path) -> str | None:
    """Compute SHA-256 fingerprint of a PEM cert file to match against TAK Server."""
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-fingerprint", "-sha256"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "sha256 Fingerprint=AA:BB:CC:..."
            return result.stdout.strip().split("=", 1)[1].replace(":", "").lower()
    except Exception:
        pass
    return None


def _build_truststore_p12() -> bytes:
    """Build a PKCS#12 truststore containing the CA certificate.

    Uses the Java truststore layout: the CA bag carries a friendlyName and the
    Oracle trustedKeyUsage attribute. Java-family loaders (ATAK's truststore
    validator, keytool) skip unnamed cert bags and would see an empty store.

    Returns:
        PKCS#12 bytes with friendly name ``truststore``, password ``atakatak``.

    Raises:
        HTTPException(500): If ``ca.pem`` is not found.
    """
    ca_pem_path = CERT_FILES_PATH / "ca.pem"
    if not ca_pem_path.exists():
        raise HTTPException(500, "CA certificate not found")
    ca_cert = x509.load_pem_x509_certificate(ca_pem_path.read_bytes())
    return pkcs12.serialize_java_truststore(
        [pkcs12.PKCS12Certificate(ca_cert, b"truststore")],
        BestAvailableEncryption(b"atakatak"),
    )


@router.get("/api/users/{user_id}/certs", summary="List user certificates")
def list_user_certs(user_id: int):
    """List all certificates for a user, merging on-disk and TAK Server data.

    Builds a unified list by scanning on-disk .p12/.pem files and matching
    them to TAK Server certadmin entries via SHA-256 hash. This catches both
    API-generated certs (on-disk) and QR-enrolled certs (certadmin only).

    Revocation status is checked against the CRL for on-disk certs and
    against certadmin's ``revocation_date`` for server-side certs.

    Args:
        user_id: LLDAP user ID.

    Returns:
        List of cert entries with ``name``, ``downloadable``, ``revoked``,
        ``cert_id``, and ``expiration_date``.

    Raises:
        HTTPException(404): If user_id doesn't exist.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    username = user["username"]

    # Get TAK Server certs (includes QR-enrolled and registered certs)
    tak_certs = []
    tak = _get_tak_server()
    if tak:
        tak_certs = tak.list_user_certs(username)

    # Build hash → TAK Server cert lookup for matching
    # Normalize: strip colons, lowercase (certadmin uses "AA:BB:CC", we compute "aabbcc")
    def _normalize_hash(h: str) -> str:
        return h.replace(":", "").lower()

    tak_by_hash = {_normalize_hash(c["hash"]): c for c in tak_certs if c.get("hash")}

    # Get revoked serials from CRL for checking on-disk certs
    revoked_serials = _get_revoked_serials()

    # Scan on-disk certs and merge with TAK Server data
    unified = []
    matched_hashes = set()

    prefix = f"{username}-"
    for p12 in sorted(CERT_FILES_PATH.glob(f"{prefix}*.p12")):
        cert_name = p12.stem[len(prefix) :]
        pem_path = CERT_FILES_PATH / f"{p12.stem}.pem"

        # Check CRL for revocation (covers API-generated certs not in certadmin)
        is_revoked = False
        if pem_path.exists():
            serial = _get_cert_serial(pem_path)
            if serial and serial in revoked_serials:
                is_revoked = True

        # Read expiration from the PEM file
        expiry = _get_cert_expiry(pem_path) if pem_path.exists() else None

        entry = {
            "name": cert_name,
            "downloadable": not is_revoked,
            "revoked": is_revoked,
            "cert_id": None,
            "expiration_date": expiry,
        }

        # Try to match with TAK Server cert by hash
        if pem_path.exists():
            file_hash = _compute_cert_hash(pem_path)
            if file_hash and file_hash in tak_by_hash:
                tak_cert = tak_by_hash[file_hash]
                entry["cert_id"] = tak_cert["id"]
                entry["revoked"] = tak_cert.get("revocation_date") is not None
                entry["expiration_date"] = tak_cert.get("expiration_date")
                matched_hashes.add(file_hash)

        unified.append(entry)

    # Add TAK Server certs that don't have on-disk files (e.g., QR-enrolled)
    for tak_cert in tak_certs:
        cert_hash = _normalize_hash(tak_cert.get("hash") or "")
        if cert_hash and cert_hash not in matched_hashes:
            unified.append(
                {
                    "name": None,
                    "downloadable": False,
                    "revoked": tak_cert.get("revocation_date") is not None,
                    "cert_id": tak_cert["id"],
                    "expiration_date": tak_cert.get("expiration_date"),
                }
            )

    return unified


@router.post(
    "/api/users/{user_id}/certs/generate", status_code=201, summary="Generate user certificate"
)
def generate_user_cert(user_id: int, body: GenerateCertRequest):
    """Generate a named client certificate for a user.

    Creates ``{username}-{name}.p12`` with ``CN=username`` so all certs for a
    user resolve to the same LDAP identity. Certificate validity is capped by
    the user's ``fastak_expires`` if set — the cert will never outlive the user.

    Args:
        user_id: LLDAP user ID.
        body: Certificate name (alphanumeric, dots, hyphens, underscores).

    Returns:
        Dict with ``name``, ``filename``, ``validity_days``, and
        ``download_url``.

    Raises:
        HTTPException(400): If the user is deactivated or already expired.
        HTTPException(404): If user_id doesn't exist.
        HTTPException(409): If a cert with the same name already exists.
        HTTPException(500): If cert generation fails.
    """
    from app.api.service_accounts.cert_gen import generate_client_cert

    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if not user["is_active"]:
        raise HTTPException(400, "Cannot generate cert for deactivated user")

    username = user["username"]
    cert_filename = f"{username}-{body.name}"

    # Any file under this name, not only a .p12: user "ca" with cert name
    # "do-not-share" would otherwise write over ca-do-not-share.key.
    if any(CERT_FILES_PATH.glob(f"{glob.escape(cert_filename)}.*")):
        raise HTTPException(409, f"Certificate '{body.name}' already exists for this user")

    # Determine validity — don't exceed user's expiry if set
    validity_days = 365
    fastak_expires = user.get("fastak_expires")
    if fastak_expires is not None:
        remaining_seconds = fastak_expires - time.time()
        if remaining_seconds <= 0:
            raise HTTPException(400, "User has already expired")
        remaining_days = int(remaining_seconds / 86400)
        validity_days = min(validity_days, max(remaining_days, 1))

    # Generate cert with CN=username (not cert_filename) so all certs
    # resolve to the same LDAP identity
    result = generate_client_cert(cert_filename, validity_days=validity_days, cn_override=username)
    if not result["success"]:
        raise HTTPException(500, f"Cert generation failed: {result.get('error')}")

    return {
        "name": body.name,
        "filename": f"{cert_filename}.p12",
        "validity_days": validity_days,
        "download_url": f"/api/users/{user_id}/certs/download/{body.name}",
    }


@router.post("/api/users/{user_id}/certs/revoke", summary="Revoke user certificate")
def revoke_user_cert(user_id: int, body: RevokeCertRequest):
    """Revoke a certificate and update the CRL so TAK Server rejects it.

    Provide exactly one of ``cert_id`` or ``cert_name``:

    - **cert_id**: For QR-enrolled certs. Fetches PEM from certadmin, revokes
      via CRL, and marks revoked in certadmin.
    - **cert_name**: For API-generated certs. Revokes via CRL using on-disk
      .pem only (certadmin may not know about these).

    Both paths delete enrollment tokens to prevent re-enrollment with cached
    credentials.

    Args:
        user_id: LLDAP user ID.
        body: Exactly one of ``cert_id`` (int) or ``cert_name`` (str).

    Returns:
        ``{"success": true}`` on success.

    Raises:
        HTTPException(400): If neither or both of cert_id/cert_name provided.
        HTTPException(404): If user or certificate not found.
        HTTPException(500): If CRL revocation fails.
        HTTPException(503): If TAK Server not configured (cert_id path).
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if body.cert_id is not None:
        from app.api.service_accounts.cert_gen import revoke_cert_by_pem

        tak = _get_tak_server()
        if not tak:
            raise HTTPException(503, "TAK Server not configured")

        # Get the cert PEM from certadmin to feed to CRL revocation
        certs = tak.list_user_certs(user["username"])
        cert_data = next((c for c in certs if c["id"] == body.cert_id), None)
        if not cert_data:
            raise HTTPException(404, "Certificate not found")
        if not cert_data.get("certificate_pem"):
            raise HTTPException(500, "Cannot revoke: cert PEM not available from TAK Server")

        # CRL revocation (actual TLS disconnect)
        result = revoke_cert_by_pem(cert_data["certificate_pem"])
        if not result["success"]:
            raise HTTPException(500, f"CRL revocation failed: {result.get('error', '')}")

        # Also mark revoked in certadmin database
        tak.revoke_cert(body.cert_id)

        # Delete enrollment tokens to prevent re-enrollment with cached credentials
        ak.delete_enrollment_tokens(user_id)

        return {"success": True}

    if body.cert_name is not None:
        from app.api.service_accounts.cert_gen import revoke_cert_by_name

        cert_filename = f"{user['username']}-{body.cert_name}"
        result = revoke_cert_by_name(cert_filename, owner=user["username"])
        if not result["success"]:
            raise HTTPException(500, f"Revocation failed: {result.get('error', '')}")

        # Delete enrollment tokens to prevent re-enrollment with cached credentials
        ak.delete_enrollment_tokens(user_id)

        return {"success": True}

    raise HTTPException(400, "Provide either cert_id or cert_name")


@router.get("/api/users/{user_id}/certs/download/{cert_name}", summary="Download user certificate")
def download_user_cert(user_id: int, cert_name: str):
    """Download a named .p12 certificate file for a user.

    The ``cert_name`` is validated against an allowlist pattern to prevent
    path traversal. Revoked certificates are blocked from download (DD-028)
    by checking the serial against the CRL before serving.

    The .p12 password is always ``atakatak`` (DD-025).

    Args:
        user_id: LLDAP user ID.
        cert_name: Certificate name (the ``{name}`` part of
            ``{username}-{name}.p12``).

    Returns:
        .p12 file as ``application/x-pkcs12`` download.

    Raises:
        HTTPException(400): If cert_name contains invalid characters.
        HTTPException(403): If the certificate has been revoked.
        HTTPException(404): If user or certificate file not found.
    """
    if not re.match(r"^[a-zA-Z0-9._-]+$", cert_name):
        raise HTTPException(400, "Invalid certificate name")
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    username = user["username"]
    filename = f"{username}-{cert_name}.p12"
    p12_path = CERT_FILES_PATH / filename
    if not p12_path.exists():
        raise HTTPException(404, f"Certificate '{cert_name}' not found")

    # Block download of revoked certs
    pem_path = CERT_FILES_PATH / f"{username}-{cert_name}.pem"
    if pem_path.exists():
        serial = _get_cert_serial(pem_path)
        if serial and serial in _get_revoked_serials():
            raise HTTPException(403, "Certificate has been revoked")

    return FileResponse(
        path=p12_path,
        media_type="application/x-pkcs12",
        filename=filename,
    )


@router.get(
    "/api/users/{user_id}/certs/download_data_package/{cert_name}",
    summary="Download data package",
)
def download_data_package(user_id: int, cert_name: str):
    """Download a TAK connection data package (.zip) for a named certificate.

    Bundles the client .p12, a CA truststore, connection preferences, and a
    manifest into a zip that ATAK/WinTAK can import directly.

    Validation and revocation checks are identical to ``download_user_cert``.

    Args:
        user_id: LLDAP user ID.
        cert_name: Certificate name (the ``{name}`` part of
            ``{username}-{name}.p12``).

    Returns:
        .zip file as ``application/zip`` download.

    Raises:
        HTTPException(400): If cert_name contains invalid characters.
        HTTPException(403): If the certificate has been revoked.
        HTTPException(404): If user or certificate file not found.
        HTTPException(500): If CA certificate is missing.
    """
    if not re.match(r"^[a-zA-Z0-9._-]+$", cert_name):
        raise HTTPException(400, "Invalid certificate name")
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    username = user["username"]
    cert_filename = f"{username}-{cert_name}"
    p12_path = CERT_FILES_PATH / f"{cert_filename}.p12"
    if not p12_path.exists():
        raise HTTPException(404, f"Certificate '{cert_name}' not found")

    # Block download of revoked certs
    pem_path = CERT_FILES_PATH / f"{cert_filename}.pem"
    if pem_path.exists():
        serial = _get_cert_serial(pem_path)
        if serial and serial in _get_revoked_serials():
            raise HTTPException(403, "Certificate has been revoked")

    # Build zip contents
    client_p12 = p12_path.read_bytes()
    truststore_p12 = _build_truststore_p12()

    connect_str = f"{settings.server_address}:8089:ssl"
    cert_loc = f"cert/{cert_filename}.p12"

    config_pref = f"""\
<?xml version='1.0' encoding='ASCII' standalone='yes'?>
<preferences>
  <preference version="1" name="cot_streams">
    <entry key="count" class="class java.lang.Integer">1</entry>
    <entry key="description0" class="class java.lang.String">FastTAK</entry>
    <entry key="enabled0" class="class java.lang.Boolean">true</entry>
    <entry key="connectString0" class="class java.lang.String">{connect_str}</entry>
  </preference>
  <preference version="1" name="com.atakmap.app_preferences">
    <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>
    <entry key="caLocation" class="class java.lang.String">cert/truststore.p12</entry>
    <entry key="caPassword" class="class java.lang.String">atakatak</entry>
    <entry key="certificateLocation" class="class java.lang.String">{cert_loc}</entry>
    <entry key="clientPassword" class="class java.lang.String">atakatak</entry>
  </preference>
</preferences>"""

    pkg_uid = str(uuid.uuid4())
    zip_name = f"{cert_filename}.zip"

    manifest_xml = f"""\
<MissionPackageManifest version="2">
  <Configuration>
    <Parameter name="uid" value="{pkg_uid}"/>
    <Parameter name="name" value="{zip_name}"/>
    <Parameter name="onReceiveDelete" value="true"/>
  </Configuration>
  <Contents>
    <Content ignore="false" zipEntry="config.pref"/>
    <Content ignore="false" zipEntry="certs/truststore.p12"/>
    <Content ignore="false" zipEntry="certs/{cert_filename}.p12"/>
  </Contents>
</MissionPackageManifest>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"certs/{cert_filename}.p12", client_p12)
        zf.writestr("certs/truststore.p12", truststore_p12)
        zf.writestr("config.pref", config_pref)
        zf.writestr("MANIFEST/manifest.xml", manifest_xml)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ── Group endpoints ───────────────────────────────────────────────


@router.get("/api/groups", summary="List groups")
def list_groups():
    """List all LLDAP groups.

    Groups with the ``tak_`` prefix are auto-managed by TAK Server
    integration. ``ROLE_ADMIN`` is filtered out (DD-026).

    Returns:
        List of group objects.
    """
    return _get_identity().list_groups()


@router.post("/api/groups", status_code=201, summary="Create group")
def create_group(body: CreateGroupRequest):
    """Create a new LLDAP group.

    Args:
        body: Group name.

    Returns:
        Created group object.
    """
    return _get_identity().create_group(body.name)


@router.get("/api/groups/{group_id}", summary="Get group")
def get_group(group_id: str):
    """Get a single group by ID.

    Args:
        group_id: LLDAP group ID.

    Returns:
        Group object.

    Raises:
        HTTPException(404): If group_id doesn't exist.
    """
    group = _get_identity().get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    return group


@router.delete("/api/groups/{group_id}", summary="Delete group")
def delete_group(group_id: str):
    """Delete an LLDAP group.

    Args:
        group_id: LLDAP group ID.

    Returns:
        ``{"success": true}`` on success.

    Raises:
        HTTPException(404): If group_id doesn't exist.
    """
    ak = _get_identity()
    group = ak.get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    ak.delete_group(group_id)
    return {"success": True}


@router.put("/api/users/{user_id}/groups", summary="Set user groups")
def set_user_groups(user_id: int, body: SetGroupsRequest):
    """Replace a user's group memberships.

    This is a full replacement, not a merge — groups not in the list are
    removed. Groups with the ``tak_`` prefix are auto-managed by TAK Server
    integration.

    Args:
        user_id: LLDAP user ID.
        body: List of group names to assign.

    Returns:
        ``{"success": true}`` on success.

    Raises:
        HTTPException(404): If user_id doesn't exist.
    """
    ak = _get_identity()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # Enforce user type group rules — default to "user" for accounts
    # created outside the API (e.g. directly in LLDAP) that lack the attribute
    user_type = user.get("fastak_user_type", "user")
    if user_type == "svc_admin":
        raise HTTPException(400, "Admin mode service accounts cannot have groups")
    if user_type in ("user", "svc_data") and not body.groups:
        raise HTTPException(400, "Users require at least one group")

    if body.groups:
        existing_groups = ak.list_groups()
        existing_names = {g["name"] for g in existing_groups}
        missing = [g for g in body.groups if g not in existing_names]
        if missing:
            msg = f"Groups do not exist: {', '.join(missing)}. Create them first."
            raise HTTPException(400, msg)

    ak.set_user_groups(user_id, body.groups)
    return {"success": True}
