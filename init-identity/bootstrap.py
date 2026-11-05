#!/usr/bin/env python3
"""Bootstrap LLDAP with users, groups, and service accounts for FastTAK.

Runs as a one-shot init container after LLDAP is healthy. Creates default
groups, optionally creates a webadmin user, and creates passwordless service
accounts.

Idempotent — safe to run multiple times.

Environment variables:
    LLDAP_URL              — LLDAP HTTP URL (default: http://lldap:17170)
    LDAP_ADMIN_PASSWORD    — admin bind password (required)
    TAK_WEBADMIN_PASSWORD  — password for webadmin user (optional)
    LDAP_BASE_DN           — LDAP base DN (default: DC=takldap)
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request

log = logging.getLogger("init-identity")

# --- Configuration ---
LLDAP_URL = os.environ.get("LLDAP_URL", "http://lldap:17170").rstrip("/")
ADMIN_USER = os.environ.get("LLDAP_ADMIN_USER", "adm_ldapservice")
ADMIN_PASS = os.environ["LDAP_ADMIN_PASSWORD"]
BASE_DN = os.environ.get("LDAP_BASE_DN", "DC=takldap").strip() or "DC=takldap"

WEBADMIN_PASS = os.environ.get("TAK_WEBADMIN_PASSWORD", "").strip()

SERVICE_ACCOUNTS = ["svc_fasttakapi"]


def _group_from_env(var: str, default: str = "monitor_admin") -> str:
    """Read a gate group name from env, falling back to the default."""
    return os.environ.get(var, default).strip() or default


# Gate groups. All are read from env so an operator who renames
# one (via .env) gets the renamed group bootstrapped and webadmin auto-joined,
# rather than being locked out with a 403.
#
# ADMIN_GROUP gates every JSON API and dashboard page (DD-047); BACKUP_ADMIN_GROUP
# gates the backup surfaces only, so a backup operator need not be a full admin.
# NODERED_ADMIN_GROUP gates the Node-RED route in Caddy (DD-058).
# They default to the same group. All MUST be bootstrapped: without ADMIN_GROUP
# here, renaming it produces a group that nothing creates and nobody joins, which
# 403s every route on the monitor — including for webadmin, with no way back in
# through the UI.
ADMIN_GROUP = _group_from_env("ADMIN_GROUP")
BACKUP_ADMIN_GROUP = _group_from_env("BACKUP_ADMIN_GROUP")
NODERED_ADMIN_GROUP = _group_from_env("NODERED_ADMIN_GROUP")

# dict.fromkeys de-duplicates while preserving order, for the common case where
# all three names are "monitor_admin".
GATE_GROUPS = list(dict.fromkeys([ADMIN_GROUP, BACKUP_ADMIN_GROUP, NODERED_ADMIN_GROUP]))
DEFAULT_GROUPS = ["tak_ROLE_ADMIN", *GATE_GROUPS]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def lldap_login(base_url, username, password, retries=60, delay=5):
    """Authenticate to LLDAP and return JWT token. Retries until LLDAP is ready."""
    url = f"{base_url}/auth/simple/login"
    body = json.dumps({"username": username, "password": password}).encode()
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["token"]
        except Exception as e:
            if attempt < retries:
                log.info("Waiting for LLDAP (%d/%d): %s", attempt, retries, e)
                time.sleep(delay)
            else:
                raise SystemExit(f"LLDAP not ready after {retries} attempts") from e


def graphql(base_url, token, query, variables=None):
    """Execute a GraphQL query against LLDAP."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        f"{base_url}/api/graphql",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if data.get("errors"):
        err_msg = str(data["errors"])
        # Treat "already exists" / "duplicate" as idempotent success
        if "already exists" in err_msg.lower() or "duplicate" in err_msg.lower():
            log.info("Already exists (idempotent): %s", err_msg)
            return data.get("data")
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data.get("data")


# ---------------------------------------------------------------------------
# Ensure helpers
# ---------------------------------------------------------------------------


def ensure_group(base_url, token, name):
    """Create group if it doesn't exist. Returns group ID (int)."""
    data = graphql(base_url, token, "query { groups { id displayName } }")
    for g in (data or {}).get("groups", []):
        if g["displayName"] == name:
            log.info("Group '%s' exists (id=%s)", name, g["id"])
            return g["id"]

    data = graphql(
        base_url,
        token,
        """
        mutation($name: String!) {
            createGroup(name: $name) { id displayName }
        }
        """,
        {"name": name},
    )
    gid = data["createGroup"]["id"]
    log.info("Created group '%s' (id=%s)", name, gid)
    return gid


