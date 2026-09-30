# DroneSense → TAK

Polls the DroneSense REST API and emits CoT to TAK Server for every active
device in your missions: drones, operator phones with a streaming camera,
and plain operators without video.

!!! note "Read the [Flows overview](index.md) first"

    Every flow in the library requires a data-mode service account with at least one `tak_*` group assignment. If you haven't set that up yet, start with the overview.

## What it emits

For each item returned by the API, per poll cycle, the CoT type is picked
from the item's shape:

| Input | CoT type | Marker |
|---|---|---|
| `isDrone: true` | `a-f-A-M-H-Q` | Rotary-wing UAV |
| `isDrone: false`, any sensor has `rtsp_url` | `a-f-G-E-S` | Ground sensor with video |
| `isDrone: false`, no video | `a-f-G-U-C` | Ground unit |

All three emit:

- **Platform event** at the item's GPS position. Callsign = `<Mission> // <callSign>`.
- **`<track>`** with course/speed (speed clamped to 0 if DroneSense reports its `-1` sentinel for devices without a reliable speed reading).
- **`<__video>`** element — empty `<__video/>` when no sensor is streaming, or populated with `sensor`/`spi`/`url` attributes plus a nested `<ConnectionEntry>` when at least one sensor has an RTSP URL. The `url` and the `ConnectionEntry address` carry `viewer:<password>@<server>`; the password comes from `MEDIAMTX_VIEWER_PASSWORD`. If it is unset the flow logs once and emits `<__video/>`. Stop emitting the URL (sensor goes offline) and the next frame clears it; WinTAK tears down its player.

Drones additionally emit:

- **SPI event** — `b-m-p-s-p-i` at `spoiLat`/`spoiLng` (the camera's ground intersection) when DroneSense provides it. Carries a `<link>` back-reference to the platform uid — WinTAK uses this to draw the sensor line from platform to SPI.

Stale time is `time + 10s` by default (configurable — see below). DroneSense-specific fields that aren't available (attitude, FOV, range, platform serial, home position) are not emitted; nothing is faked.

## Video proxy

DroneSense video streams use `rtsps://` (RTSP over TLS) with self-signed certificates that most TAK clients can't validate. The flow registers each active stream as a MediaMTX proxy path so TAK clients connect to a plain `rtsp://` URL on your server:

```
DroneSense RTSPS → MediaMTX proxy → rtsp://viewer:<password>@<your-server>:8554/ds/<sensor-id>?tcp
```

TLS fingerprints are fetched on first contact with each DroneSense video server and cached in Node-RED flow context — no manual fingerprint configuration required.

The flow continuously reconciles these proxy paths against the DroneSense API: because
DroneSense rotates each stream's relay endpoint per session, the flow updates the
MediaMTX source when it changes and removes the path when the sensor goes offline. Reconcile authenticates to the MediaMTX API as `api` using `MEDIAMTX_API_PASSWORD`.

DroneSense streams inherit MediaMTX's `pathDefaults`, so enabling [video recording](../video-recording.md) captures them automatically. Files land under `recordings/ds/<sensor-id>/`.

## Stream start/stop semantics

There is **no separate stream-stop CoT**. When a sensor's `rtsp_url` disappears from the DroneSense response, the next platform frame emits `<__video/>` empty. WinTAK diffs frame-to-frame and tears down its player accordingly.

## Install

Using the sample account name `svc_nodered` throughout — substitute your
actual account name where shown.

### Before you start

Make sure you've done the [Flows overview prerequisites](index.md#prerequisites):
at least one data-mode service account exists in the FastTAK dashboard with
the `tak_*` groups this flow should publish to.

### 1. Import the flow

1. Open Node-RED.
2. Click **☰** (top right) → **Import** → **Local** tab.
3. Expand the **fasttak** folder and click **dronesense**.
4. Click **Import**. A **DroneSense UAS** tab appears.

### 2. Configure the TLS node

1. Click **☰** → **Configuration nodes**.
2. Under **tls-config**, double-click **DroneSense Server TLS** (this is
   distinct from the shared `FastTAK Server TLS` — leave that one alone).
3. **Check the box "Use key or certificate from local file".** The cert
   and key fields change to plain text inputs.
4. The Client Certificate and Client Key fields are pre-filled with a
   template path — `/data/certs/{svc_user}.cert.pem` and `.key.pem`.
   Replace `{svc_user}` with your account's username (e.g., for
   `svc_nodered` the cert path becomes `/data/certs/svc_nodered.cert.pem`).
   **CA Certificate** stays at `/opt/tak/certs/ca.pem`.
5. Leave **Server Name** at `${SERVER_ADDRESS}` — Node-RED resolves it
   from your `.env` on every flow load.
6. Click **Update**.

### 3. Add your DroneSense API keys

1. On the **DroneSense UAS** tab, double-click the **Missions** change node.
2. Replace the default value with your API keys:

    ```json
    [
      { "name": "Structure Fire",  "key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
      { "name": "Search & Rescue", "key": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy" }
    ]
    ```

    `name` shows as a prefix on the TAK map (e.g., `Structure Fire // DRONE-1`).

3. Click **Done**.

### 4. Deploy

Click the red **Deploy** button at the top right. The first poll fires
within a second. Open WinTAK/ATAK to see your drones appear.

## Config

Environment variables available to the nodered container:

| Variable | Default | Purpose |
|---|---|---|
| `SERVER_ADDRESS` | `localhost` | Public hostname/IP of your TAK Server. Already set from `.env` via `docker-compose.yml`. Used in the RTSP proxy URL advertised to clients. |
| `DRONESENSE_STALE_SECONDS` | `10` | How long WinTAK keeps a marker alive between polls. Should be > poll interval. Add to the nodered service's `environment:` block in `docker-compose.yml` to override. |
| `MEDIAMTX_VIEWER_PASSWORD` | generated | Viewer credential embedded in each marker's video URL. Generated by setup; passed to Node-RED by `docker-compose.yml`. |
| `MEDIAMTX_API_PASSWORD` | generated | Credential for the MediaMTX control API, used by reconcile. Generated by setup; passed to Node-RED by `docker-compose.yml`. |

Poll interval is the **Inject** node's `repeat` value (default 5s). Edit the node if you need a different cadence.

## Verify

After deploy, watch the reconcile activity:

```bash
docker compose logs -f nodered | grep 'ds:'
```

You should see `ds: registered ds/<id>` when a sensor starts streaming, `ds: replaced
(endpoint rotated) ds/<id>` when DroneSense issues a new session endpoint, and `ds:
deleted ds/<id> (offline)` when it stops. In WinTAK, drones appear with their callsign;
when a sensor starts streaming, the `<__video>` populates and a video button becomes
available on the marker.

`ds: cannot read MediaMTX paths: MediaMTX API rejected credentials` means the API
password Node-RED has does not match MediaMTX's. `just up` recreates
both from `.env`.

## Upgrading from an earlier release

MediaMTX now requires credentials, so the flow must be re-imported:

1. Copy your API keys out of the **Missions** node.
2. Re-import the flow: **☰** → **Import** → **Local** → **fasttak** → **dronesense** → **Replace**.
3. Paste the keys back into **Missions** and click **Deploy**.

Until then, markers carry a URL without credentials and video returns 401. Before the re-import, Node-RED also logs `ds: cannot read MediaMTX paths: list HTTP 401` every cycle, because the old flow calls the API anonymously.

## Files

- `nodered/flows-library/dronesense.json` — the flow (importable).
