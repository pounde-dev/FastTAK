"""start.sh, driven end to end with a stub docker on PATH.

The stub records every docker invocation to a log and answers the few
queries start.sh makes (health status, container ids). Tests assert on the
recorded argv — which flags reached compose — not on the stack.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
START = REPO / "start.sh"


def _publishers(*entries: tuple[str, int, str]) -> str:
    """Lines as `docker compose ps --format '{{.Service}} {{json .Publishers}}'`
    prints them: one per service, each published port listed for IPv4 and IPv6."""
    by_svc: dict[str, list[dict]] = {}
    for svc, port, proto in entries:
        for url in ("0.0.0.0", "::"):
            by_svc.setdefault(svc, []).append(
                {"URL": url, "TargetPort": port, "PublishedPort": port, "Protocol": proto}
            )
    return "\n".join(f"{svc} {json.dumps(pubs)}" for svc, pubs in by_svc.items())


SUBDOMAIN_PUBLISHED = _publishers(
    ("caddy", 80, "tcp"),
    ("caddy", 443, "tcp"),
    ("caddy", 443, "udp"),
    ("tak-server", 8089, "tcp"),
    ("tak-server", 8443, "tcp"),
    ("tak-server", 8446, "tcp"),
    ("mediamtx", 8554, "tcp"),
    ("mediamtx", 1935, "tcp"),
)

STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
# Match the compose subcommand with its surrounding spaces: the --env-file
# path is a pytest tmp dir named after the test, so it can contain "up".
case " $* " in
  *" build "*|*" build")
    [ -n "${STUB_BUILD_ERR:-}" ] && { printf '%s\n' "$STUB_BUILD_ERR" >&2; exit 1; }
    ;;
  *" up "*)
    [ -n "${STUB_UP_ERR:-}" ] && { printf '%s\n' "$STUB_UP_ERR" >&2; exit 1; }
    ;;
  *" port "*)
    svc=$(printf '%s\n' "$*" | sed -n 's/.* port \([^ ]*\) .*/\1/p')
    entry=$(printf '%s\n' "${STUB_PORT_MAP:-}" | tr ';' '\n' |
      grep "^$svc=" | head -1 | cut -d= -f2-)
    case "$entry" in
      "")
        cport=$(printf '%s\n' "$*" | sed -n 's/.* port [^ ]* \([^ ]*\).*/\1/p')
        printf '0.0.0.0:%s\n' "$cport"
        ;;
      ERR) echo "no port for container $svc" >&2; exit 1 ;;
      *)   printf '%s\n' "$entry" ;;
    esac
    ;;
  *" ps --format "*)
    printf '%s\n' "${STUB_PS_JSON:-}"
    ;;
  *" ps "*)
    svc=$(printf '%s\n' "$*" | sed -n 's/.* ps -a\{0,1\}q \([^ ]*\).*/\1/p')
    [ "$svc" = "${STUB_PS_EMPTY:-}" ] || echo stubid
    ;;
  "inspect "*|" inspect "*)
    case "$*" in
      *" ") exit 1 ;;  # trailing space: the container id argument was empty
      *ExitCode*)     echo "${STUB_INSPECT_EXITCODE:-0}" ;;
      *State.Status*) echo running ;;
      *)              echo healthy ;;
    esac
    ;;
  " exec "*)
    case "$*" in
      *healthcheck.sh*)
        echo "${STUB_HEALTHCHECK_OUT:-HEALTHY: all processes running, ports ok, certs valid}"
        exit "${STUB_HEALTHCHECK_RC:-0}"
        ;;
      *"grep -c"*)      echo 0 ;;
      *)                echo "5/5" ;;
    esac
    ;;
esac
exit 0
"""


CORECONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<Configuration>
  <repository>
    <connection url="jdbc:postgresql://tak-database:5432/cot" password="stub-password"/>
  </repository>
  <network><connector port="8446" enableAdminUI="true"/></network>
  <auth><ldap serviceAccountDN="cn=adm_ldapservice,ou=people,dc=takldap"/></auth>
  <security><certificateSigning CA="TAKServer"></certificateSigning></security>
  <groups adminGroup="ROLE_ADMIN"/>
