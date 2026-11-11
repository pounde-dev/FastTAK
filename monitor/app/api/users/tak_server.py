"""TAK Server REST API client for cert and group operations via mTLS."""

import atexit
import logging
import os
import ssl
import tempfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 30


class TakServerClient:
    """Client for TAK Server's certadmin and groups API on port 8443."""

    def __init__(self, base_url: str, cert_path: str, cert_password: str):
        self.base_url = base_url.rstrip("/")
        self.cert_path = cert_path
        self.cert_password = cert_password
        self._ssl_context: ssl.SSLContext | None = None
        self._cert_pem_path: str | None = None
        self._key_pem_path: str | None = None
        self._client: httpx.Client | None = None
        self._init_ssl()

    def _init_ssl(self) -> None:
        """Extract PEM from .p12 once and create reusable SSL context and client."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            pkcs12,
        )

        p12_path = Path(self.cert_path)
        if not p12_path.exists():
            log.warning("TAK API cert not found at %s", self.cert_path)
            return
        # The deployment CA sits beside the p12 in tak/certs/files. Without it
        # there is no way to verify the server, so build no client at all —
        # falling back to unverified TLS would be the silent failure #57 names.
        ca_path = p12_path.parent / "ca.pem"
        if not ca_path.exists():
            log.error("TAK CA not found at %s; refusing an unverified TLS client", ca_path)
            return

        with open(p12_path, "rb") as f:
            p12_data = f.read()

        private_key, certificate, chain = pkcs12.load_key_and_certificates(
            p12_data, self.cert_password.encode()
        )

        # Write PEM files (persist for lifetime of this client, cleaned up on exit)
        cert_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
        cert_file.write(certificate.public_bytes(Encoding.PEM))
        if chain:
            for c in chain:
                cert_file.write(c.public_bytes(Encoding.PEM))
        cert_file.close()
        self._cert_pem_path = cert_file.name
        os.chmod(self._cert_pem_path, 0o600)

        key_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
        key_file.write(
            private_key.private_bytes(
                Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
            )
        )
        key_file.close()
        self._key_pem_path = key_file.name
        os.chmod(self._key_pem_path, 0o600)

        # Register cleanup so temp files are removed when the process exits
        atexit.register(self.close)

        # Pin the deployment CA: the chain must verify, so nothing else on the
        # Docker network can impersonate TAK Server to this admin credential.
        # The hostname check stays off — the server cert is issued for
        # SERVER_ADDRESS, and this client reaches it as tak-server.
        ctx = ssl.create_default_context(cafile=str(ca_path))
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        # ca.pem is TAK's intermediate. Python 3.13 anchors on a trusted
        # intermediate by default; the image runs 3.12, so ask for it.
        ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        ctx.load_cert_chain(self._cert_pem_path, self._key_pem_path)
        self._ssl_context = ctx

        self._client = httpx.Client(verify=self._ssl_context, timeout=_TIMEOUT)

    def close(self) -> None:
        """Close the HTTP client and delete temp PEM files."""
        if self._client is not None:
            self._client.close()
            self._client = None
        for path in (self._cert_pem_path, self._key_pem_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self._cert_pem_path = None
        self._key_pem_path = None

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self._client:
            raise RuntimeError("TAK Server client not initialized — check cert path")
        r = self._client.get(f"{self.base_url}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        if not self._client:
            raise RuntimeError("TAK Server client not initialized — check cert path")
        r = self._client.delete(f"{self.base_url}{path}")
        r.raise_for_status()

    def list_user_certs(self, username: str) -> list[dict]:
        """List certs for a user by CN match."""
        try:
            return self._fetch_user_certs(username)
        except httpx.HTTPError:
            log.warning("Failed to query certs for %s", username)
            return []

    def _fetch_user_certs(self, username: str) -> list[dict]:
        """List certs, letting a failure raise: an empty list must mean no certs."""
        data = self._get("/Marti/api/certadmin/cert", params={"username": username})
        return [
            {
                "id": c["id"],
                "hash": c.get("hash", ""),
                "certificate_pem": c.get("certificate", ""),
                "serial_number": c.get("serialNumber", ""),
                "issuance_date": c.get("issuanceDate"),
                "expiration_date": c.get("expirationDate"),
                "revocation_date": c.get("revocationDate"),
            }
            for c in data.get("data", [])
        ]

    def revoke_cert(self, cert_id: int) -> bool:
        """Mark a cert as revoked in certadmin (database flag only).

        NOTE: This does NOT update the CRL. Use revoke_cert_via_crl() for
        actual TLS-level revocation that disconnects devices.
        """
        try:
            self._delete(f"/Marti/api/certadmin/cert/revoke/{cert_id}")
            return True
        except httpx.HTTPError as exc:
            log.error("Failed to revoke cert %d: %s", cert_id, exc)
            return False

    def revoke_all_user_certs(self, username: str) -> bool:
        """Revoke all active certs for a user via CRL (not certadmin DB flag).

        Covers two cert sources:
        1. On-disk .pem files owned by this user (monitor-generated)
        2. Certs in TAK certadmin with no on-disk copy (QR-enrolled) — PEM is
           extracted from the certadmin response and fed to CRL revocation.

        CRL is regenerated so TAK Server rejects revoked certs on next connect.
        Returns True only if every cert was successfully revoked.
        """
        from app.api.service_accounts.cert_gen import (
            revoke_cert_by_pem,
            revoke_certs_on_disk_for_user,
        )

        all_ok = True

        disk_result = revoke_certs_on_disk_for_user(username)
        if not disk_result["success"]:
            all_ok = False
            for err in disk_result.get("errors", []):
                log.error("On-disk cert revocation failed for %s: %s", username, err)

        try:
            certs = self._fetch_user_certs(username)
        except httpx.HTTPError as exc:
            log.error("Cannot list certs for %s, so cannot confirm revocation: %s", username, exc)
            return False

        for cert in certs:
            if cert.get("revocation_date") is not None:
                continue
            pem = cert.get("certificate_pem")
            if not pem:
                log.warning(
                    "certadmin cert %s for %s has no PEM — cannot CRL-revoke",
                    cert.get("id"),
                    username,
                )
                all_ok = False
                continue
            result = revoke_cert_by_pem(pem)
            if not result["success"]:
                log.error(
                    "PEM cert revocation failed for %s cert %s: %s",
                    username,
                    cert.get("id"),
                    result.get("error", ""),
                )
                all_ok = False
                continue
            # The CRL is what stops the cert. This flag keeps the dashboard honest,
            # so a failure is logged by revoke_cert and picked up by any retry.
            self.revoke_cert(cert["id"])

        return all_ok

    def list_groups(self) -> list[dict]:
        """List all groups from TAK Server."""
        try:
            data = self._get("/Marti/api/groups/all", params={"useCache": "false"})
            return data.get("data", [])
        except httpx.HTTPError:
            log.warning("Failed to query TAK Server groups")
            return []

    def list_clients(self) -> list[dict]:
        """List currently connected TAK clients (/Marti/api/subscriptions/all).

        Each entry has callsign, uid (normalised from TAK Server's ``clientUid``),
        group memberships (list of group dicts), team, last-report time, and
        TAK client/version metadata. Returns ``[]`` on any HTTP failure.

        Endpoint note: TAK Server 5.x removed ``/Marti/api/Subscription/GetAllRepeaters``;
        ``subscriptions/all`` is its successor and returns the richest data set
        (team, takClient, takVersion, structured group dicts) needed by the
        Connected Clients dashboard and the upcoming agency filter (#21).
        """
        try:
            data = self._get("/Marti/api/subscriptions/all")
            entries = data.get("data", [])
            # Normalise clientUid -> uid so callers don't need to special-case
            # the TAK Server quirk. Mutate in place; entries are throw-away dicts.
            for e in entries:
                if "clientUid" in e and "uid" not in e:
                    e["uid"] = e["clientUid"]
            return entries
        except httpx.HTTPError:
            log.warning("Failed to query TAK Server clients")
            return []

    def list_contacts(self) -> list[dict]:
        """List the TAK Server contact roster (/Marti/api/contacts/all).

        TAK versions differ on the response shape: some return a bare list,
        others wrap in {"data": [...]}. Both are handled.
        """
        try:
            data = self._get("/Marti/api/contacts/all")
            if isinstance(data, list):
                return data
            return data.get("data", [])
        except httpx.HTTPError:
            log.warning("Failed to query TAK Server contacts")
            return []

    def list_missions(self) -> list[dict]:
        """List TAK Server missions (/Marti/api/missions)."""
        try:
            data = self._get("/Marti/api/missions")
            return data.get("data", [])
        except httpx.HTTPError:
            log.warning("Failed to query TAK Server missions")
            return []
