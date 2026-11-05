# Node-RED

FastTAK includes [Node-RED](https://nodered.org/) with **node-red-contrib-postgresql**
pre-installed for querying the `app-db` PostgreSQL database. For spatial queries,
point your flow at `tak-database` (connection details are the same pattern;
`tak-database` has PostGIS natively).

Only members of `NODERED_ADMIN_GROUP` (default `monitor_admin`) can reach Node-RED, and that includes anything a flow serves over HTTP. See [who may use Node-RED](authentication.md#authorization-who-may-use-node-red).

## Pre-configured Config Nodes

Two config nodes are available out of the box — no credentials or certificates to set up:

| Config Node            | Type       | Use With                                                |
| ---------------------- | ---------- | ------------------------------------------------------- |
| **FastTAK App DB**     | PostgreSQL | Any `postgresql` node — select it as the server         |
| **FastTAK Server TLS** | TLS        | Any `tcp in` or `tcp out` node that talks to TAK Server |

### Sending / Receiving CoT

To connect to TAK Server, add a **tcp out** (send) or **tcp in** (receive) node and:

1. Set **Host** to `tak-server`
2. Set **Port** to `8089`
3. Enable TLS and select **FastTAK Server TLS**

For receiving, set the tcp in node to split on `</event>` so each CoT event arrives
as a complete message.

!!! warning "You must pick a client cert before TLS works"

    **FastTAK Server TLS** ships with empty cert and key fields. Do this once
    after install to make it functional. Copy-paste walkthrough using the
    example account name `svc_nodered` — substitute your actual account name
    wherever you see it.

    ### Step 1 — Create a service account

    1. Open the FastTAK dashboard in your browser.
    2. Go to **Service Accounts**.
    3. Click **Create Service Account**.
    4. Fill in:
        - **Username**: `svc_nodered` (or another name; must start with `svc_`)
        - **Mode**: select **data**
        - **Groups**: check every `tak_*` group that flows using this account
          should publish to. Clients who aren't in at least one of these groups
          won't see the CoT.
    5. Click **Create**.

    The dashboard makes the cert for you behind the scenes.

    ### Step 2 — Open the TLS config node in Node-RED

    1. Open Node-RED in your browser.
    2. Click the **☰** (hamburger) menu at the top right.
    3. Click **Configuration nodes**. A sidebar appears with a list of config
       nodes grouped by type.
    4. Under **tls-config**, double-click **FastTAK Server TLS**.

    An edit dialog opens.

    ### Step 3 — Switch cert/key fields to file-path mode

    The dialog defaults to a file-upload widget. FastTAK needs file paths
    instead (the cert files live on disk inside the container — you won't
    upload anything).

    **Check the checkbox labeled "Use key or certificate from local file".**

    After checking it, the Client Certificate and Client Key inputs change
    into plain text fields.

    ### Step 4 — Fill in the account name

    The Client Certificate and Client Key fields are pre-filled with a
    template path:

    ```
    /data/certs/{svc_user}.cert.pem
    /data/certs/{svc_user}.key.pem
    ```

    Replace `{svc_user}` with the account you created in Step 1. If the
    account is `svc_nodered`, the fields become:

    ```
    /data/certs/svc_nodered.cert.pem
    /data/certs/svc_nodered.key.pem
    ```

    Leave **CA Certificate** at `/opt/tak/certs/ca.pem` (already filled in).

    Click **Update**, then click the red **Deploy** button at the top right.

    Everything that uses **FastTAK Server TLS** now connects as that
    account and can publish into its groups.

### Using different certs for different flows

`FastTAK Server TLS` is a **single shared** config node — if you reference it from
multiple flows, they all use the same cert (same service account, same groups).

For per-flow cert isolation:

- **If the flow ships with its own TLS config node** (everything under
  [Flows library](flows/index.md) does), just configure that node and leave
  `FastTAK Server TLS` alone. See [Flows overview](flows/index.md).
- **For ad-hoc flows you build yourself**, clone `FastTAK Server TLS` in the
  config-nodes panel: find it, use Node-RED's duplicate action, rename the clone,
  set its `cert` and `key` to a different service account's PEMs, and change your
  flow's `tcp out` **TLS** dropdown to the clone.

---

## GeoJSON to CoT

The flow below converts any GeoJSON source into Cursor on Target (CoT) XML and pushes
it to TAK Server. It uses a **configurable function node** — instead of hardcoding CoT
parameters in the code, configuration is passed on the message via a **change** node.
This means you can reuse the same function node for completely different data sources,
each with its own CoT type, icon, stale time, and property mappings.

### How the Config Pattern Works

Each data source gets its own config by wiring a **change** node before the function:

```
[Data Source A] → [Config A] ──→ [GeoJSON to CoT] → [TAK Server]
[Data Source B] → [Config B] ──↗
```

The config rides on the message (`msg.cotType`, `msg.uidPrefix`, etc.), so when
Data Source A's message arrives, it carries Config A's settings. When Data Source B's
message arrives, it carries Config B's settings. The function node reads config from
whichever message it receives.

This is useful when you have a single GeoJSON feed that contains different event types.
For example, a sensor API might return both temperature alerts and motion detections.
You can split the feed with a **switch** node, apply different configs, and push both
to TAK with different icons:

```
                    ┌→ [Temperature config] ──→ [GeoJSON to CoT] → [TAK Server]
[Sensor API] → [Switch]
                    └→ [Motion config]     ──↗
```

Temperature alerts show as one icon type, motion detections as another — all from
the same API call, through the same converter, to the same TAK Server connection.

### Configuration Properties

Set these on the message via a **change** node wired before the function:

| msg property           | Default     | Description                                                     |
| ---------------------- | ----------- | --------------------------------------------------------------- |
| `msg.cotType`          | `a-f-G-U-C` | CoT event type (determines TAK icon)                            |
| `msg.cotHow`           | `m-g`       | How the event was generated (`m-g` = machine-generated)         |
| `msg.uidPrefix`        | `geojson`   | Prefix for unique IDs                                           |
| `msg.staleMinutes`     | `60`        | Minutes until marker expires on TAK clients                     |
| `msg.callsignProperty` | `name`      | GeoJSON property to use as the marker callsign                  |
| `msg.remarksProperty`  | `remarks`   | GeoJSON property to use as the marker description               |
| `msg.altitudeProperty` |             | GeoJSON property for altitude override (empty = use geometry z) |

### Common CoT Types

The `cotType` determines the icon displayed on TAK clients. Types follow
[MIL-STD-2525](https://en.wikipedia.org/wiki/NATO_Joint_Military_Symbology):
first letter is `a` (atom/entity) or `b` (bits/point), second is affiliation
(`f`=friendly, `h`=hostile, `n`=neutral, `u`=unknown), then hierarchy
(G=ground, A=air, S=sea).

| Type              | Description                        |
| ----------------- | ---------------------------------- |
| `a-f-G-U-C`       | Ground unit                        |
| `a-f-G-E-S`       | Sensor                             |
| `a-f-A-M-H-Q`     | Drone/UAV (rotary wing/quadcopter) |
| `a-f-A-M-F-Q`     | Drone/uav (fixed wing)             |
| `a-f-g-i-i-e-icp` | incident command post              |
| `a-f-g-i-i-m`     | emergency medical services         |
| `b-m-p-s-m`       | seismic event                      |

---

## Tutorial: USGS Earthquakes on the TAK Map

This tutorial walks through building a flow that polls the USGS earthquake feed
every five minutes and pushes each event to TAK Server as a seismic event marker.
It demonstrates the configurable GeoJSON-to-CoT pattern with a real data source.

### Step 1 — Create the data source

1. Create a new flow tab — double-click the tab and name it `USGS Earthquakes to TAK`
2. Drag an **inject** node onto the canvas
3. Double-click the inject node to configure it:
   - Set **Name** to `Every 5 Min`
   - Delete all message payload properties (click the X next to each one)
   - Check **Inject once after 0 seconds** so it fires on deploy
   - Set **Repeat** to interval, every `5` minutes
   - Click **Done**
4. Drag an **http request** node and wire it to the inject
5. Set **Method** to GET
6. Set **URL** to `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson`
7. Set **Return** to a parsed JSON object

### Step 2 — Add the config

1. Drag a **change** node and wire it to the http request output
2. Add rules to set these `msg` properties:
   - `msg.cotType` → `b-m-p-s-m` (seismic event)
   - `msg.uidPrefix` → `usgs`
   - `msg.staleMinutes` → `60` (set type to number)
   - `msg.callsignProperty` → `title`
   - `msg.remarksProperty` → `place`

### Step 3 — Add the converter

1. Drag a **function** node and wire it to the change node
2. Set **Name** to `GeoJSON to CoT`
3. Paste the following code into the function body:

```js
// Read config from the message (set by the change node in Step 2)
const cotType = msg.cotType || "a-f-G-U-C"; // CoT event type
const cotHow = msg.cotHow || "m-g"; // m-g = machine-generated
const uidPrefix = msg.uidPrefix || "geojson"; // prefix for marker UIDs
const staleMinutes = msg.staleMinutes || 60; // marker expiry time
const callsignProp = msg.callsignProperty || "name"; // GeoJSON prop → callsign
const remarksProp = msg.remarksProperty || "remarks"; // GeoJSON prop → remarks
const altProp = msg.altitudeProperty || ""; // GeoJSON prop → altitude

// Accept a FeatureCollection or a single Feature
const features = msg.payload.features || [msg.payload];
const messages = [];

for (const f of features) {
  if (!f.geometry || !f.geometry.coordinates) continue;

  const coords = f.geometry.coordinates;
  const lon = coords[0];
  const lat = coords[1];

  // Use altitude property if specified, otherwise fall back to geometry z
  const alt =
    altProp && f.properties[altProp] != null
      ? f.properties[altProp]
      : coords[2] || 0;

  const props = f.properties || {};
  const uid = `${uidPrefix}-${f.id || Date.now()}`;
  const callsign = props[callsignProp] || uid;
  const remarks = props[remarksProp] || "";

  // Build timestamps — stale controls how long the marker lives on the map
  const now = new Date().toISOString();
  const stale = new Date(Date.now() + staleMinutes * 60000).toISOString();

  // Build CoT XML event
  const cot = `<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0"
       uid="${uid}"
       type="${cotType}"
       how="${cotHow}"
       time="${now}"
       start="${now}"
       stale="${stale}">
  <point lat="${lat}" lon="${lon}" hae="${alt}" ce="9999999" le="9999999"/>
  <detail>
    <remarks>${remarks}</remarks>
    <contact callsign="${callsign}"/>
  </detail>
</event>`;

  messages.push({ payload: cot });
}

// Return all messages at once — Node-RED sends them in sequence
return [messages];
```

### Step 4 — Add the TAK Server output

1. Drag a **tcp out** node and wire it to the function output
2. Set **Host** to `tak-server`, **Port** to `8089`
3. Enable TLS and select **FastTAK Server TLS**

### Step 5 — Deploy and verify

Click **Deploy**. The inject node fires immediately, fetches the latest earthquakes,
and pushes them to TAK Server. Open ATAK, WinTAK, or iTAK to see the markers.

### Import This Flow

Or skip the manual setup — copy the JSON below, then in Node-RED go to
**Menu → Import → Clipboard** and paste:

```json
[
  {
    "id": "geojson-cot-tab",
    "type": "tab",
    "label": "GeoJSON to CoT",
    "info": "Configurable GeoJSON to CoT converter. Each data source carries its own config on the message."
  },
  {
    "id": "geojson-cot-comment",
    "type": "comment",
    "z": "geojson-cot-tab",
    "name": "Data Source → Config → GeoJSON to CoT → TAK Server",
    "info": "Wire a change node before the function to set msg.cotType, msg.uidPrefix, etc.\nEach source can have its own config. Duplicate the source chain to add more feeds.",
    "x": 280,
    "y": 40,
    "wires": []
  },
  {
    "id": "geojson-cot-usgs-inject",
    "type": "inject",
    "z": "geojson-cot-tab",
    "name": "Every 5 min",
    "props": [],
    "repeat": "300",
    "once": true,
    "onceDelay": "0",
    "topic": "",
    "x": 130,
    "y": 120,
    "wires": [["geojson-cot-usgs-http"]]
  },
  {
    "id": "geojson-cot-usgs-http",
    "type": "http request",
    "z": "geojson-cot-tab",
    "name": "USGS Feed",
    "method": "GET",
    "ret": "obj",
    "paytoqs": "ignore",
    "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "tls": "",
    "persist": false,
    "proxy": "",
    "insecureHTTPParser": false,
    "authType": "",
    "senderr": false,
    "headers": [],
    "x": 300,
    "y": 120,
    "wires": [["geojson-cot-usgs-config"]]
  },
  {
    "id": "geojson-cot-usgs-config",
    "type": "change",
    "z": "geojson-cot-tab",
    "name": "Earthquake config",
    "rules": [
      {
        "t": "set",
        "p": "cotType",
        "pt": "msg",
        "to": "b-m-p-s-m",
        "tot": "str"
      },
      { "t": "set", "p": "uidPrefix", "pt": "msg", "to": "usgs", "tot": "str" },
      {
        "t": "set",
        "p": "staleMinutes",
        "pt": "msg",
        "to": "60",
        "tot": "num"
      },
      {
        "t": "set",
        "p": "callsignProperty",
        "pt": "msg",
        "to": "title",
        "tot": "str"
      },
      {
        "t": "set",
        "p": "remarksProperty",
        "pt": "msg",
        "to": "place",
        "tot": "str"
      }
    ],
    "x": 510,
    "y": 120,
    "wires": [["geojson-cot-function"]]
  },
  {
    "id": "geojson-cot-function",
    "type": "function",
    "z": "geojson-cot-tab",
    "name": "GeoJSON to CoT",
    "func": "const cotType = msg.cotType || 'a-f-G-U-C';\nconst cotHow = msg.cotHow || 'm-g';\nconst uidPrefix = msg.uidPrefix || 'geojson';\nconst staleMinutes = msg.staleMinutes || 60;\nconst callsignProp = msg.callsignProperty || 'name';\nconst remarksProp = msg.remarksProperty || 'remarks';\nconst altProp = msg.altitudeProperty || '';\n\nconst features = msg.payload.features || [msg.payload];\nconst messages = [];\n\nfor (const f of features) {\n    if (!f.geometry || !f.geometry.coordinates) continue;\n\n    const coords = f.geometry.coordinates;\n    const lon = coords[0];\n    const lat = coords[1];\n    const alt = altProp && f.properties[altProp] != null\n        ? f.properties[altProp]\n        : (coords[2] || 0);\n\n    const props = f.properties || {};\n    const uid = `${uidPrefix}-${f.id || Date.now()}`;\n    const callsign = props[callsignProp] || uid;\n    const remarks = props[remarksProp] || '';\n\n    const now = new Date().toISOString();\n    const stale = new Date(Date.now() + staleMinutes * 60000).toISOString();\n\n    const cot = `<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<event version=\"2.0\"\n       uid=\"${uid}\"\n       type=\"${cotType}\"\n       how=\"${cotHow}\"\n       time=\"${now}\"\n       start=\"${now}\"\n       stale=\"${stale}\">\n  <point lat=\"${lat}\" lon=\"${lon}\" hae=\"${alt}\" ce=\"9999999\" le=\"9999999\"/>\n  <detail>\n    <remarks>${remarks}</remarks>\n    <contact callsign=\"${callsign}\"/>\n  </detail>\n</event>`;\n\n    messages.push({ payload: cot });\n}\n\nreturn [messages];",
    "outputs": 1,
    "timeout": "",
    "noerr": 0,
    "initialize": "",
    "finalize": "",
    "libs": [],
    "x": 720,
    "y": 180,
    "wires": [["geojson-cot-tcp"]]
  },
  {
    "id": "geojson-cot-tcp",
    "type": "tcp out",
    "z": "geojson-cot-tab",
    "name": "TAK Server",
    "host": "tak-server",
    "port": "8089",
    "beserver": "client",
    "base64": false,
    "end": false,
    "tls": "fastak-tls",
    "close": false,
    "x": 920,
    "y": 180,
    "wires": []
  }
]
```

After importing, click **Deploy**. The inject node fires immediately, fetches the latest
earthquakes from USGS, and pushes them to TAK Server as seismic event markers.

### Adding Another Data Source

To add a second GeoJSON feed to the same flow:

1. Drag an **inject** node (set your poll interval)
2. Drag an **http request** node (set your GeoJSON URL)
3. Drag a **change** node and set the config properties you want (different `cotType`, `uidPrefix`, etc.)
4. Wire: inject → http request → change → the existing **GeoJSON to CoT** function node

Both feeds share the same function node and TAK Server connection. Each arrives with
its own config on the message, so they render as different icons on the TAK map.