</Configuration>
"""

CERT_FILES = ["root-ca.pem", "ca.pem", "takserver.jks", "svc_fasttakapi.p12", "ca-signing.jks"]


@pytest.fixture
def deployment(tmp_path):
    """A scratch deployment that passes every check: tak/ with a CoreConfig
    and the cert files start.sh looks for, a valid .env, and stubs on PATH
    that answer healthy unless a STUB_* variable says otherwise."""
    tak = tmp_path / "tak"
    (tak / "certs" / "files").mkdir(parents=True)
    (tak / "CoreConfig.xml").write_text(CORECONFIG)
    for name in CERT_FILES:
        (tak / "certs" / "files" / name).write_bytes(b"stub")
    env = (REPO / ".env.example").read_text()
    env = env.replace("SERVER_ADDRESS=tak.example.com", "SERVER_ADDRESS=localhost")
    env = env.replace("TOKENS_API_SECRET=", "TOKENS_API_SECRET=" + "0" * 64)
    for key in (
        "MEDIAMTX_VIEWER_PASSWORD",
        "MEDIAMTX_PUBLISHER_PASSWORD",
        "MEDIAMTX_API_PASSWORD",
    ):
        env = env.replace(f"{key}=", f"{key}=" + "a1" * 16)
    (tmp_path / ".env").write_text(env)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    nc = bin_dir / "nc"
    nc.write_text(
        '#!/bin/sh\nprintf \'nc %s\\n\' "$*" >> "$DOCKER_LOG"\nexit "${STUB_NC_RC:-0}"\n'
    )
    nc.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf \'curl %s\\n\' "$*" >> "$DOCKER_LOG"\n'
        "printf '%s' \"${STUB_CURL_CODE:-200}\"\n"
    )
    curl.chmod(0o755)
    return tmp_path


def run_start(
    deployment: Path, *args: str, extra_env: dict | None = None
) -> tuple[subprocess.CompletedProcess, list[str]]:
    log = deployment / "docker.log"
    log.unlink(missing_ok=True)  # tests calling run_start twice must not see the prior call's log
    env = {
        **os.environ,
        "PATH": f"{deployment / 'bin'}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "FASTAK_ENV_FILE": str(deployment / ".env"),
        "STUB_PS_JSON": SUBDOMAIN_PUBLISHED,
        **(extra_env or {}),
    }
    result = subprocess.run(
        ["/bin/bash", str(START), *args], capture_output=True, text=True, env=env, timeout=120
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_bare_start_succeeds_against_the_stub(deployment):
    result, calls = run_start(deployment)
    assert result.returncode == 0, result.stderr + result.stdout
    assert any(c.startswith("compose") and " build" in c for c in calls)
    assert any(c.startswith("compose") and " up " in c for c in calls)


def test_direct_mode_selects_the_direct_compose_file(deployment):
    env = (deployment / ".env").read_text().replace("DEPLOY_MODE=subdomain", "DEPLOY_MODE=direct")
    (deployment / ".env").write_text(env)
    # The stub sees COMPOSE_FILE via the environment; prove it reached the up.
    stub = deployment / "bin" / "docker"
    stub.write_text(
        STUB.replace(
            '*" up "*)',
            '*" up "*) printf "COMPOSE_FILE=%s\\n" "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG";',
        )
    )
    result, calls = run_start(deployment)
    assert result.returncode == 0, result.stderr
    # Prefix, not equality: a gitignored docker-compose.override.yml in the
    # repo root (an expected local dev artefact) gets appended by
    # stack_export_compose_file, which would fail an exact-match assertion
    # with no code change.
    assert any(
        c.startswith("COMPOSE_FILE=docker-compose.yml:docker-compose.direct.yml") for c in calls
    )


def test_subdomain_mode_leaves_compose_file_unset(deployment):
    stub = deployment / "bin" / "docker"
    stub.write_text(
        STUB.replace(
            '*" up "*)',
            '*" up "*) printf "COMPOSE_FILE=%s\\n" "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG";',
        )
    )
    result, calls = run_start(deployment)
    assert result.returncode == 0, result.stderr
    assert "COMPOSE_FILE=<unset>" in calls


def test_missing_tak_dir_fails_preflight(deployment):
    shutil.rmtree(deployment / "tak")
    result, _ = run_start(deployment)
    assert result.returncode == 1
    assert "tak/ not found" in result.stderr


def _up_call(calls: list[str]) -> str:
    return next(c for c in calls if c.startswith("compose") and " up " in c)


def _build_call(calls: list[str]) -> str:
    return next(c for c in calls if c.startswith("compose") and " build" in c)


def test_bare_up_removes_orphans(deployment):
    _, calls = run_start(deployment)
    assert "--remove-orphans" in _up_call(calls)


def test_named_services_are_force_recreated_without_orphan_pruning(deployment):
    """A targeted rebuild has no business pruning the project — that is what
    silently removed the capture sidecars."""
    _, calls = run_start(deployment, "monitor", "nodered")
    up = _up_call(calls)
    assert "--force-recreate" in up
    assert up.endswith("monitor nodered")
    assert "--remove-orphans" not in up
    assert _build_call(calls).endswith("monitor nodered")


def test_capture_adds_the_overlay(deployment):
    stub = deployment / "bin" / "docker"
    stub.write_text(
        STUB.replace(
            '*" up "*)',
            '*" up "*) printf "COMPOSE_FILE=%s\\n" "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG";',
        )
    )
    result, calls = run_start(deployment, "--capture")
    assert result.returncode == 0, result.stderr
    assert any(
        c.startswith("COMPOSE_FILE=docker-compose.yml:docker-compose.capture.yml") for c in calls
    )


def test_checks_run_on_a_bare_up(deployment):
    result, calls = run_start(deployment)
    assert any(c.startswith("exec") for c in calls), "the checks exec into tak-server"
    assert "checks" in result.stdout.lower()


def test_checks_skipped_when_services_are_named(deployment):
    result, calls = run_start(deployment, "monitor")
    assert not any(c.startswith("exec") for c in calls)
    assert "skipped" in result.stdout.lower()
    assert "https://localhost:8446" in result.stdout


def test_checks_flag_overrides_the_default(deployment):
    _, calls = run_start(deployment, "monitor", "--checks")
    assert any(c.startswith("exec") for c in calls)
    _, calls = run_start(deployment, "--no-checks")
    assert not any(c.startswith("exec") for c in calls)


def test_no_wait_skips_the_health_loop(deployment):
    _, calls = run_start(deployment, "--no-wait", "--no-checks")
    assert not any(c.startswith("inspect") for c in calls)


def test_no_wait_implies_no_checks_by_default(deployment):
    _, calls = run_start(deployment, "--no-wait")
    assert not any(c.startswith("exec") for c in calls)


def test_no_wait_with_explicit_checks_still_runs_them(deployment):
    _, calls = run_start(deployment, "--no-wait", "--checks")
    assert any(c.startswith("exec") for c in calls)


def test_unknown_option_is_rejected(deployment):
    result, _ = run_start(deployment, "--bogus")
    assert result.returncode == 2
    assert "--bogus" in result.stderr


def test_failed_build_stops_the_start_and_shows_why(deployment):
    """The old line was `docker compose build --quiet 2>/dev/null` with no
    exit check: a failed build was followed by up -d starting the previous
    image and a success summary."""
    result, calls = run_start(
        deployment, "--no-checks", extra_env={"STUB_BUILD_ERR": "ERROR: failed to solve: monitor"}
    )
    assert result.returncode == 1
    assert "build failed" in result.stderr.lower()
    assert "failed to solve: monitor" in result.stderr
    assert not any(" up " in c for c in calls), "must not start the old image"


def test_failed_up_stops_the_start_and_shows_why(deployment):
    result, calls = run_start(
        deployment,
        "--no-checks",
        extra_env={"STUB_UP_ERR": "Error response from daemon: port is already allocated"},
    )
    assert result.returncode == 1
    assert "port is already allocated" in result.stderr
    assert not any(c.startswith("inspect") for c in calls), (
        "must not wait on a stack that did not start"
    )


def test_verbose_prints_every_check(deployment):
    """Passes are silent by default; --verbose shows them (#114)."""
    quiet, _ = run_start(deployment)
    verbose, _ = run_start(deployment, "--verbose")
    assert "✅ TAK Server healthy" not in quiet.stdout
    assert "✅ TAK Server healthy" in verbose.stdout


def test_help_lists_verbose(deployment):
    result, _ = run_start(deployment, "--help")
    assert result.returncode == 0
    assert "--verbose" in result.stdout


def test_failure_lines_say_what_was_checked_and_what_came_back(deployment):
    """STUB_INSPECT_EXITCODE makes the exit-code checks fail. The line must
    carry the expectation, the observation and the next command, not just a
    label (#114)."""
    result, _ = run_start(deployment, extra_env={"STUB_INSPECT_EXITCODE": "1"})
    assert (
        '❌ init-config exited 0: expected "0", got "1". '
        "Next: docker compose logs init-config" in result.stdout
    )


def test_summary_points_at_verbose_when_checks_fail(deployment):
    result, _ = run_start(deployment, extra_env={"STUB_INSPECT_EXITCODE": "1"})
    assert "checks failed" in result.stdout
    assert "--verbose" in result.stdout


def test_exposed_but_unpublished_port_is_a_note(deployment):
    """Compose prints `invalid IP:0` for a port the image exposes but nothing
    publishes — Node-RED in subdomain mode. It read as port 0 and failed (#120)."""
    result, _ = run_start(
        deployment, "--verbose", extra_env={"STUB_PORT_MAP": "nodered=invalid IP:0"}
    )
    assert "❌ Node-RED" not in result.stdout
    assert "– Node-RED not published by this compose configuration" in result.stdout


def test_unknown_container_port_is_a_note(deployment):
    """Compose exits 1 with nothing on stdout for a port the image does not
    expose at all — MediaMTX HLS in subdomain mode."""
    result, _ = run_start(deployment, "--verbose", extra_env={"STUB_PORT_MAP": "mediamtx=ERR"})
    assert "❌ MediaMTX HLS" not in result.stdout
    assert "– MediaMTX HLS not published by this compose configuration" in result.stdout


def test_published_port_is_probed(deployment):
    result, calls = run_start(
        deployment, "--verbose", extra_env={"STUB_PORT_MAP": "nodered=0.0.0.0:1880"}
    )
    assert "✅ Node-RED (port 1880)" in result.stdout
    assert "nc -z localhost 1880" in calls


def test_published_port_not_listening_fails_with_the_port(deployment):
    result, _ = run_start(
        deployment, extra_env={"STUB_PORT_MAP": "nodered=0.0.0.0:1880", "STUB_NC_RC": "1"}
    )
    assert (
        "❌ Node-RED: nothing listening on localhost:1880. Next: docker compose ps nodered"
        in result.stdout
    )


def test_unpublished_port_on_a_stopped_service_fails(deployment):
    """Not published must never hide not running."""
    result, _ = run_start(
        deployment,
        extra_env={"STUB_PORT_MAP": "nodered=invalid IP:0", "STUB_PS_EMPTY": "nodered"},
    )
    assert "❌ Node-RED: nodered is not running. Next: docker compose ps nodered" in result.stdout


def test_healthy_stack_passes_every_check(deployment):
    """The spec's headline: a subdomain-mode start on a healthy stack ends
    green. Every stub answer and fixture file below exists for this line."""
    result, _ = run_start(deployment)
    assert result.returncode == 0, result.stderr
    assert "❌" not in result.stdout, result.stdout
    assert "✅ All checks passed (32/32)" in result.stdout


def test_doctor_runs_only_the_checks(deployment):
    """--doctor: no build, no up, no wait; the checklist runs; exit 0 when green."""
    result, calls = run_start(deployment, "--doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(" build" in c for c in calls)
    assert not any(" up " in c for c in calls)
    assert any(c.startswith("exec") for c in calls), "the checks exec into tak-server"
    assert "FastTAK doctor" in result.stdout
    assert "FastTAK is running" not in result.stdout
    assert "✅ All checks passed (" in result.stdout


def test_doctor_exits_one_when_a_check_fails(deployment):
    result, _ = run_start(deployment, "--doctor", extra_env={"STUB_INSPECT_EXITCODE": "1"})
    assert result.returncode == 1
    assert "❌ init-config exited 0" in result.stdout


def test_doctor_does_not_provision_secrets(deployment):
    """doctor changes nothing on the host. The fixture's .env has an empty
    TAK_DB_PASSWORD; a normal start fills it, doctor must not."""
    before = (deployment / ".env").read_text()
    assert "\nTAK_DB_PASSWORD=\n" in before
    run_start(deployment, "--doctor")
    assert (deployment / ".env").read_text() == before
    run_start(deployment)
    assert "\nTAK_DB_PASSWORD=\n" not in (deployment / ".env").read_text()


@pytest.mark.parametrize(
    "args",
    [
        ("--doctor", "monitor"),
        ("--doctor", "--capture"),
        ("--doctor", "--no-wait"),
        ("--doctor", "--no-checks"),
        ("--doctor", "--checks"),
    ],
)
def test_doctor_takes_no_other_option(deployment, args):
    result, _ = run_start(deployment, *args)
    assert result.returncode == 2, args
    assert "--doctor" in result.stderr


def test_doctor_with_verbose_prints_passes(deployment):
    result, _ = run_start(deployment, "--doctor", "--verbose")
    assert result.returncode == 0
    assert "✅ TAK Server healthy" in result.stdout


def test_doctor_lists_the_published_ports(deployment):
    result, _ = run_start(deployment, "--doctor")
    assert result.returncode == 0, result.stdout
    assert "Published ports (subdomain):" in result.stdout
    assert "caddy  443/udp" in result.stdout
    assert "tak-server  8089/tcp" in result.stdout
    assert "cloud firewall" in result.stdout


def test_doctor_fails_on_a_port_outside_the_expected_set(deployment):
    """The case that catches a stray docker-compose.override.yml."""
    extra = SUBDOMAIN_PUBLISHED + "\n" + _publishers(("app-db", 5432, "tcp"))
    result, _ = run_start(deployment, "--doctor", extra_env={"STUB_PS_JSON": extra})
    assert result.returncode == 1
    assert (
        "❌ Published port 5432/tcp on app-db: not in the subdomain set. "
        "Next: ls docker-compose.override.yml compose.override.yml compose.override.yaml"
        in result.stdout
    )


def test_doctor_notes_an_expected_port_that_is_not_published(deployment):
    without_cot = _publishers(
        ("caddy", 80, "tcp"),
        ("caddy", 443, "tcp"),
        ("caddy", 443, "udp"),
        ("tak-server", 8443, "tcp"),
        ("tak-server", 8446, "tcp"),
        ("mediamtx", 8554, "tcp"),
        ("mediamtx", 1935, "tcp"),
    )
    result, _ = run_start(
        deployment, "--doctor", "--verbose", extra_env={"STUB_PS_JSON": without_cot}
    )
    assert result.returncode == 0, result.stdout
    assert "– Expected port 8089/tcp is not published" in result.stdout


def test_doctor_direct_mode_expects_the_ui_ports(deployment):
    env = (deployment / ".env").read_text().replace("DEPLOY_MODE=subdomain", "DEPLOY_MODE=direct")
    (deployment / ".env").write_text(env)
    direct = (
        SUBDOMAIN_PUBLISHED
        + "\n"
        + _publishers(
            ("caddy", 1880, "tcp"),
            ("caddy", 1880, "udp"),
            ("caddy", 8180, "tcp"),
            ("caddy", 8180, "udp"),
            ("caddy", 8888, "tcp"),
            ("caddy", 8888, "udp"),
        )
    )
    result, _ = run_start(deployment, "--doctor", extra_env={"STUB_PS_JSON": direct})
    assert result.returncode == 0, result.stdout
    assert "Published ports (direct):" in result.stdout


def test_doctor_skips_the_report_when_compose_gives_nothing(deployment):
    result, _ = run_start(deployment, "--doctor", extra_env={"STUB_PS_JSON": ""})
    assert result.returncode == 0, result.stdout
    assert (
        "Published ports: skipped — docker compose ps --format gave nothing "
        "(is the stack running?)" in result.stdout
    )


def test_a_normal_start_does_not_print_the_report(deployment):
    result, _ = run_start(deployment)
    assert "Published ports" not in result.stdout


def test_bare_start_checks_the_monitor(deployment):
    result, _ = run_start(deployment, extra_env={"STUB_PS_EMPTY": "monitor"})
    assert (
        '❌ Monitor healthy: expected "healthy", got "unknown". '
        "Next: docker compose logs monitor" in result.stdout
    )


def test_stopped_service_reads_unknown_not_empty(deployment):
    """A container that is not running yields an empty id from `compose ps`;
    the health-status checks must read that as "unknown", not blank."""
    result, _ = run_start(deployment, extra_env={"STUB_PS_EMPTY": "tak-database"})
    assert (
        '❌ TAK Database healthy: expected "healthy", got "unknown". '
        "Next: docker compose logs tak-database" in result.stdout
    )


def test_doctor_reports_when_python3_is_missing(deployment):
    python3 = deployment / "bin" / "python3"
    python3.write_text("#!/bin/sh\nexit 127\n")
    python3.chmod(0o755)
    result, _ = run_start(deployment, "--doctor")
    assert result.returncode == 0, result.stdout
    assert "Published ports: skipped — python3 not found" in result.stdout
    assert "Published ports (subdomain):" not in result.stdout


def test_doctor_tolerates_a_malformed_publisher(deployment):
    broken = (
        SUBDOMAIN_PUBLISHED
        + '\nbroken {"not": "a list"}'
        + '\nodd [{"URL":"0.0.0.0","TargetPort":1,"PublishedPort":"abc","Protocol":"tcp"}]'
    )
    result, _ = run_start(deployment, "--doctor", extra_env={"STUB_PS_JSON": broken})
    assert result.returncode == 0, result.stdout
    assert "tak-server  8089/tcp" in result.stdout


def test_failed_healthcheck_points_at_the_incident_file(deployment):
    """healthcheck.sh snapshots the log tails on the first failure of an
    incident and sets the marker; the start check names the newest snapshot
    so the operator opens evidence, not a rotated stub."""
    incidents = deployment / "tak" / "logs" / "incident"
    incidents.mkdir(parents=True)
    (incidents / "20260915T120000Z-api-probe.log").write_text("== api-probe tripped ==\n")
    (incidents / "20260915T090000Z-oom.log").write_text("== oom tripped ==\n")
    (incidents / ".tripped").write_text("")
    result, _ = run_start(
        deployment,
        extra_env={
            "STUB_HEALTHCHECK_OUT": (
                "UNHEALTHY: https://localhost:8446/Marti/api/version returned HTTP 503 "
                "(the API is up but failing)"
            ),
            "STUB_HEALTHCHECK_RC": "1",
        },
    )
    assert (
        "❌ TAK Server processes: healthcheck.sh said: UNHEALTHY: "
        "https://localhost:8446/Marti/api/version returned HTTP 503 "
        "(the API is up but failing); newest incident snapshot: "
        "tak/logs/incident/20260915T120000Z-api-probe.log. "
        "Next: docker compose logs tak-server" in result.stdout
    )


def test_failed_healthcheck_ignores_stale_snapshots_without_a_marker(deployment):
    """No .tripped marker means no incident is open — old *.log files are
    leftovers from a prior incident, not evidence for this failure."""
    incidents = deployment / "tak" / "logs" / "incident"
    incidents.mkdir(parents=True)
    (incidents / "20260915T120000Z-api-probe.log").write_text("== api-probe tripped ==\n")
    result, _ = run_start(
        deployment,
        extra_env={
            "STUB_HEALTHCHECK_OUT": "UNHEALTHY: missing processes: api",
            "STUB_HEALTHCHECK_RC": "1",
        },
    )
    assert (
        "❌ TAK Server processes: healthcheck.sh said: UNHEALTHY: missing processes: api. "
        "Next: docker compose logs tak-server" in result.stdout
    )
    assert "incident snapshot" not in result.stdout


def test_failed_healthcheck_without_an_incident_file_says_so_plainly(deployment):
    result, _ = run_start(
        deployment,
        extra_env={
            "STUB_HEALTHCHECK_OUT": "UNHEALTHY: missing processes: api",
            "STUB_HEALTHCHECK_RC": "1",
        },
    )
    assert (
        "❌ TAK Server processes: healthcheck.sh said: UNHEALTHY: missing processes: api. "
        "Next: docker compose logs tak-server" in result.stdout
    )
