"""Runs the DroneSense flow's function nodes under Node.js with Node-RED's
runtime objects stubbed, so the CoT and the MediaMTX API calls they produce
can be asserted without a running Node-RED.

Requires `node` on PATH (GitHub's ubuntu runners ship it).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FLOW = json.loads((REPO / "nodered" / "flows-library" / "dronesense.json").read_text())
NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const { EventEmitter } = require('events');
const { func, msg, env, flow, apiStatus } = JSON.parse(fs.readFileSync(0, 'utf8'));

const store = Object.assign({}, flow);
const ctxStore = {};
const logs = { error: [], warn: [], log: [] };
const calls = [];

const http = {
  request(opts, cb) {
    calls.push(opts);
    const req = new EventEmitter();
    req.setTimeout = () => {};
    req.write = () => {};
    req.destroy = () => {};
    req.end = () => {
      const res = new EventEmitter();
      res.statusCode = apiStatus;
      cb(res);
      res.emit('data', apiStatus === 200 ? '{"items":[]}' : '');
      res.emit('end');
    };
    return req;
  },
};

const fn = new Function('msg', 'env', 'flow', 'context', 'node', 'http', 'tls', 'crypto', func);
Promise.resolve(fn(
  msg,
  { get: (k) => env[k] },
  { get: (k) => store[k], set: (k, v) => { store[k] = v; } },
  { get: (k) => ctxStore[k], set: (k, v) => { ctxStore[k] = v; } },
  {
    error: (m) => logs.error.push(String(m)),
    warn: (m) => logs.warn.push(String(m)),
    log: (m) => logs.log.push(String(m)),
  },
  http, {}, require('crypto'),
)).then((out) => {
  const result = { out: out === undefined ? null : out, logs, flow: store, calls };
  process.stdout.write(JSON.stringify(result));
});
"""


def _func(node_id: str) -> str:
    return next(n for n in FLOW if n["id"] == node_id)["func"]


def _run(node_id: str, *, msg=None, env=None, flow=None, api_status=200) -> dict:
    if NODE is None:
        pytest.fail("node is not on PATH; install Node.js to run the flow tests")
    payload = {
        "func": _func(node_id),
        "msg": msg or {},
        "env": env or {},
        "flow": flow or {},
        "apiStatus": api_status,
    }
    result = subprocess.run(
        [NODE, "-e", HARNESS],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


DRONE = {
    "id": "d1",
    "latitude": 38.9,
    "longitude": -77.0,
    "isDrone": True,
    "callSign": "D1",
    "sensors": [{"id": "s1", "name": "cam", "rtsp_url": "rtsps://relay.example:443/x"}],
}


def _build(env):
    return _run("ds-build", msg={"payload": [DRONE], "missionLabel": "M"}, env=env)


def _platform_cot(result) -> str:
    return next(m["payload"] for m in result["out"][0] if 'uid="ds-d1"' in m["payload"])


def test_video_url_carries_the_viewer_credential():
    result = _build({"SERVER_ADDRESS": "tak.example.org", "MEDIAMTX_VIEWER_PASSWORD": "abc123"})
    cot = _platform_cot(result)
    assert 'url="rtsp://viewer:abc123@tak.example.org:8554/ds/s1?tcp"' in cot


def test_connection_entry_address_carries_the_viewer_credential():
    # ATAK connects from ConnectionEntry, which has no username/password attributes.
    result = _build({"SERVER_ADDRESS": "tak.example.org", "MEDIAMTX_VIEWER_PASSWORD": "abc123"})
    assert 'address="viewer:abc123@tak.example.org"' in _platform_cot(result)


def test_missing_viewer_password_emits_no_video_but_keeps_the_proxy_path():
    result = _build({"SERVER_ADDRESS": "tak.example.org"})
    cot = _platform_cot(result)
    assert "<__video/>" in cot
    assert "rtsp://" not in cot
    assert "ds/s1" in result["flow"]["dsDesired"]
    assert any("MEDIAMTX_VIEWER_PASSWORD" in e for e in result["logs"]["error"])


def test_reconcile_authenticates_to_the_api():
    result = _run("ds-reconcile", env={"MEDIAMTX_API_PASSWORD": "apipw"})
    assert result["calls"], "reconcile made no API call"
    assert all(c.get("auth") == "api:apipw" for c in result["calls"])


def test_reconcile_names_a_credential_rejection():
    result = _run("ds-reconcile", env={"MEDIAMTX_API_PASSWORD": "wrong"}, api_status=401)
    assert any("rejected credentials" in w for w in result["logs"]["warn"])
