# FastTAK

TAK ecosystem infrastructure — deployed with `docker compose up`.

## What Is This?

A Docker Compose stack for deploying and managing the TAK ecosystem:

- **TAK Server** — Official tak.gov Docker images (messaging, CA, web admin)
- **Caddy** — Automatic Let's Encrypt TLS and reverse proxy
- **MediaMTX** — RTSP/RTMP/HLS video streaming with optional recording ([guide](docs/video-recording.md))
- **LLDAP + ldap-proxy** — Lightweight LDAP authentication and user management
- **Node-RED** — Flow-based automation engine with pre-configured PostgreSQL and TAK Server connections
- **Monitor** — Management dashboard (user/group/certificate management and QR enrollment), health monitoring, and operations API

## Prerequisites

1. **Docker Engine** and **Docker Compose v2** (v2.20+) installed
2. **[just](https://github.com/casey/just)** — the command runner every workflow is documented with. Don't have it? The `justfile` is a readable map: each recipe is one line naming the script it runs, so open it and run that script directly.
3. **Official TAK Server release** ZIP from [tak.gov](https://tak.gov) — the **hardened** bundle, 5.8 or later (`takserver-docker-hardened-5.8-RELEASE-65.zip` or newer). See: [DD-051](docs/decisions.md#dd-051-tak-server-58-hardened-bundle-is-the-supported-floor).
4. **DNS** (subdomain mode only) — required for Let's Encrypt TLS and subdomain routing. Your FQDN and subdomains must resolve to the host's public IP. Not needed for direct mode.

## Deployment Modes

FastTAK supports two deployment modes, controlled by `DEPLOY_MODE` in `.env`:

**`direct`** — Port-based routing through Caddy with self-signed TLS. No DNS needed. Each service gets its own port (e.g., `https://192.168.1.50:8446`). Good for field deployments, air-gapped networks, and getting started quickly.

**`subdomain`** — Subdomain-based routing through Caddy with automatic Let's Encrypt TLS. Requires public DNS. Each service gets a subdomain (e.g., `https://monitor.tak.example.com`).

## Quick Start

```bash
git clone https://github.com/pounde/FastTAK.git FastTAK && cd FastTAK

# One-time setup (builds images, extracts tak/, generates secrets)
just setup takserver-docker-hardened-5.8-RELEASE-65.zip

# Set SERVER_ADDRESS to your IP or hostname, pick a DEPLOY_MODE
vim .env

# Start
just up
```

`just setup` extracts the TAK Server release, builds Docker images, creates `.env` with generated secrets. You only run it once (or again to upgrade).

`just up` brings up the stack, waits for healthy, verifies it, and shows connection info.
`just doctor` re-runs those checks against the running stack without touching it, and lists the ports this host publishes.

The fastest path to a working stack is `DEPLOY_MODE=direct` with `SERVER_ADDRESS` set to your machine's IP address — no DNS required.

For the full end-to-end walkthrough (user enrollment, video streaming), see [docs/quickstart-walkthrough.md](docs/quickstart-walkthrough.md).

### What `setup.sh` does

1. Verifies the bundle is the hardened one and at or above the 5.8 floor
2. Extracts `tak/`, strips the vendor's build-time certs, and fixes the file
   modes the release ZIP ships (nothing in it is executable)
3. Copies FastTAK's `healthcheck.sh` and `register-api-cert.sh` into `tak/`
4. Builds `takserver` and `takserver-database` from the bundle's **hardened**
   Dockerfiles
5. Creates `.env` from `.env.example` and generates every required secret

The README used to document these as manual `unzip` + `docker build` steps
against `Dockerfile.takserver-db`. That path bypassed the version floor and
reproduced the un-persisted-`PGDATA` defect DD-051 exists to prevent, and the
database entrypoint now refuses to start on a `PGDATA` that is not a mount
point — so it no longer works at all.

### Changing configuration

Edit `.env` and re-run the init containers to apply changes — no need to stop TAK Server:

```bash
just up init-config init-identity
```

This re-runs the configuration and identity bootstrap containers, which read from `.env` and patch the running services. If you're running the monitor dashboard, it will detect `.env` changes and show the command to run.

For version changes or Dockerfile modifications, a full rebuild is needed:

```bash
docker compose up -d --build
```

> [!CAUTION]
> `docker compose down -v` destroys all database data (PostgreSQL volumes). Use it only for a full reset. Certs and config in `./tak/` are always preserved.

## Services

All services start together — TAK Server, Caddy, MediaMTX, LLDAP (LDAP authentication), Node-RED, Monitor. Init containers handle configuration before TAK Server starts — no restart needed.

## Monitor

FastTAK includes a monitoring service with two components:

**API** (`/api/*`) — JSON endpoints for health checks, operations, and alerts. Use this to integrate with external tools, scripts, or your own dashboards. API documentation is available at `/api/docs` (Swagger UI, admin group only). The Swagger page loads its scripts from a public CDN, so on an air-gapped deployment it renders blank while `/api/openapi.json` still works — see [#119](https://github.com/pounde/FastTAK/issues/119).

- `GET /api/health/containers` — container health status for all services
- `GET /api/health/resources` — CPU/memory stats per container
- `GET /api/health/certs` — TAK certificate expiry
- `GET /api/health/tls` — TLS (Let's Encrypt) certificate expiry
- `GET /api/health/database` — CoT database size
- `GET /api/health/disk` — filesystem usage
- `GET /api/health/updates` — available version updates
- `GET /api/health/config` — configuration drift detection
- `GET /api/ops/certs/list` — list all certificates
- `POST /api/ops/certs/create-client/{name}` — create client certificate
- `POST /api/ops/certs/create-server/{name}` — create server certificate
- `POST /api/ops/certs/revoke/{name}` — revoke a certificate
- `GET /api/ops/service/{name}/logs` — view container logs
- `POST /api/ops/database/vacuum` — database maintenance
- `POST /api/ops/alerts/test-email` — test email alerting
- `POST /api/ops/alerts/test-sms` — test SMS alerting

**Dashboard** — A web UI built on top of the API. Auto-refreshing health grid, certificate status, update notifications, disk usage, activity log, and an operations page for cert management and database maintenance.

Access the dashboard at `https://<SERVER_ADDRESS>:8180` (direct mode) or `https://monitor.<SERVER_ADDRESS>` (subdomain mode). Both use LDAP authentication via Caddy.

## Configuration

All configuration lives in a single `.env` file. See `.env.example` for the full reference.

### Required variables

| Variable                | Description                                                                                                                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SERVER_ADDRESS`        | IP, hostname, or FQDN that clients use to reach this server (e.g. `192.168.1.50`)                                                                                                        |
| `DEPLOY_MODE`           | `direct` (port-based, self-signed TLS) or `subdomain` (DNS-based, Let's Encrypt TLS)                                                                                                     |
| `TAK_WEBADMIN_PASSWORD` | Password for the `webadmin` account — auto-generated by `setup.sh`, stored in `.env` (run `grep TAK_WEBADMIN_PASSWORD .env` to retrieve it). Leave empty to skip webadmin user creation. |

Additional optional variables (SMTP relay, LDAP base DN, admin email) are documented in `.env.example`.

### Version pins

| Variable           | Default  | Description                   |
| ------------------ | -------- | ----------------------------- |
| `TAK_VERSION`      | `5.8-RELEASE-65` | TAK Server Docker image tag, set by `setup.sh` |
| `LLDAP_VERSION`    | `v0.6.1` | LLDAP lightweight LDAP server |
| `MEDIAMTX_VERSION` | `1.21.1` | MediaMTX video streaming      |
| `NODERED_VERSION`  | `4.1`    | Node-RED                      |

### Port assignments (direct mode)

In direct mode, each service gets its own port on Caddy with self-signed TLS:

| Variable               | Default | Service              |
| ---------------------- | ------- | -------------------- |
| `TAKSERVER_ADMIN_PORT` | `8446`  | TAK Server web admin |
| `MEDIAMTX_PORT`        | `8888`  | MediaMTX streaming   |
| `NODERED_PORT`         | `1880`  | Node-RED             |
| `MONITOR_PORT`         | `8180`  | Monitor dashboard    |

In direct mode, the bare `https://<SERVER_ADDRESS>` (port 443) redirects to the Monitor dashboard. Ignored in subdomain mode.

### Subdomains (subdomain mode)

In subdomain mode, each service gets a configurable subdomain:

| Variable              | Default     | Routes to            |
| --------------------- | ----------- | -------------------- |
| `TAKSERVER_SUBDOMAIN` | `takserver` | TAK Server web admin |
| `MEDIAMTX_SUBDOMAIN`  | `stream`    | MediaMTX streaming   |
| `NODERED_SUBDOMAIN`   | `nodered`   | Node-RED             |
| `MONITOR_SUBDOMAIN`   | `monitor`   | Monitor dashboard    |

Ignored in direct mode. The Caddyfile is generated by `init-config` based on the deploy mode — changing variables requires re-running the init containers.

### Internal secrets

Generated by `setup.sh` — users don't interact with these directly. If not using `setup.sh`, generate with the noted commands.

| Variable             | Description                                                                   |
| -------------------- | ----------------------------------------------------------------------------- |
| `TAK_DB_PASSWORD`    | TAK database password (`openssl rand -hex 16`)                                |
| `APP_DB_PASSWORD`    | App database password — shared by LLDAP and Node-RED (`openssl rand -hex 16`) |
| `LDAP_BIND_PASSWORD` | LDAP service account password (`openssl rand -hex 16`)                        |

## Ports

### Always exposed (both modes)

| Port | Service    | Protocol                                            | Auth        |
| ---- | ---------- | --------------------------------------------------- | ----------- |
| 80   | Caddy      | HTTP (redirect to HTTPS)                            | —           |
| 443  | Caddy      | HTTPS — redirects to Monitor (direct) or subdomains | LDAP        |
| 443/udp | Caddy  | HTTP/3 (QUIC)                                       | LDAP        |
| 8089 | TAK Server | CoT over TLS                                        | Client cert |
| 8443 | TAK Server | Client-cert HTTPS (mutual TLS)                      | Client cert |
| 8554 | MediaMTX   | RTSP — video ingress from cameras, drones           | None        |
| 1935 | MediaMTX   | RTMP — video ingress from OBS, encoders             | None        |

### Direct mode additional ports

In direct mode, Caddy also listens on per-service ports (configurable in `.env`):

| Port (default) | Service              | Auth |
| -------------- | -------------------- | ---- |
| 8446           | TAK Server web admin | LDAP |
| 8888           | MediaMTX HLS         | LDAP |
| 1880           | Node-RED             | LDAP |
| 8180           | Monitor dashboard    | LDAP |

### Subdomain mode routing

In subdomain mode, all services route through Caddy on port 443 via subdomains.

### Internal only (not bound to host)

| Service      | Container Port | Purpose                                        |
| ------------ | -------------- | ---------------------------------------------- |
| tak-database | 5432           | TAK PostgreSQL (CoT data)                      |
| app-db       | 5432           | App PostgreSQL (LLDAP + Node-RED)              |
| lldap        | 3890           | LDAP server + GraphQL API (internal)           |
| ldap-proxy   | 3389           | LDAP proxy (TAK Server → LLDAP) + forward auth |

Ports 8089 and 8443 are direct TAK client connections — they bypass Caddy because TAK clients use mutual TLS with client certificates.

## Certificate Management

Use `certs.sh` for certificate operations:

```bash
./certs.sh list                          # List all certs
./certs.sh create-client alice           # Create client cert
./certs.sh download alice.p12            # Download .p12 to host
./certs.sh create-server my.domain.com   # Create server cert for hostname
./certs.sh ca-info                       # Show CA details + expiry
./certs.sh create-user webadmin 'Pass!'  # Create TAK admin user
./certs.sh revoke alice                  # Revoke a certificate
```

Run `./certs.sh help` for the full reference. Some certificate operations are also available via the monitor API and dashboard.

Certificate files are also directly accessible on the host at `./tak/certs/files/`.

For detailed information about how TAK certificates work, see [docs/certificates.md](docs/certificates.md).

## User Management

The Monitor dashboard is the primary interface for managing users, groups, and certificate enrollment. Access it at `https://<SERVER_ADDRESS>:8180` (direct mode) or `https://monitor.<SERVER_ADDRESS>` (subdomain mode), logging in as `webadmin`.

### Enrollment flow

1. Create a user in the Monitor dashboard (Users → New User)
2. Assign groups — groups prefixed with `tak_` become TAK channels
3. Click the Enroll button next to the user
4. A QR code (and a `tak://` enrollment URL) appears with a ~15-minute token
5. User scans the QR code with ATAK/iTAK/TAK Aware
6. The TAK client enrolls directly with TAK Server and receives its certificate

For details on authentication flows and LDAP, see [docs/authentication.md](docs/authentication.md).

## Node-RED

Node-RED is available at `https://<SERVER_ADDRESS>:1880` (direct mode) or `https://nodered.<SERVER_ADDRESS>` (subdomain mode).

On first boot, FastTAK pre-installs `node-red-contrib-postgresql` and `node-red-contrib-tak`, and configures a PostgreSQL connection to `app-db`. Flows needing spatial queries should point at `tak-database` instead — it has PostGIS natively. A `nodered` LDAP user is automatically created in the `tak_ROLE_ADMIN` group so CoT messages from Node-RED flows reach all TAK clients.

## Updating

### FastTAK updates

New FastTAK code on the same TAK Server release:

```bash
git pull
just up
```

`just up` rebuilds the images FastTAK builds itself (the monitor is built from
source, so this is what carries new code into the running container), removes
containers for services that no longer exist in the compose file, and provisions
any secret a newer release requires but your `.env` does not have yet. The
databases are untouched.

See [docs/upgrading.md](docs/upgrading.md) for the parts that are not automatic
— chiefly the Monitor's admin-group gate, which can lock you out if the account
you log in with is not in `ADMIN_GROUP`.

### Component updates (LLDAP, MediaMTX, Node-RED)

Edit the version pin in `.env`, then pull and restart:

```bash
docker compose pull
just up
```

### TAK Server updates

TAK Server images are built locally from the tak.gov release ZIP. `just setup`
handles extraction, image builds, and updating `TAK_VERSION` in `.env`.

**Moving an existing deployment to a new TAK Server release is not yet
supported.** Nothing carries the databases across a version change. Back up
first, and read [docs/upgrading.md](docs/upgrading.md) before you start.

## Testing

See [CONTRIBUTING.md](CONTRIBUTING.md#testing). The fast suite is `just test`;
the full integration cycle against an isolated stack is `just test-stack cycle`.

## Stopping and Teardown

```bash
# Stop services (preserves databases and ./tak/ .env config)
# 5.8 is the supported floor, and its hardened image lands PGDATA in the mounted
# volume — earlier releases kept cot inside the container, where `down` destroyed
# it. See docs/decisions.md DD-051.
just down

# Full reset (destroys database data, keeps ./tak/ certs and .env config)
docker compose down -v

# Complete wipe (including certs and .env config)
docker compose down -v && rm -rf tak/ .env
```

`./tak/` is a bind-mount — certs, CoreConfig.xml, and logs always survive `down`. Only named volumes (PostgreSQL data) are removed with `-v`.

## Resource Limits

### Memory

Each service has a memory cap enforced in `docker-compose.yml` (via
`deploy.resources.limits.memory`). Starting points below — if your
deployment has larger needs, override per-service via a
`docker-compose.override.yml`.

| Service           | Cap    | Notes                                   |
| ----------------- | ------ | --------------------------------------- |
| `tak-server`      | 4 GB   | JVM heap; scales with connected clients |
| `tak-database`    | 2 GB   | PostgreSQL on write-heavy CoT workload  |
| `app-db`          | 1 GB   | PostgreSQL shared by LLDAP + Node-RED   |
| `nodered`         | 512 MB | Node-RED runtime                        |
| `monitor`         | 512 MB | FastAPI management API                  |
| `mediamtx`        | 512 MB | Scales with concurrent streams          |
| `lldap`           | 256 MB | Rust LDAP server                        |
| `caddy`           | 256 MB | Reverse proxy                           |
| `ldap-proxy`      | 128 MB | Go LDAP bind proxy                      |
| `init-config`     | 128 MB | One-shot bash (exits after bootstrap)   |
| `init-identity`   | 256 MB | One-shot Python                         |
| `init-ldap-ready` | 64 MB  | One-shot LDAP bind probe                |

See DD-034 for rationale.

## Troubleshooting

**Services not starting?**

```bash
docker compose ps                           # check status
docker compose logs -f                      # follow all logs
docker compose logs tak-server --tail 50    # specific service
```

**TAK Server not healthy after 5 minutes?**

```bash
docker compose logs tak-server | tail -30
```

**Identity bootstrap failed?**

```bash
docker compose logs init-identity
```

**QR enrollment not working?**

- Ensure TAK Server is healthy: `docker compose ps tak-server`
- Check Monitor logs: `docker compose logs monitor`
- Verify `SERVER_ADDRESS` is reachable from the client device
- Enrollment tokens expire after 15 minutes — generate a fresh QR

**Certificate issues?**

```bash
./certs.sh ca-info    # check CA cert expiry
```

Caddy auto-manages Let's Encrypt certs. TAK Server internal CA cert expiry is monitored by the healthcheck — the container becomes `unhealthy` when any cert is within 30 days of expiring. The monitor dashboard also tracks cert expiry across all TAK certificates.
