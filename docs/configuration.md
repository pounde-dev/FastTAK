# FastTAK Configuration

FastTAK configuration is spread across a few files, each with a distinct purpose.

## Deployment Modes

FastTAK supports two deployment modes controlled by `DEPLOY_MODE` in `.env`.

### `SERVER_ADDRESS`

The address clients and services use to reach this deployment. Can be an IP address, a Tailscale hostname, or a fully qualified domain name (FQDN):

```
SERVER_ADDRESS=192.168.1.10
SERVER_ADDRESS=my-node.tail1234.ts.net
SERVER_ADDRESS=tak.example.com
```

### `DEPLOY_MODE=direct`

**Use when:** you don't have public DNS — air-gapped networks, Tailscale, local IP, or any address that isn't resolvable by Let's Encrypt.

- Caddy serves HTTPS on a single port, using its internal CA (self-signed)
- Services are routed by port number (see port variables below)
- No DNS required — works with IPs and private hostnames
- TLS is still enforced; browsers will show an untrusted certificate warning on first visit

**Self-signed certificate warnings:** See [Self-signed certificate warnings](certificates.md#self-signed-certificate-warnings) in the certificate guide for how to suppress browser warnings.

**TAK enrollment:** Some TAK client versions do not support IP-only enrollment URLs — they expect an FQDN. This is a platform limitation, not a FastTAK issue.

### `DEPLOY_MODE=subdomain`

**Use when:** you have a public FQDN with DNS that Let's Encrypt can resolve.

- Caddy terminates HTTPS using Let's Encrypt certificates (automatic)
- Services are routed by subdomain (e.g., `tak.example.com`, `monitor.example.com`)
- Requires DNS records pointing to the host running FastTAK
- The subdomain variables in `.env` (e.g., `TAKSERVER_SUBDOMAIN`, `MONITOR_SUBDOMAIN`) are only used in this mode

### Port variables (direct mode only)

These control which host ports Caddy exposes for each service. Defaults are used if not set. In direct mode, the bare `https://<SERVER_ADDRESS>` (port 443) redirects to the Monitor dashboard. These variables are ignored in subdomain mode.

| Variable | Default | Service |
|---|---|---|
| `TAKSERVER_ADMIN_PORT` | `8446` | TAK Server admin UI |
| `NODERED_PORT` | `1880` | Node-RED |
| `MONITOR_PORT` | `8180` | Monitor dashboard |
| `MEDIAMTX_PORT` | `8888` | MediaMTX streaming |

TAK client ports (8443, 8089) are not configurable — they connect directly to TAK Server, not through Caddy.

### Troubleshooting

If Caddy or auth is misconfigured and you're locked out, access services directly via `docker compose exec` for debugging without going through the reverse proxy.

---

## `.env`

The primary configuration interface. All deployment settings — hostnames, credentials, feature flags, database tuning, data retention, and monitoring threshold overrides — are set here. See `.env.example` for all available variables with inline documentation.

### Monitoring threshold overrides

The monitoring system's default thresholds are defined in `thresholds.yml` (see below), but you should override them via `.env` rather than editing the YAML directly. This keeps your customizations separate from the defaults that ship with FastTAK. Use the `FASTAK_MON_` prefix:

```bash
# Override disk warning from 80% to 85%
FASTAK_MON_DISK__THRESHOLDS__PERCENT__WARNING=85

# Poll database health every 2 minutes instead of 60 seconds
FASTAK_MON_DATABASE__INTERVAL=120

# Only email on config drift if it reaches warning level
FASTAK_MON_CONFIG__ALERT_MIN_LEVEL=warning
```

The naming convention: `FASTAK_MON_<SERVICE>__<KEY>` for top-level settings, `FASTAK_MON_<SERVICE>__THRESHOLDS__<METRIC>__<LEVEL>` for threshold values. See `.env.example` for more examples.

## MediaMTX access

MediaMTX admits nobody anonymously ([DD-056](decisions.md)). Three fixed users, each with a generated password in `.env` (letters and digits only; setup and `./start.sh` create them, and `scripts/check-env.sh` refuses empty or non-alphanumeric values):

| Variable | User | Purpose | Who receives it |
|---|---|---|---|
| `MEDIAMTX_VIEWER_PASSWORD` | `viewer` | Read and playback (RTSP, HLS) | TAK clients, in the video URL they receive for a stream |
| `MEDIAMTX_PUBLISHER_PASSWORD` | `publisher` | Publish a stream | Every source that sends a stream: an encoder, a camera, a drone, a TAK plugin |
| `MEDIAMTX_API_PASSWORD` | `api` | MediaMTX control API | Node-RED, readable by every flow |

Compose refuses to start without all three. Browsers opening HLS are prompted for the viewer credential.

### `MEDIAMTX_PUBLISH_IPS`

Who may publish, as comma-separated CIDRs. Publishing also needs the publisher password. Viewing is unaffected.

| Value | Meaning |
|---|---|
| `127.0.0.1/32` (default) | Publishing off: only MediaMTX's own loopback matches and nothing runs there |
| `0.0.0.0/0` | Anywhere, with the password |
| A subnet, e.g. `10.8.0.0/24` | That subnet only, e.g. a VPN. Linux hosts only |

On Docker Desktop every client appears as the VM gateway (e.g. `192.168.65.1`), so a subnet restriction cannot tell clients apart there.

### Publishing a stream

Any source can publish to any path name, on the same two conditions: its address is in `MEDIAMTX_PUBLISH_IPS`, and it signs in as `publisher`. After you change `MEDIAMTX_PUBLISH_IPS`, run `just up`.

| Protocol | URL the source sends to |
|---|---|
| RTSP | `rtsp://publisher:<MEDIAMTX_PUBLISHER_PASSWORD>@<server>:8554/<path>` |
| RTMP | `rtmp://<server>:1935/<path>?user=publisher&pass=<MEDIAMTX_PUBLISHER_PASSWORD>` |

- **Path** is a name you choose, such as `live/uas1`. It can contain `/`.
- **RTSP is Digest-only.** A source that can only send Basic credentials is refused. If the source has separate username and password fields, use those; MediaMTX treats them the same as credentials in the URL.
- **RTMP sends the password in plaintext**, in the URL. Prefer RTSP. In a tool that has separate *server* and *stream key* fields (OBS), the server is `rtmp://<server>:1935/<first part of path>` and the stream key is `<rest of path>?user=publisher&pass=<password>`.
- **All sources share the one publisher password.** To remove one source, change the password and give the new one to the others.
- **A second source that publishes to a path in use replaces the first** (MediaMTX's default). Give each source its own path.

### Telling TAK clients about a stream

Viewers use a different URL from the one the source sends to:

    rtsp://viewer:<MEDIAMTX_VIEWER_PASSWORD>@<server>:8554/<path>

- Give viewers that URL, or send it in a CoT video marker. `./video-cot.sh` does both the publish and the marker for a test stream.
- **Keep the source's URL to the source.** It contains the publisher password. A source that announces its own stream to other clients (some TAK plugins do) announces the URL it was given, so check what the other clients receive before you rely on it.
- The DroneSense flow builds the viewer URL into each marker itself. MediaMTX pulls that video, so it works with publishing off.

### Rotating a password

Edit the value in `.env` (or clear it to generate a new one), then run `just up`, which applies the letters-and-digits check first. That recreates `mediamtx` and `nodered`. Streams already playing continue. Give the new publisher password to every source, and the new viewer password to every viewer; markers from Node-RED flows such as DroneSense carry the new one within seconds.

### Banning repeated failures (optional)

On Linux hosts you can ban addresses that fail authentication repeatedly with fail2ban, following [MediaMTX's own guide](https://github.com/bluenviron/mediamtx/blob/v1.21.1/docs/2-features/06-authentication.md#ban-after-failed-attempts).

## `monitor/config/thresholds.yml`

Monitoring policy defaults: health check intervals, alert levels, and numeric thresholds (e.g., when disk usage triggers a warning vs. critical alert). This file ships with FastTAK and defines sensible defaults for all services.

**Do not edit this file for deployment-specific tuning.** Use `.env` overrides instead (see above). Direct edits to `thresholds.yml` will be overwritten on upgrades. The YAML file is the reference for what settings are available and what the defaults are.

## `tak/conf/` (init-config output)

TAK Server XML configuration, generated by the `init-config` container at startup from your `.env` values. You generally don't edit these files directly — set the corresponding `.env` variable and recreate `init-config`:

```bash
docker compose up -d --force-recreate init-config tak-server
```

See [database-management.md](database-management.md) for the one case where direct YAML editing is appropriate (retention for files/missions when no `.env` retention variables are set).

## Federation

TAK Server federation is **effectively disabled** in FastTAK: `CoreConfig.xml`
does not enable it, and the federation ports (9000/9001) are not published by
Docker Compose. Enabling federation in the TAK admin UI will **not** make it
reachable — the listener stays isolated inside the container. See DD-046 in
[decisions.md](decisions.md) for the reasoning, and issue #72 for what proper
support would require.


## Deployment-specific overrides

Anything you need to change for one deployment — extra published ports, removed
ports, different memory caps — belongs in `docker-compose.override.yml` at the
repository root, not in `docker-compose.yml`.

Compose loads that filename automatically, and FastTAK's entry points
(`./start.sh`, `just up`) append it last when they assemble `COMPOSE_FILE` for
direct mode or the capture overlay, so your settings win in every mode.

```yaml
# docker-compose.override.yml — not tracked in git
services:
  mediamtx:
    ports: !reset []          # remove MediaMTX's public bindings entirely
  tak-server:
    deploy:
      resources:
        limits:
          memory: 24G         # raise a cap for a larger deployment
```

`!reset` clears a list the base file set; a plain list would merge with it.

!!! warning "Do not use a differently-named file with `COMPOSE_FILE`"
    Setting `COMPOSE_FILE` in your shell to point at, say,
    `docker-compose.local.yml` appears to work, but `./start.sh` and `just up`
    overwrite that variable whenever they need the direct-mode or capture
    overlay. Your file is then silently dropped — the stack comes up without
    it and reports success. If your override removes a published port, that
    port comes back.

    `docker-compose.override.yml` has no such failure mode, because it is
    re-appended by name in every branch.
