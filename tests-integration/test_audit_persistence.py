"""Integration test: audit middleware -> fastak_events -> /api/events.

The test stack runs monitor on a host port without Caddy in front, so the
Remote-User header is unforged here -- that's by design for testing. In
production this header is only set by Caddy after a successful LDAP bind.
"""

import time
import uuid

import httpx
import pytest
from conftest import MONITOR_HOST_PORT, admin_headers

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

MONITOR_URL = f"http://localhost:{MONITOR_HOST_PORT}"


def test_creating_a_group_writes_an_audit_row():
    name = f"audit_smoke_{uuid.uuid4().hex[:8]}"
    actor = f"smoke-tester-{uuid.uuid4().hex[:8]}"  # unique to isolate from other tests

    r = httpx.post(
        f"{MONITOR_URL}/api/groups",
        json={"name": name},
        # Same admin group as admin_headers(), but a per-run actor so the audit
        # assertion below can isolate this test's row.
        headers={**admin_headers(), "Remote-User": actor},
        timeout=15,
    )
    assert r.status_code in (200, 201), f"create group failed: {r.status_code} {r.text}"

    body = r.json()
    group_id = body.get("id")

    # Audit write is synchronous, but allow a beat for the DB commit.
    time.sleep(0.5)

    r = httpx.get(
        f"{MONITOR_URL}/api/events",
        params={"actor": actor, "limit": 10},
        headers=admin_headers(),
        timeout=15,
    )
    assert r.status_code == 200
    events = r.json()
    found = [
        e
        for e in events["events"]
        if (e.get("detail") or {}).get("request_body", {}).get("name") == name
    ]
    assert found, (
        f"expected an audit event for group {name!r} created by actor {actor!r}; "
        f"got {len(events['events'])} events for that actor"
    )
    ev = found[0]
    assert ev["actor"] == actor
    assert ev["source"] == "audit"
    assert ev["action"] == "POST /api/groups"

    # Inline cleanup -- test stack tear-down would catch it anyway, but be tidy.
    if group_id is not None:
        httpx.delete(f"{MONITOR_URL}/api/groups/{group_id}", headers=admin_headers(), timeout=15)


def test_csv_export_returns_text_csv():
    r = httpx.get(f"{MONITOR_URL}/api/events.csv?limit=5", headers=admin_headers(), timeout=15)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    # CSV has at least the header row
    assert r.text.strip().splitlines()[0].startswith("id,timestamp,source,actor,action,")
