# FastTAK Authentication Guide

## Overview

FastTAK supports two authentication modes:

1. **Certificate-only** — users authenticate with client certificates (.p12). No passwords, no LDAP. Simple but no centralized user management.

2. **LDAP via LLDAP** — users authenticate with username/password against LLDAP, a lightweight LDAP server. Certificates still required for TAK client connections, but web admin and monitor dashboard access use password-based login.

## How LDAP Authentication Works

The `init-identity` container configures this chain on startup:

```
TAK Server ←→ ldap-proxy ←→ LLDAP ←→ PostgreSQL (app-db)
```

### The bootstrap sequence

1. **LLDAP starts** — lightweight LDAP server backed by PostgreSQL (app-db)
2. **`init-identity` runs** — creates the LDAP infrastructure via LLDAP's GraphQL API:
   - Creates `adm_ldapservice` service account (LDAP bind user)
   - Creates `webadmin` user with password from `TAK_WEBADMIN_PASSWORD` (generated per-install by `setup.sh`; find it with `grep TAK_WEBADMIN_PASSWORD .env`)
   - Creates `tak_ROLE_ADMIN` group
   - Configures custom attribute schemas
   - Exits
3. **ldap-proxy starts** — listens on port 3389 (internal), proxies LDAP binds to LLDAP and provides `/auth/verify` for Caddy forward auth
4. **TAK Server reads CoreConfig.xml** — connects to ldap-proxy for auth

### How a user logs in (web admin on 8446)

1. User opens `https://takserver.example.com` (or `https://host:8446`)
2. TAK Server shows login form
3. User enters username + password
4. TAK Server queries ldap-proxy: `cn=<username>,ou=people,dc=takldap`
5. ldap-proxy forwards the bind to LLDAP, which validates credentials
6. If valid, TAK Server checks group membership for `tak_ROLE_ADMIN`
7. User gets admin or read-only access based on groups

### How a TAK client connects (ATAK/iTAK)

1. Client connects to port 8089 with client certificate (TLS mutual auth)
2. TAK Server validates the cert is signed by its CA
3. TAK Server looks up the cert's CN in LDAP to find group membership
4. Groups with `tak_` prefix become TAK channels (e.g., `tak_team1` → channel `team1`)
5. Group membership is cached for 30 seconds (`updateinterval="30"`)

### Important: LDAP cache delay

When a new user is created or group membership changes, TAK Server's LDAP cache takes up to 30 seconds to refresh. During this window:

- The user can connect (cert auth works immediately)
- But they may see "No channels found"
- After 30 seconds, disconnect and reconnect — channels appear

### Groups and TAK channels

| LLDAP Group      | TAK Channel    | Who sees it    |
| ---------------- | -------------- | -------------- |
| `tak_ROLE_ADMIN` | (admin access) | Admin users    |
| `tak_team1`      | `team1`        | Users in group |
| `tak_fires`      | `fires`        | Users in group |

Only groups with the `tak_` prefix appear as TAK channels. Create groups in the Monitor dashboard (or directly in LLDAP), then assign users.

### Key components

| Component         | What it does                                                           | Runs on              |
| ----------------- | ---------------------------------------------------------------------- | -------------------- |
| `lldap`           | Lightweight LDAP server (Rust), user directory, GraphQL management API | Port 3890 (internal) |
| `ldap-proxy`      | LDAP proxy with enrollment token interception, forward auth endpoint   | Port 3389 (internal) |
| `init-identity`   | One-shot bootstrap of LDAP users, groups, and schemas via GraphQL      | Exits after setup    |
| `adm_ldapservice` | Service account TAK Server uses to query LDAP                          | LLDAP user           |
| `CoreConfig.xml`  | TAK Server config with LDAP connection details                         | `/opt/tak/`          |

## Authorization: who may use the Monitor

Authentication and authorization are separate steps. Caddy's `forward_auth`
proves _who_ you are — any valid LDAP bind passes, so every TAK user clears it —
and then copies `Remote-User` and `Remote-Groups` to the monitor. The monitor
decides _what_ you may do from that group list.

Caddy removes any `Remote-User` or `Remote-Groups` the client sent before it
calls ldap-proxy, and removes `Authorization` before it calls a backend, so
backends never see your password ([DD-057](decisions.md)). If ldap-proxy cannot
read your groups, it answers 503 rather than treating you as a member of none.

The monitor is an admin console: every JSON API and every dashboard page
requires membership in `ADMIN_GROUP` (default `monitor_admin`). That covers user
and certificate management, ops operations, health telemetry, the audit event
export, and the TAK proxies. A TAK user in `tak_team1` but not in the admin
group authenticates successfully and then gets **403** on every route.

Two deliberate exceptions:

| Route                                  | Access               | Why                                                         |
| -------------------------------------- | -------------------- | ----------------------------------------------------------- |
| `/api/ping`                            | Open                 | Liveness probe; returns `{"status": "ok"}` and nothing else |
| `/api/backup/*`, `/dashboard/backups*` | `BACKUP_ADMIN_GROUP` | Lets you appoint a backup operator who is not a full admin  |

Both group names are env vars, read per request — rename a group in `.env` and
restart the stack; no rebuild, no code change. Restart the whole stack rather
than just the monitor, so `init-identity` creates the renamed group and adds
`webadmin` to it. Any other account you want to keep admin access must be added
to the new group yourself.

The monitor trusts these headers only on a request that carries
`MONITOR_PROXY_SECRET`, which Caddy adds and which only Caddy and the monitor
hold ([DD-059](decisions.md)). Any other container on the Docker network can
reach `monitor:8080`, but what it says about its identity is ignored, so it gets
**403** on everything except `/api/ping`.

## Authorization: who may use Node-RED

Node-RED runs whatever its flows contain, and its environment holds service
credentials such as the MediaMTX API password. Caddy therefore admits only
members of `NODERED_ADMIN_GROUP` (default `monitor_admin`); everyone else gets
**403** on the editor, its admin API, and anything a flow serves over HTTP
([DD-058](decisions.md)).

`init-identity` creates the group and adds `webadmin` to it. To use a separate
group, set `NODERED_ADMIN_GROUP` in `.env` and restart the stack.

## Rate Limiting

Authentication requests to `/auth/verify` (the endpoint Caddy uses for
forward_auth) are rate-limited per source IP. Only **failed** attempts
count toward the budget — successful auths don't consume it, because
Caddy's forward_auth hits this endpoint on every protected-route
request and counting successes would lock out legitimate users. See
DD-037 for the failures-only semantics.

By default, 10 failures per 5 minutes triggers a 15-minute lockout.
Tunable via `LDAP_RATE_LIMIT_*` env vars (see `.env.example`).
Exceeding the limit returns HTTP 429 with a `Retry-After` header.

Password logins through TAK Server (WebTAK and the admin UI on port 8446)
reach `ldap-proxy` from TAK Server's address, so those are counted per
**account name** instead, with the same limits
([DD-062](decisions.md)). A locked name cannot log in with a password or an
enrollment token until the lockout ends; certificate logins are unaffected.
New passwords must be at least 12 characters.

Enrollment tokens work only for enrolling a device with TAK Server. They are
not accepted as a login to the monitor or Node-RED.

**Recovering from accidental lockout:** if you trip the limit during
testing (wrong password, debugging client configs), restart
`ldap-proxy` to clear the in-memory state:

```bash
docker compose restart ldap-proxy
```