def ensure_user(base_url, token, username, display_name):
    """Create user if it doesn't exist. Returns user ID (string username)."""
    # Query for existing user — LLDAP returns an error if user doesn't exist
    try:
        data = graphql(
            base_url,
            token,
            """
            query($id: String!) { user(userId: $id) { id displayName } }
            """,
            {"id": username},
        )
        if data and data.get("user"):
            log.info("User '%s' exists", username)
            return data["user"]["id"]
    except RuntimeError:
        pass  # User doesn't exist, create below

    data = graphql(
        base_url,
        token,
        """
        mutation($user: CreateUserInput!) {
            createUser(user: $user) { id displayName }
        }
        """,
        {
            "user": {
                "id": username,
                "displayName": display_name,
                "email": f"{username}@dummy.example.com",
            }
        },
    )
    if data and data.get("createUser"):
        uid = data["createUser"]["id"]
        log.info("Created user '%s'", uid)
        return uid
    # Idempotent: user was created by a concurrent run or already-exists was swallowed
    log.info("User '%s' already exists (idempotent)", username)
    return username


def set_password(base_url, token, username, password):
    """Set a user's password using the lldap_set_password binary."""
    cmd = [
        "/usr/local/bin/lldap_set_password",
        "--base-url",
        base_url,
        "--token",
        token,
        "--username",
        username,
        "--password",
        password,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"lldap_set_password failed for {username}: {result.stderr.strip()}")
    log.info(f"Set password for '{username}'")


def add_to_group(base_url, token, user_id, group_id):
    """Add user to group (idempotent).

    Args:
        user_id: String username (LLDAP GraphQL userId is String!)
        group_id: Int group ID (LLDAP GraphQL groupId is Int!)
    """
    graphql(
        base_url,
        token,
        """
        mutation($userId: String!, $groupId: Int!) {
            addUserToGroup(userId: $userId, groupId: $groupId) { ok }
        }
        """,
        {"userId": user_id, "groupId": group_id},
    )
    log.info("User '%s' added to group %s", user_id, group_id)


def set_user_attribute(base_url, token, username, attr_name, attr_value):
    """Set a custom attribute on a user (idempotent)."""
    graphql(
        base_url,
        token,
        """
        mutation($input: UpdateUserInput!) {
            updateUser(user: $input) { ok }
        }
        """,
        {
            "input": {
                "id": username,
                "insertAttributes": [{"name": attr_name, "value": [str(attr_value)]}],
            }
        },
    )
    log.info("Set %s=%s on '%s'", attr_name, attr_value, username)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def ensure_custom_attributes(base_url, token):
    """Register custom user attribute schemas required by FastTAK."""
    attrs = [
        ("fastak_expires", "INTEGER"),
        ("fastak_certs_revoked", "STRING"),
        ("is_active", "STRING"),
        ("fastak_user_type", "STRING"),
    ]
    for attr_name, attr_type in attrs:
        try:
            graphql(
                base_url,
                token,
                """
                mutation($name: String!, $attributeType: AttributeType!,
                         $isList: Boolean!, $isVisible: Boolean!, $isEditable: Boolean!) {
                    addUserAttribute(name: $name, attributeType: $attributeType,
                                     isList: $isList, isVisible: $isVisible,
                                     isEditable: $isEditable) { ok }
                }
                """,
                {
                    "name": attr_name,
                    "attributeType": attr_type,
                    "isList": False,
                    "isVisible": True,
                    "isEditable": True,
                },
            )
            log.info("Registered attribute schema '%s' (%s)", attr_name, attr_type)
        except RuntimeError as e:
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                log.info("Attribute schema '%s' already exists", attr_name)
            else:
                raise


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("Bootstrapping LLDAP at %s", LLDAP_URL)

    token = lldap_login(LLDAP_URL, ADMIN_USER, ADMIN_PASS)
    log.info("Authenticated to LLDAP")

    # Register custom attribute schemas
    ensure_custom_attributes(LLDAP_URL, token)

    # Ensure default groups
    group_ids = {}
    for group_name in DEFAULT_GROUPS:
        group_ids[group_name] = ensure_group(LLDAP_URL, token, group_name)

    # Webadmin user (optional)
    if WEBADMIN_PASS:
        webadmin_id = ensure_user(LLDAP_URL, token, "webadmin", "Web Admin")
        set_password(LLDAP_URL, token, "webadmin", WEBADMIN_PASS)
        add_to_group(LLDAP_URL, token, webadmin_id, group_ids["tak_ROLE_ADMIN"])
        for gate_group in GATE_GROUPS:
            add_to_group(LLDAP_URL, token, webadmin_id, group_ids[gate_group])
        set_user_attribute(LLDAP_URL, token, "webadmin", "fastak_user_type", "user")
    else:
        log.info("No TAK_WEBADMIN_PASSWORD set, skipping webadmin user")

    # Service accounts (passwordless — they auth via client certs)
    for svc_name in SERVICE_ACCOUNTS:
        ensure_user(LLDAP_URL, token, svc_name, svc_name)

    # Set user types
    set_user_attribute(LLDAP_URL, token, "svc_fasttakapi", "fastak_user_type", "svc_admin")

    log.info("Bootstrap complete")


if __name__ == "__main__":
    main()
