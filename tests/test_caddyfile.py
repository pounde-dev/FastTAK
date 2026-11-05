"""Renders the Caddyfile from init-config/start.sh and checks the auth routes.

Runs only the "Generate Caddyfile" section of the script, so the assertions see
exactly what Caddy would load in each DEPLOY_MODE.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = (REPO / "init-config" / "start.sh").read_text()
CADDY_FLOOR = (2, 11, 2)

SECTION = re.search(
    r'^CADDY_DIR=.*?^echo "\[init\] Caddyfile generated', SCRIPT, re.MULTILINE | re.DOTALL
).group(0)


def render(tmp_path: Path, mode: str, **env) -> str:
    script = SECTION.rsplit("\n", 1)[0]
    subprocess.run(
        ["sh", "-eu", "-c", script],
        env={
            "PATH": "/usr/bin:/bin",
            "TAK_DIR": str(tmp_path),
            "SERVER_ADDRESS": "tak.example.org",
            "DEPLOY_MODE": mode,
            **env,
        },
        check=True,
        capture_output=True,
        text=True,
    )
    return (tmp_path / "caddy" / "Caddyfile").read_text()


def site_blocks(caddyfile: str) -> dict[str, str]:
    """Top-level site blocks, keyed by address."""
    blocks = {}
    for match in re.finditer(r"^(\S.*?) \{\n(.*?)^\}$", caddyfile, re.MULTILINE | re.DOTALL):
        blocks[match.group(1)] = match.group(2)
    return blocks


@pytest.fixture(params=["direct", "subdomain"])
def authed_routes(request, tmp_path) -> list[str]:
    blocks = site_blocks(render(tmp_path, request.param))
    routes = [body for body in blocks.values() if "forward_auth" in body]
    assert len(routes) == 2, "expected the Node-RED and monitor routes"
    return routes


def test_client_supplied_identity_headers_are_dropped_before_auth(authed_routes):
    for route in authed_routes:
        strip_user = route.index("request_header -Remote-User")
        strip_groups = route.index("request_header -Remote-Groups")
        assert max(strip_user, strip_groups) < route.index("forward_auth")


def test_client_supplied_forwarded_address_is_never_trusted(authed_routes):
    """ldap-proxy locks out by the address Caddy forwards. With trusted_proxies,
    a client seen from a private range chooses that address itself (#62)."""
    for route in authed_routes:
        assert "trusted_proxies" not in route


def test_backends_never_receive_the_ldap_password(authed_routes):
    for route in authed_routes:
        assert re.search(r"reverse_proxy \S+ \{[^}]*header_up -Authorization", route)


def test_caddy_is_pinned_by_digest_at_or_above_the_forward_auth_fix():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    image = compose["services"]["caddy"]["image"]
    match = re.fullmatch(r"caddy:(\d+)\.(\d+)\.(\d+)-alpine@sha256:[0-9a-f]{64}", image)
    assert match, f"not an exact version with a digest: {image}"
    assert tuple(int(part) for part in match.groups()) >= CADDY_FLOOR


def _route(tmp_path: Path, mode: str, backend: str, **env) -> str:
    blocks = site_blocks(render(tmp_path, mode, **env)).values()
    return next(body for body in blocks if f"reverse_proxy {backend}" in body)


@pytest.mark.parametrize("mode", ["direct", "subdomain"])
def test_node_red_is_limited_to_one_group(tmp_path, mode):
    route = _route(tmp_path, mode, "nodered:1880")
    assert "uri /auth/verify?group=monitor_admin\n" in route


@pytest.mark.parametrize("mode", ["direct", "subdomain"])
def test_node_red_group_comes_from_the_environment(tmp_path, mode):
    route = _route(tmp_path, mode, "nodered:1880", NODERED_ADMIN_GROUP="flow_authors")
    assert "uri /auth/verify?group=flow_authors\n" in route


@pytest.mark.parametrize("mode", ["direct", "subdomain"])
def test_monitor_authorizes_for_itself(tmp_path, mode):
    """The monitor gates each route on groups (DD-047), so Caddy only authenticates."""
    route = _route(tmp_path, mode, "monitor:8080")
    assert "uri /auth/verify\n" in route
    assert "copy_headers Remote-User Remote-Groups" in route


@pytest.mark.parametrize("group", ["a&b=c", "a b", "a\n}", "a/b", "a?x"])
def test_unsafe_node_red_group_stops_startup(tmp_path, group):
    with pytest.raises(subprocess.CalledProcessError) as failure:
        render(tmp_path, "direct", NODERED_ADMIN_GROUP=group)
    assert "NODERED_ADMIN_GROUP" in failure.value.stderr


def test_every_authenticated_route_is_recorded():
    """A new forward_auth route must say who may use it, here and in authentication.md."""
    assert SECTION.count("forward_auth ldap-proxy:8080") == 4


@pytest.mark.parametrize("mode", ["direct", "subdomain"])
def test_monitor_route_proves_it_came_through_caddy(tmp_path, mode):
    route = _route(tmp_path, mode, "monitor:8080")
    assert "header_up X-FastTAK-Proxy-Secret {$MONITOR_PROXY_SECRET}" in route


@pytest.mark.parametrize("mode", ["direct", "subdomain"])
def test_only_the_monitor_route_gets_the_proxy_secret(tmp_path, mode):
    caddyfile = render(tmp_path, mode)
    assert caddyfile.count("X-FastTAK-Proxy-Secret") == 1


def test_proxy_secret_is_never_written_to_the_caddyfile(tmp_path):
    caddyfile = render(tmp_path, "direct", MONITOR_PROXY_SECRET="s3cret-value")
    assert "s3cret-value" not in caddyfile


def test_proxy_secret_reaches_only_caddy_and_the_monitor():
    services = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    holders = {
        name
        for name, svc in services.items()
        if "MONITOR_PROXY_SECRET" in svc.get("environment", {})
    }
    assert holders == {"caddy", "monitor"}
