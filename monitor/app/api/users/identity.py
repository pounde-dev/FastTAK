"""LLDAP GraphQL + Proxy REST client for user and group management.

Replaces the Authentik REST client. Uses LLDAP's GraphQL API for user/group
CRUD and the ldap-proxy's REST API for enrollment token management.
"""

import hashlib
import logging
import secrets
import time
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

_RETRIES = 3
_RETRY_DELAY = 2
_TIMEOUT = 30

# LLDAP stores usernames as string IDs but the FastTAK API uses numeric IDs
# in URL paths. We hash the username to produce a stable numeric ID.
# This avoids needing a separate lookup table.


def _username_to_numeric_id(username: str) -> int:
    """Produce a stable positive integer from a username string.

    Uses SHA-256 truncated to 53 bits for determinism across process restarts
    (Python's built-in hash() is randomized). 53 bits stays within JavaScript's
    Number.MAX_SAFE_INTEGER (2^53 - 1) so JSON-consuming frontends won't
    silently truncate the value.
    """
    digest = hashlib.sha256(username.encode()).digest()
    return int.from_bytes(digest[:7], "big") & 0x1FFFFFFFFFFFFF


class IdentityClient:
    """User and group management via LLDAP GraphQL + ldap-proxy REST."""

    def __init__(
        self,
        lldap_url: str,
        proxy_url: str,
        admin_password: str,
        hidden_prefixes: list[str],
        proxy_secret: str = "",
    ):
        self.lldap_url = lldap_url.rstrip("/")
        self.proxy_url = proxy_url.rstrip("/")
        self._admin_password = admin_password
        self._proxy_secret = proxy_secret
        self.hidden_prefixes = [p.lower() for p in hidden_prefixes]
        self._client = httpx.Client(timeout=_TIMEOUT)
        self._jwt: str | None = None
        # Maps numeric ID -> LLDAP username for resolving API calls
        self._user_id_map: dict[int, str] = {}

    # ── Auth ───────────────────────────────────────────────────────

    def _login(self) -> str:
        """Authenticate to LLDAP and cache JWT token."""
        r = self._client.post(
            f"{self.lldap_url}/auth/simple/login",
            json={"username": "adm_ldapservice", "password": self._admin_password},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        self._jwt = r.json()["token"]
        return self._jwt

    def _get_token(self) -> str:
        if self._jwt is None:
            return self._login()
        return self._jwt

    # ── GraphQL ────────────────────────────────────────────────────

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query against LLDAP with retry and re-auth on 401."""
        for attempt in range(1, _RETRIES + 1):
            try:
                token = self._get_token()
                r = self._client.post(
                    f"{self.lldap_url}/api/graphql",
                    json={"query": query, "variables": variables or {}},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=_TIMEOUT,
                )
                if r.status_code == 401:
                    self._jwt = None
                    if attempt < _RETRIES:
                        continue
                r.raise_for_status()
                data = r.json()
                if data.get("errors"):
                    err_msg = str(data["errors"])
                    if "already exists" in err_msg.lower() or "duplicate" in err_msg.lower():
                        log.info("Already exists (idempotent): %s", err_msg)
                        return data.get("data", {})
                    raise RuntimeError(f"GraphQL error: {data['errors']}")
                return data.get("data", {})
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 401:
                    raise
                log.warning("GraphQL attempt %d/%d: %s", attempt, _RETRIES, exc)
                if attempt == _RETRIES:
                    raise
                time.sleep(_RETRY_DELAY)
            except httpx.HTTPError as exc:
                log.warning("GraphQL attempt %d/%d: %s", attempt, _RETRIES, exc)
                if attempt == _RETRIES:
                    raise
                time.sleep(_RETRY_DELAY)

    # ── Proxy REST ─────────────────────────────────────────────────

    def _proxy_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request against the ldap-proxy REST API."""
        url = f"{self.proxy_url}/{path.lstrip('/')}"
        kwargs.setdefault("timeout", _TIMEOUT)
        if self._proxy_secret:
            headers = kwargs.setdefault("headers", {})
            headers.setdefault("Authorization", f"Bearer {self._proxy_secret}")
        for attempt in range(1, _RETRIES + 1):
            try:
                r = self._client.request(method, url, **kwargs)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise
                log.warning("Proxy %s %s attempt %d/%d: %s", method, path, attempt, _RETRIES, exc)
                if attempt == _RETRIES:
                    raise
                time.sleep(_RETRY_DELAY)
            except httpx.HTTPError as exc:
                log.warning("Proxy %s %s attempt %d/%d: %s", method, path, attempt, _RETRIES, exc)
                if attempt == _RETRIES:
                    raise
                time.sleep(_RETRY_DELAY)

    # ── Helpers ────────────────────────────────────────────────────

    def is_hidden(self, username: str) -> bool:
        lower = username.lower()
        return any(lower.startswith(p) for p in self.hidden_prefixes)

    def _resolve_username(self, user_id: int) -> str | None:
        """Resolve numeric ID to LLDAP username. Uses cache, falls back to listing."""
        if user_id in self._user_id_map:
            return self._user_id_map[user_id]
        # Refresh cache from LLDAP
        self._refresh_user_id_map()
        return self._user_id_map.get(user_id)

    def _refresh_user_id_map(self) -> None:
        """Populate the numeric ID -> username map from LLDAP."""
        data = self._graphql(
            """
            query {
                users {
                    id
                    creationDate
                }
            }
            """
        )
        for u in data.get("users", []):
            nid = _username_to_numeric_id(u["id"])
            existing = self._user_id_map.get(nid)
            if existing is not None and existing != u["id"]:
                raise RuntimeError(
                    f"Numeric ID collision: {existing!r} and {u['id']!r} both map to {nid}. "
                    "This should be practically impossible with 53-bit hashes — please report."
                )
            self._user_id_map[nid] = u["id"]

    # Attributes managed by FastTAK (safe to read/write via insertAttributes).
    # LLDAP built-in attributes (creation_date, display_name, mail, etc.) are
    # read-only or managed through dedicated fields and must NOT be included
    # in insertAttributes mutations.
    _FASTAK_ATTRIBUTES = {
        "fastak_expires",
        "fastak_certs_revoked",
        "is_active",
        "fastak_user_type",
    }

    def _parse_attributes(self, attrs_list: list[dict]) -> dict:
        """Convert LLDAP attribute list to a flat dict.

        LLDAP attributes are [{name: str, value: [str]}].
        Only parses known FastTAK custom attributes — ignores LLDAP built-ins.
        """
        result = {}
        for attr in attrs_list:
            name = attr["name"]
            if name not in self._FASTAK_ATTRIBUTES:
                continue
            values = attr.get("value", [])
            if not values:
                continue
            if name == "fastak_expires":
                try:
                    result[name] = int(values[0])
                except (ValueError, IndexError):
                    pass
            elif name == "fastak_certs_revoked":
                result[name] = values[0].lower() == "true"
            elif name == "is_active":
                result[name] = values[0].lower() != "false"
            elif name == "fastak_user_type":
                result[name] = values[0]  # "user", "svc_data", or "svc_admin"
        return result

    def _format_user(self, u: dict) -> dict:
        """Convert LLDAP user to API response shape."""
        attrs = self._parse_attributes(u.get("attributes", []))
        groups_raw = u.get("groups", [])
        tak_groups = [
            g["displayName"][4:] for g in groups_raw if g.get("displayName", "").startswith("tak_")
        ]

        username = u["id"]
        numeric_id = _username_to_numeric_id(username)
        self._user_id_map[numeric_id] = username

        # is_active is stored as a custom attribute in LLDAP
        is_active = attrs.get("is_active", True)

        result = {
            "id": numeric_id,
            "username": username,
            "name": u.get("displayName", ""),
            "is_active": is_active,
            "groups": tak_groups,
        }
        if "fastak_expires" in attrs:
            result["fastak_expires"] = attrs["fastak_expires"]
        if "fastak_certs_revoked" in attrs:
            result["fastak_certs_revoked"] = attrs["fastak_certs_revoked"]
        if "fastak_user_type" in attrs:
            result["fastak_user_type"] = attrs["fastak_user_type"]
        return result

    def _build_custom_attributes(self, attrs: dict) -> list[dict]:
        """Convert a flat dict to LLDAP custom attribute format.

        A value of None means "clear this attribute" (set to empty list).
        """
        result = []
        for name, value in attrs.items():
            if value is None:
                result.append({"name": name, "value": []})
            elif isinstance(value, bool):
                result.append({"name": name, "value": [str(value).lower()]})
            else:
                result.append({"name": name, "value": [str(value)]})
        return result

    # ── Users ──────────────────────────────────────────────────────

    _LIST_USERS_QUERY = """
        query {
            users {
                id
                creationDate
                displayName
                attributes {
                    name
                    value
                }
                groups {
                    id
                    displayName
                }
            }
        }
    """

    def list_users(self, search: str | None = None) -> list[dict]:
        """Fetch all users, filtering out hidden prefixes.

        LLDAP's GraphQL only supports exact-match filters (no substring/wildcard),
        so search filtering is done client-side after fetching all users.
        """
        data = self._graphql(self._LIST_USERS_QUERY)
        search_lower = search.lower() if search else None
        all_users = []
        for u in data.get("users", []):
            if self.is_hidden(u["id"]):
                continue
            if search_lower:
                uid = u.get("id", "").lower()
                name = u.get("displayName", "").lower()
                if search_lower not in uid and search_lower not in name:
                    continue
            all_users.append(self._format_user(u))
        return all_users

    _GET_USER_QUERY = """
        query($id: String!) {
            user(userId: $id) {
                id
                creationDate
                displayName
                attributes {
                    name
                    value
                }
                groups {
                    id
                    displayName
                }
            }
        }
    """

    def get_user(self, user_id: int) -> dict | None:
        """Get a single user by numeric ID. Returns None for hidden or missing users."""
        username = self._resolve_username(user_id)
        if username is None:
            return None
        if self.is_hidden(username):
            return None

        data = self._graphql(self._GET_USER_QUERY, {"id": username})
        user = data.get("user")
        if user is None:
            return None
        if self.is_hidden(user["id"]):
            return None
        return self._format_user(user)

    def create_user(
        self,
        username: str,
        name: str,
        ttl_hours: int | None = None,
        groups: list[str] | None = None,
        user_type: str | None = None,
    ) -> dict:
        """Create a passwordless user."""
        self._graphql(
            """
            mutation($user: CreateUserInput!) {
                createUser(user: $user) { id creationDate }
            }
            """,
            {
                "user": {
                    "id": username,
                    "displayName": name,
                    "email": f"{username}@dummy.example.com",
                }
            },
        )

        # Set custom attributes
        attrs = {"fastak_certs_revoked": False}
        if ttl_hours is not None:
            attrs["fastak_expires"] = int(time.time() + ttl_hours * 3600)
        if user_type is not None:
            attrs["fastak_user_type"] = user_type

        custom_attrs = self._build_custom_attributes(attrs)
        self._graphql(
            """
            mutation($input: UpdateUserInput!) {
                updateUser(user: $input) { ok }
            }
            """,
            {"input": {"id": username, "insertAttributes": custom_attrs}},
        )

        # Add to groups
        if groups:
            self._ensure_user_groups(username, groups)

        # Fetch and return formatted user
        data = self._graphql(self._LIST_USERS_QUERY)
        for u in data.get("users", []):
            if u["id"] == username:
                return self._format_user(u)
        # Fallback — shouldn't happen
        return self._format_user(
            {
                "id": username,
                "displayName": name,
                "attributes": self._build_custom_attributes(attrs),
                "groups": [],
            }
        )

    def update_user(self, user_id: int, **kwargs) -> dict:
        """Update user fields. Handles reactivation, TTL changes."""
        username = self._resolve_username(user_id)
        if username is None:
            raise ValueError(f"User {user_id} not found")

        attrs_update = {}

        if "is_active" in kwargs:
            attrs_update["is_active"] = kwargs["is_active"]
            if kwargs["is_active"]:
                attrs_update["fastak_certs_revoked"] = False

        if "ttl_hours" in kwargs:
            if kwargs["ttl_hours"] is None:
                attrs_update["fastak_expires"] = None
                attrs_update["fastak_certs_revoked"] = None
            else:
                attrs_update["fastak_expires"] = int(time.time() + kwargs["ttl_hours"] * 3600)
                if "fastak_certs_revoked" not in attrs_update:
                    attrs_update["fastak_certs_revoked"] = False

        mutation_input: dict = {"id": username}

        if "name" in kwargs:
            mutation_input["displayName"] = kwargs["name"]

        if attrs_update:
            # Get existing attributes to merge
            data = self._graphql(self._GET_USER_QUERY, {"id": username})
            user = data.get("user", {})
            existing_attrs = self._parse_attributes(user.get("attributes", []))

            # Track which attrs to clear (send with empty value list)
            cleared_attrs = {}
            for k, v in attrs_update.items():
                if v is None:
                    existing_attrs.pop(k, None)
                    cleared_attrs[k] = None  # will become {"name": k, "value": []}
                else:
                    existing_attrs[k] = v

            custom = self._build_custom_attributes(existing_attrs)
            # Append cleared attributes with empty values
            for name in cleared_attrs:
                custom.append({"name": name, "value": []})
            mutation_input["insertAttributes"] = custom

        if len(mutation_input) > 1:  # more than just "id"
            self._graphql(
                """
                mutation($input: UpdateUserInput!) {
                    updateUser(user: $input) { ok }
                }
                """,
                {"input": mutation_input},
            )

        return self.get_user(user_id)

    def deactivate_user(self, user_id: int) -> None:
        """Cut every credential the user holds, then mark them inactive.

        LLDAP has no disabled state and ldap-proxy accepts a token before it
        reaches LLDAP, so stopping logins takes all three steps: the password is
        replaced with one nobody knows, groups are removed so a cert that
        outlives this keeps no access, and tokens go (DD-061).

        Every step is attempted even when an earlier one fails, because each
        closes a separate way in. is_active is written only when all three
        worked: the dashboard and the TTL job retry active users only, so a
        failure must leave the user active. Safe to repeat.

        Does NOT set fastak_certs_revoked: the caller does that after confirming
        cert revocation succeeded.
        """
        username = self._resolve_username(user_id)
        if username is None:
            raise ValueError(f"User {user_id} not found")

        steps = {
            "password": lambda: self.set_password(user_id, secrets.token_urlsafe(48)),
            "groups": lambda: self._remove_all_groups(username),
            "enrollment tokens": lambda: self.delete_enrollment_tokens(user_id),
        }
        failed = []
        for name, cut in steps.items():
            try:
                cut()
            except Exception:
                log.exception("Deactivating %s: could not cut %s", username, name)
                failed.append(name)
        if failed:
            raise RuntimeError(f"Could not cut {', '.join(failed)} for {username}")

        self._graphql(
            """
            mutation($input: UpdateUserInput!) {
                updateUser(user: $input) { ok }
            }
            """,
            {
                "input": {
                    "id": username,
                    "insertAttributes": self._build_custom_attributes({"is_active": False}),
                }
            },
        )
        # Enrollment is refused for inactive users from here on. A token minted
        # between the first delete and that write would otherwise survive.
        self.delete_enrollment_tokens(user_id)

    def get_inactive_users_with_groups(self) -> list[dict]:
        """Inactive users who still belong to a group.

        Deactivation removes every group, so these were deactivated before it
        did (or given groups since) and still hold their old access.
        """
        data = self._graphql(self._LIST_USERS_QUERY)
        leftover = []
        for u in data.get("users", []):
            attrs = self._parse_attributes(u.get("attributes", []))
            if attrs.get("is_active", True) is False and u.get("groups"):
                leftover.append(self._format_user(u))
        return leftover

    def _remove_all_groups(self, username: str) -> None:
        """Remove the user from every group, including non-tak_ ones like monitor_admin."""
        data = self._graphql(self._GET_USER_QUERY, {"id": username})
        groups = data.get("user", {}).get("groups", [])
        if groups:
            log.info(
                "Removing %s from groups: %s",
                username,
                ", ".join(g["displayName"] for g in groups),
            )
        for group in groups:
            self._graphql(
                """
                mutation($userId: String!, $groupId: Int!) {
                    removeUserFromGroup(userId: $userId, groupId: $groupId) { ok }
                }
                """,
                {"userId": username, "groupId": group["id"]},
            )

    def set_password(self, user_id: int, password: str) -> None:
        """Set user password using the lldap_set_password binary (OPAQUE protocol)."""
        import subprocess

        username = self._resolve_username(user_id)
        if username is None:
            raise ValueError(f"User {user_id} not found")

        token = self._get_token()
        cmd = [
            "/usr/local/bin/lldap_set_password",
            "--base-url",
            self.lldap_url,
            "--token",
            token,
            "--username",
            username,
            "--password",
            password,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"lldap_set_password failed for {username}: {result.stderr.strip()}"
            )

    def mark_certs_revoked(self, user_id: int) -> None:
        """Set fastak_certs_revoked: true after cert cleanup confirmed."""
        username = self._resolve_username(user_id)
        if username is None:
            raise ValueError(f"User {user_id} not found")

        # Get existing attributes and merge
        data = self._graphql(self._GET_USER_QUERY, {"id": username})
        user = data.get("user", {})
        existing_attrs = self._parse_attributes(user.get("attributes", []))
        existing_attrs["fastak_certs_revoked"] = True

        self._graphql(
            """
            mutation($input: UpdateUserInput!) {
                updateUser(user: $input) { ok }
            }
            """,
            {
                "input": {
                    "id": username,
                    "insertAttributes": self._build_custom_attributes(existing_attrs),
                }
            },
        )

    # ── Groups ─────────────────────────────────────────────────────

    def list_groups(self) -> list[dict]:
        data = self._graphql("query { groups { id displayName } }")
        groups = []
        for g in data.get("groups", []):
            name = g.get("displayName", "")
            if name.startswith("tak_") and name != "tak_ROLE_ADMIN":
                groups.append({"id": g["id"], "name": name[4:]})
        return groups

    def get_group(self, group_id: str) -> dict | None:
        # LLDAP group IDs are integers
        try:
            gid = int(group_id)
        except (ValueError, TypeError):
            return None

        try:
            data = self._graphql(
                """
                query($id: Int!) {
                    group(groupId: $id) {
                        id
                        displayName
                        users {
                            id
                            displayName
                        }
                    }
                }
                """,
                {"id": gid},
            )
        except RuntimeError as exc:
            # LLDAP answers a missing group with an error, not `group: null`.
            # Unhandled, that turned GET and DELETE of a missing group into a
            # server error instead of the 404 both routes document.
            if "entity not found" in str(exc).lower():
                return None
            raise
        g = data.get("group")
        if g is None:
            return None
        name = g.get("displayName", "")
        if not name.startswith("tak_"):
            return None
        members = [
            {"id": _username_to_numeric_id(u["id"]), "username": u["id"]}
            for u in g.get("users", [])
            if not self.is_hidden(u.get("id", ""))
        ]
        return {"id": g["id"], "name": name[4:], "members": members}

    def create_group(self, name: str) -> dict:
        tak_name = f"tak_{name}" if not name.startswith("tak_") else name
        data = self._graphql(
            """
            mutation($name: String!) {
                createGroup(name: $name) { id displayName }
            }
            """,
            {"name": tak_name},
        )
        g = data["createGroup"]
        return {"id": g["id"], "name": tak_name[4:]}

    def delete_group(self, group_id: str) -> None:
        try:
            gid = int(group_id)
        except (ValueError, TypeError):
            return
        self._graphql(
            """
            mutation($id: Int!) {
                deleteGroup(groupId: $id) { ok }
            }
            """,
            {"id": gid},
        )

    def set_user_groups(self, user_id: int, group_names: list[str]) -> None:
        """Replace user's tak_-prefixed groups. Non-tak groups untouched."""
        username = self._resolve_username(user_id)
        if username is None:
            raise ValueError(f"User {user_id} not found")

        desired_tak_names = {f"tak_{n}" if not n.startswith("tak_") else n for n in group_names}

        # Get all groups to find IDs
        all_groups = self._graphql("query { groups { id displayName } }")
        group_by_name = {g["displayName"]: g["id"] for g in all_groups.get("groups", [])}

        # Get user's current groups
        data = self._graphql(self._GET_USER_QUERY, {"id": username})
        user = data.get("user", {})
        current_groups = {g["displayName"]: g["id"] for g in user.get("groups", [])}

        # Remove tak_ groups not in desired set
        for gname, gid in current_groups.items():
            if gname.startswith("tak_") and gname not in desired_tak_names:
                self._graphql(
                    """
                    mutation($userId: String!, $groupId: Int!) {
                        removeUserFromGroup(userId: $userId, groupId: $groupId) { ok }
                    }
                    """,
                    {"userId": username, "groupId": gid},
                )

        # Add desired tak_ groups not already assigned
        for gname in desired_tak_names:
            if gname not in current_groups and gname in group_by_name:
                self._graphql(
                    """
                    mutation($userId: String!, $groupId: Int!) {
                        addUserToGroup(userId: $userId, groupId: $groupId) { ok }
                    }
                    """,
                    {"userId": username, "groupId": group_by_name[gname]},
                )

    def _ensure_user_groups(self, username: str, group_names: list[str]) -> None:
        """Add user to groups by name (creating groups if needed)."""
        all_groups = self._graphql("query { groups { id displayName } }")
        group_by_name = {g["displayName"]: g["id"] for g in all_groups.get("groups", [])}

        for name in group_names:
            tak_name = f"tak_{name}" if not name.startswith("tak_") else name
            gid = group_by_name.get(tak_name)
            if gid is None:
                # Group doesn't exist, create it
                data = self._graphql(
                    """
                    mutation($name: String!) {
                        createGroup(name: $name) { id displayName }
                    }
                    """,
                    {"name": tak_name},
                )
                gid = data["createGroup"]["id"]
            self._graphql(
                """
                mutation($userId: String!, $groupId: Int!) {
                    addUserToGroup(userId: $userId, groupId: $groupId) { ok }
                }
                """,
                {"userId": username, "groupId": gid},
            )

    # ── Enrollment tokens (via proxy REST) ─────────────────────────

    def get_or_create_enrollment_token(
        self, user_id: int, ttl_minutes: int, one_time: bool = False
    ) -> tuple[str, str]:
        """Get existing or create new enrollment token via the proxy REST API.

        If the user already has an active (non-expired) token, return it.
        Otherwise create a new one. Token DB is in tmpfs so plaintext storage
        is safe — it never hits disk.

        Returns (token_value, expires_iso).
        """
        username = self._resolve_username(user_id)
        if username is None:
            raise ValueError(f"User {user_id} not found")

        # Check for existing active tokens
        r = self._proxy_request("GET", f"/tokens/{username}")
        existing = r.json().get("tokens", [])
        if existing:
            t = existing[0]
            expires_at = t.get("expires_at")
            if expires_at:
                expires_iso = datetime.fromtimestamp(expires_at, tz=UTC).isoformat()
            else:
                expires_iso = ""
            return t["token"], expires_iso

        # No active token — create a new one
        r = self._proxy_request(
            "POST",
            "/tokens",
            json={
                "username": username,
                "ttl_minutes": ttl_minutes,
                "one_time": one_time,
            },
        )
        data = r.json()
        expires_at = data.get("expires_at")
        if expires_at:
            expires_iso = datetime.fromtimestamp(expires_at, tz=UTC).isoformat()
        else:
            expires_iso = ""
        return data["token"], expires_iso

    def delete_enrollment_tokens(self, user_id: int) -> int:
        """Delete all tokens for a user. Returns count deleted."""
        username = self._resolve_username(user_id)
        if username is None:
            return 0

        r = self._proxy_request("DELETE", f"/tokens/{username}")
        return r.json().get("deleted", 0)

    # ── TTL queries ────────────────────────────────────────────────

    def get_users_pending_expiry(self) -> list[dict]:
        """Get users with fastak_expires set and fastak_certs_revoked != true.
        Returns formatted user dicts for the TTL task."""
        data = self._graphql(self._LIST_USERS_QUERY)
        pending = []
        now = time.time()
        for u in data.get("users", []):
            attrs = self._parse_attributes(u.get("attributes", []))
            expires = attrs.get("fastak_expires")
            revoked = attrs.get("fastak_certs_revoked", False)
            if expires is not None and not revoked and expires <= now:
                pending.append(self._format_user(u))
        return pending
