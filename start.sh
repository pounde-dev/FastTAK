#!/bin/bash
# start.sh — Start and verify FastTAK

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# The shared .env reader, so this script and scripts/check-env.sh resolve a
# value identically. It follows Compose's own dotenv semantics — notably
# stripping surrounding quotes, which the `grep | cut -d= -f2` this replaced did
# not: `DEPLOY_MODE="direct"` read as `"direct"` here, matched nothing, and
# silently selected the subdomain compose files — while Compose, parsing the
# same .env for its own variable substitution, had resolved it to `direct`.
[ -r "$SCRIPT_DIR/scripts/lib-env.sh" ] || {
  echo "ERROR: scripts/lib-env.sh is missing; cannot read .env." >&2
  exit 1
}
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/scripts/lib-env.sh"

# shellcheck source=scripts/lib-stack.sh
. "$SCRIPT_DIR/scripts/lib-stack.sh"

# The deployment this run operates on. FASTAK_ENV_FILE points the preflight
# and Compose at another directory, which is how the test suite drives this
# script against a scratch deployment.
ENV_FILE="${FASTAK_ENV_FILE:-$SCRIPT_DIR/.env}"
DEPLOY_DIR="$(cd "$(dirname "$ENV_FILE")" && pwd)"

compose() { docker compose --env-file "$ENV_FILE" "$@"; }

PASS=0
FAIL=0
VERBOSE=false    # --verbose: print passes and notes, not only failures

CAPTURE=false
WAIT=true
CHECKS=""        # empty: decided below from whether services were named
SERVICES=()
DOCTOR=false

usage() {
  cat <<'EOF'
Usage: ./start.sh [--capture] [--checks|--no-checks] [--no-wait] [--verbose] [service...]
       ./start.sh --doctor [--verbose]

Start the stack, wait for tak-server, and verify it.

  service...    rebuild and force-recreate only these services
  --capture     include the mitmproxy capture overlay
  --checks      run the post-start checks (default for a whole-stack start)
  --no-checks   skip them (default when services are named)
  --no-wait     do not wait for tak-server to report healthy (skips the checks unless --checks is given)
  --verbose     print every check, not only the failures
  --doctor      check the running stack without touching it; exit 1 on any failure
  -h, --help    show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)   usage; exit 0 ;;
    --capture)   CAPTURE=true ;;
    --checks)    CHECKS=true ;;
    --no-checks) CHECKS=false ;;
    --no-wait)   WAIT=false ;;
    --verbose)   VERBOSE=true ;;
    --doctor)    DOCTOR=true ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) SERVICES+=("$1") ;;
  esac
  shift
done

# doctor is a read-only mode: it inspects whatever is running and takes no
# option that would change that.
if $DOCTOR; then
  if $CAPTURE || ! $WAIT || [ -n "$CHECKS" ] || [ ${#SERVICES[@]} -gt 0 ]; then
    echo "--doctor checks the running stack and takes no other option" >&2
    usage >&2
    exit 2
  fi
  CHECKS=true
fi

# The checks need a healthy tak-server to inspect; skipping the wait means
# they would run against a stack that may not be up yet. The invocation says
# which was meant; an explicit --checks overrides.
if ! $WAIT && [ -z "$CHECKS" ]; then
  CHECKS=false
fi

# A targeted rebuild cannot affect CoreConfig, the certs or TAK's processes,
# so the whole-stack checks are noise there. The invocation says which was
# meant; the flags override.
if [ -z "$CHECKS" ]; then
  if [ ${#SERVICES[@]} -gt 0 ]; then CHECKS=false; else CHECKS=true; fi
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

log() { if $VERBOSE; then echo "  $1"; fi; }

pass() {
  PASS=$((PASS + 1))
  if $VERBOSE; then echo "  ✅ $1"; fi
}

# fail <label> [detail] [next]
# One line an operator can act on: what was checked, what came back, what
# to run. "❌ Port 0 (Node-RED)" cost a debugging session (#120, #114).
fail() {
  FAIL=$((FAIL + 1))
  local line="  ❌ $1"
  [ -n "${2:-}" ] && line="$line: $2"
  [ -n "${3:-}" ] && line="$line. Next: $3"
  echo "$line"
}

# assert <value> <expected> <label> [next]
assert()      { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3" "expected \"$2\", got \"$1\"" "${4:-}"; fi; }
# assert_not <value> <unexpected> <label> [next]
assert_not()  { if [ "$1" != "$2" ]; then pass "$3"; else fail "$3" "got \"$1\"" "${4:-}"; fi; }
# assert_file <path> <label> [next]
assert_file() { if [ -f "$1" ]; then pass "$2"; else fail "$2" "$1 is missing" "${3:-}"; fi; }
# assert_grep <pattern> <file> <label> [next]
assert_grep() { if grep -q "$1" "$2" 2>/dev/null; then pass "$3"; else fail "$3" "\"$1\" not found in $2" "${4:-}"; fi; }
# assert_port <port> <label> [next]
assert_port() { if nc -z localhost "$1" 2>/dev/null; then pass "$2 (port $1)"; else fail "$2" "nothing listening on localhost:$1" "${3:-}"; fi; }

# A condition that is legitimately absent rather than broken. Not counted as a
# failure — a start script that reports "5 checks failed" on a healthy stack
# trains the operator to ignore it.
note() { if $VERBOSE; then echo "  – $1"; fi; }

# Verify a port only if this deployment actually publishes it. Which ports are
# published depends on DEPLOY_MODE (docker-compose.direct.yml adds the UI
# ports) and on the operator's docker-compose.override.yml, which may remove
# some deliberately. Asking compose is the only reading that stays true across
# both.
assert_published_port() {
  local _svc="$1" _cport="$2" _label="$3" _mapping _hport
  _mapping=$(compose port "$_svc" "$_cport" 2>/dev/null | head -1)
  _hport="${_mapping##*:}"
  # Compose prints "invalid IP:0" (older releases ":0") for a port the image
  # exposes but nothing publishes, and nothing at all for a port it does not
  # know. Both mean "not published"; only a real host port is worth probing (#120).
  if [ -n "$_hport" ] && [ "$_hport" != "0" ]; then
    assert_port "$_hport" "$_label" "docker compose ps $_svc"
    return
  fi
  # No mapping. Distinguish "deliberately not published" from "the service is
  # not running" — the latter would otherwise read as a config choice.
  if [ -z "$(compose ps -q "$_svc" 2>/dev/null)" ]; then
    fail "$_label" "$_svc is not running" "docker compose ps $_svc"
  else
    note "$_label not published by this compose configuration"
  fi
}

# Build, start, and wait for tak-server. Exits the script on a failed build
# or up, or a tak-server that never reports healthy.
run_start() {
  log ""
  log "Start"
  log "─────"

  echo "  ⏳ Building containers..."
  if [ ${#SERVICES[@]} -gt 0 ]; then
    BUILD_ERR=$(compose build --quiet "${SERVICES[@]}" 2>&1 >/dev/null); BUILD_RC=$?
  else
    BUILD_ERR=$(compose build --quiet 2>&1 >/dev/null); BUILD_RC=$?
  fi
  if [ "$BUILD_RC" -ne 0 ]; then
    echo "  ❌ docker compose build failed:" >&2
    printf '%s\n' "$BUILD_ERR" >&2
    exit 1
  fi

  echo "  ⏳ Starting services..."
  # --remove-orphans: containers for services deleted from the compose file are
  # NOT removed by a plain `up`; tak-portal kept running for months after
  # DD-043 removed it. Only for a whole-stack up — a targeted rebuild must not
  # prune the project, which is what silently removed the capture sidecars.
  if [ ${#SERVICES[@]} -gt 0 ]; then
    UP_ERR=$(compose up -d --force-recreate "${SERVICES[@]}" 2>&1 >/dev/null); UP_RC=$?
  else
    UP_ERR=$(compose up -d --remove-orphans 2>&1 >/dev/null); UP_RC=$?
  fi
  if [ "$UP_RC" -ne 0 ]; then
    echo "  ❌ docker compose up failed:" >&2
    printf '%s\n' "$UP_ERR" >&2
    exit 1
  fi

  if $WAIT; then
    echo "  ⏳ Waiting for tak-server..."
    STATUS="unknown"
    for _ in $(seq 1 48); do
      STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q tak-server 2>/dev/null)" 2>/dev/null || echo "unknown")
      if [ "$STATUS" = "healthy" ]; then break; fi
      if [ "$STATUS" = "unhealthy" ]; then
        echo "  ❌ tak-server failed — run: docker compose logs tak-server"
        exit 1
      fi
      sleep 10
    done

    if [ "$STATUS" != "healthy" ]; then
      echo "  ❌ tak-server timed out — run: docker compose logs tak-server"
      exit 1
    fi
  fi
}

# The post-start checklist. Every check calls pass/fail/note; the RESULTS
# section reads PASS and FAIL.
run_checks() {
  log ""
  log "Services"
  log "────────"

  TAK_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q tak-server 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$TAK_STATUS" "healthy" "TAK Server healthy" "docker compose logs tak-server"

  DB_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q tak-database 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$DB_STATUS" "healthy" "TAK Database healthy" "docker compose logs tak-database"

  INIT_EXIT=$(docker inspect --format='{{.State.ExitCode}}' "$(compose ps -aq init-config 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$INIT_EXIT" "0" "init-config exited 0" "docker compose logs init-config"

  ID_EXIT=$(docker inspect --format='{{.State.ExitCode}}' "$(compose ps -aq init-identity 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$ID_EXIT" "0" "init-identity exited 0" "docker compose logs init-identity"

  LLDAP_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q lldap 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$LLDAP_STATUS" "healthy" "LLDAP healthy" "docker compose logs lldap"

  PROXY_STATE=$(docker inspect --format='{{.State.Status}}' "$(compose ps -q ldap-proxy 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$PROXY_STATE" "running" "ldap-proxy running" "docker compose logs ldap-proxy"

  MONITOR_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q monitor 2>/dev/null)" 2>/dev/null || echo unknown)
  assert "$MONITOR_STATUS" "healthy" "Monitor healthy" "docker compose logs monitor"

  log ""
  log "Config"
  log "──────"

  assert_file "$DEPLOY_DIR/tak/CoreConfig.xml" "CoreConfig.xml" "docker compose logs init-config"
  CC_PASS=$(grep -o '<connection[^>]*password="[^"]*"' "$DEPLOY_DIR/tak/CoreConfig.xml" | sed 's/.*password="//;s/"//')
  assert_not "$CC_PASS" "" "DB password set" "grep -n '<connection' tak/CoreConfig.xml"
  assert_grep "tak-database:5432" "$DEPLOY_DIR/tak/CoreConfig.xml" "DB host" "grep -n 'tak-database:5432' tak/CoreConfig.xml"
  assert_grep 'enableAdminUI="true"' "$DEPLOY_DIR/tak/CoreConfig.xml" "Admin UI enabled" "grep -n 'enableAdminUI' tak/CoreConfig.xml"
  assert_grep '<certificateSigning CA="TAKServer">' "$DEPLOY_DIR/tak/CoreConfig.xml" "Certificate signing" "grep -n 'certificateSigning' tak/CoreConfig.xml"
  assert_grep "adm_ldapservice" "$DEPLOY_DIR/tak/CoreConfig.xml" "LDAP auth" "grep -n 'adm_ldapservice' tak/CoreConfig.xml"
  assert_grep 'crlFile="certs/files/ca.crl"' "$DEPLOY_DIR/tak/CoreConfig.xml" "Revocation list read by TAK Server" "docker compose logs init-config | grep -i revocation"
  assert_grep 'adminGroup="ROLE_ADMIN"' "$DEPLOY_DIR/tak/CoreConfig.xml" "ROLE_ADMIN" "grep -n 'adminGroup' tak/CoreConfig.xml"

  log ""
  log "Certificates"
  log "────────────"

  assert_file "$DEPLOY_DIR/tak/certs/files/root-ca.pem" "Root CA" "docker compose logs tak-server"
  assert_file "$DEPLOY_DIR/tak/certs/files/ca.pem" "Intermediate CA" "docker compose logs tak-server"
  assert_file "$DEPLOY_DIR/tak/certs/files/takserver.jks" "Server cert" "docker compose logs tak-server"
  assert_file "$DEPLOY_DIR/tak/certs/files/svc_fasttakapi.p12" "API service cert" "docker compose logs init-identity"
  # svc_nodered is NOT created at bootstrap — init-identity's SERVICE_ACCOUNTS
  # is just svc_fasttakapi. The monitor writes these PEMs when a data-mode
  # service account is created, so on a fresh install the file is absent and
  # that is correct.
  if [ -f "$DEPLOY_DIR/tak/certs/files/svc_nodered.p12" ]; then
    pass "Node-RED service cert"
  else
    note "Node-RED service cert not present (created on demand via the Monitor)"
  fi
  assert_file "$DEPLOY_DIR/tak/certs/files/ca-signing.jks" "CA signing keystore" "docker compose logs tak-server"
  if ./certs.sh ca-info > /dev/null 2>&1; then pass "certs.sh ca-info"; else fail "certs.sh ca-info" "exited non-zero" "./certs.sh ca-info"; fi
  if ./certs.sh list > /dev/null 2>&1; then pass "certs.sh list"; else fail "certs.sh list" "exited non-zero" "./certs.sh list"; fi

  log ""
  log "Ports"
  log "─────"

  assert_published_port tak-server 8089 "CoT TLS"
  assert_published_port tak-server 8443 "Cert HTTPS"
  assert_published_port tak-server "$TAKSERVER_ADMIN_PORT" "Admin HTTPS"
  assert_published_port mediamtx "$MEDIAMTX_PORT" "MediaMTX HLS"
  assert_published_port mediamtx 8554 "MediaMTX RTSP"
  assert_published_port nodered "$NODERED_PORT" "Node-RED"

  HTTP_8446=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "https://localhost:${TAKSERVER_ADMIN_PORT}" 2>/dev/null)
  assert_not "$HTTP_8446" "000" "Admin HTTPS TLS on ${TAKSERVER_ADMIN_PORT}" "docker compose logs tak-server"

  log ""
  log "Health"
  log "──────"

  # Delegate to the container's own healthcheck rather than counting with
  # `ps`: the TAK 5.8 hardened image ships no procps, so `ps aux` fails and
  # every process reads as missing on a perfectly healthy server. The
  # healthcheck matches /proc/*/cmdline and already knows the five processes.
  if TAK_HEALTH=$(docker exec "$(compose ps -q tak-server)" /opt/tak/healthcheck.sh 2>&1); then
    pass "TAK Server processes ($TAK_HEALTH)"
  else
    # healthcheck.sh captures the log tails on the first failure of an incident
    # (tak/logs/incident/) and sets .tripped for its duration; only look for a
    # snapshot while that marker is present, so a stale *.log left over from a
    # past, already-cleared incident is never named as if it were this failure.
    INCIDENT=""
    if [ -f "$DEPLOY_DIR/tak/logs/incident/.tripped" ]; then
      # shellcheck disable=SC2012  # filenames are our own generated timestamp-pid-check.log, never adversarial
      INCIDENT=$(ls -1 "$DEPLOY_DIR/tak/logs/incident"/*.log 2>/dev/null | sort -r | head -n 1)
    fi
    fail "TAK Server processes" \
      "healthcheck.sh said: $TAK_HEALTH${INCIDENT:+; newest incident snapshot: ${INCIDENT#"$DEPLOY_DIR/"}}" \
      "docker compose logs tak-server"
  fi

  DB_FAILS=$(docker exec "$(compose ps -q tak-server)" grep -c "password authentication failed" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
  DB_FAILS="${DB_FAILS:-0}"
  if [ "$DB_FAILS" -le 2 ] 2>/dev/null; then pass "DB auth (failures: $DB_FAILS)"; else fail "DB auth" "$DB_FAILS \"password authentication failed\" lines in takserver.log" "docker compose logs tak-database"; fi

  OOM=$(docker exec "$(compose ps -q tak-server)" grep -c "OutOfMemoryError" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
  OOM="${OOM:-0}"
  assert "$OOM" "0" "No OutOfMemoryError" "docker compose logs tak-server"

  SEC_COUNT=$(docker exec "$(compose ps -q tak-server)" grep -c "Security status" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
  SEC_COUNT="${SEC_COUNT:-0}"
  if [ "$SEC_COUNT" -le 4 ] 2>/dev/null; then pass "Single start (status: $SEC_COUNT)"; else fail "Single start" "$SEC_COUNT \"Security status\" lines in takserver.log" "docker compose logs tak-server"; fi
}

# What this host publishes, against what the deploy mode is expected to bind.
# Read from `compose ps` so an operator's override file counts. An extra
# port is a failure (a stray override), a missing one a note (a removed port
# can be deliberate; "not running" is already caught by the port checks).
report_published_ports() {
  local raw published expected svc port parse_rc
  command -v python3 >/dev/null 2>&1 || {
    echo "  Published ports: skipped — python3 not found on this host"
    return
  }
  raw=$(compose ps --format '{{.Service}} {{json .Publishers}}' 2>/dev/null) || raw=""
  if [ -z "$raw" ]; then
    echo "  Published ports: skipped — docker compose ps --format gave nothing (is the stack running?)"
    return
  fi
  # "svc port/proto" per line, host-bound only, one row per port (Compose
  # lists IPv4 and IPv6 separately). A line whose Publishers is not a list, or
  # whose PublishedPort is not an integer, is skipped rather than aborting the
  # whole report.
  published=$(printf '%s\n' "$raw" | python3 -c '
import json, sys
seen = set()
for line in sys.stdin:
    svc, _, rest = line.strip().partition(" ")
    try:
        pubs = json.loads(rest or "null") or []
        for p in pubs:
            if not p.get("PublishedPort"):
                continue
            key = (svc, int(p["PublishedPort"]), p.get("Protocol", "tcp"))
            if key not in seen:
                seen.add(key)
                print(f"{svc} {key[1]}/{key[2]}")
    except (ValueError, AttributeError, TypeError):
        continue
')
  parse_rc=$?
  if [ "$parse_rc" -eq 127 ]; then
    # A python3 on PATH that fails to execute at all reports 127, the shell's
    # own "command not found" convention — the same as a genuinely absent one.
    echo "  Published ports: skipped — python3 not found on this host"
    return
  elif [ "$parse_rc" -ne 0 ]; then
    echo "  Published ports: skipped — the parser failed (see above)"
    return
  fi
  expected=$(stack_expected_published_ports "$DEPLOY_MODE" "$NODERED_PORT" "$MONITOR_PORT" "$MEDIAMTX_PORT")

  echo ""
  echo "  Published ports ($DEPLOY_MODE):"
  while read -r svc port; do
    [ -z "$svc" ] && continue
    if printf '%s\n' "$expected" | grep -qx "$port"; then
      echo "    $svc  $port"
    else
      echo "    $svc  $port  ← not expected"
      fail "Published port $port on $svc" "not in the $DEPLOY_MODE set" \
        "ls docker-compose.override.yml compose.override.yml compose.override.yaml"
    fi
  done <<EOF
$published
EOF
  while read -r port; do
    [ -z "$port" ] && continue
    printf '%s\n' "$published" | grep -q " $port\$" || note "Expected port $port is not published"
  done <<EOF
$expected
EOF
  echo "    Docker publishes on 0.0.0.0 and bypasses ufw: what the internet reaches is this"
  echo "    set intersected with the cloud firewall, which this host cannot see."
}

# ═══════════════════════════════════════════════════════════════════════════
# PREFLIGHT
# ═══════════════════════════════════════════════════════════════════════════

if [ ! -d "$DEPLOY_DIR/tak" ]; then
  echo "ERROR: tak/ not found. Run: ./setup.sh <zip>" >&2; exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found. Run: ./setup.sh <zip>" >&2; exit 1
fi
# Provision here as well as in setup.sh: a FastTAK-only upgrade is `git pull`
# with no new TAK zip, which never runs setup.sh. The launch is the one step
# every upgrade path takes. This script does not use `set -e`, so the exit
# status is checked explicitly.
# doctor changes nothing, so it does not provision either.
if ! $DOCTOR; then
  if ! "$SCRIPT_DIR/scripts/ensure-secrets.sh" "$ENV_FILE"; then
    exit 1
  fi
fi
if ! "$SCRIPT_DIR/scripts/check-env.sh" "$ENV_FILE"; then
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════
# START
# ═══════════════════════════════════════════════════════════════════════════

SERVER_ADDRESS=$(env_get "$ENV_FILE" SERVER_ADDRESS)
DEPLOY_MODE=$(env_get "$ENV_FILE" DEPLOY_MODE)
DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"
TAKSERVER_ADMIN_PORT=$(env_get "$ENV_FILE" TAKSERVER_ADMIN_PORT)
TAKSERVER_ADMIN_PORT="${TAKSERVER_ADMIN_PORT:-8446}"
MEDIAMTX_PORT=$(env_get "$ENV_FILE" MEDIAMTX_PORT)
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8888}"
NODERED_PORT=$(env_get "$ENV_FILE" NODERED_PORT)
NODERED_PORT="${NODERED_PORT:-1880}"
MONITOR_PORT=$(env_get "$ENV_FILE" MONITOR_PORT)
MONITOR_PORT="${MONITOR_PORT:-8180}"

if $CAPTURE; then
  FASTAK_ENV_FILE="$ENV_FILE" stack_export_compose_file --capture
  mkdir -p captures capture/mitm
else
  FASTAK_ENV_FILE="$ENV_FILE" stack_export_compose_file
fi
stack_export_version

echo ""
echo "╔══════════════════════════════════════════╗"
if $DOCTOR; then
  echo "║       FastTAK doctor                     ║"
else
  echo "║       Starting FastTAK                   ║"
fi
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Address: $SERVER_ADDRESS"
echo "  Mode:    $DEPLOY_MODE"
echo ""

if ! $DOCTOR; then run_start; fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════════

if $CHECKS; then
  run_checks
  if $DOCTOR; then report_published_ports; fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════════

if $CHECKS; then
  TOTAL=$((PASS + FAIL))
  if [ $FAIL -eq 0 ]; then
    echo "  ✅ All checks passed ($PASS/$TOTAL)"
  else
    echo "  ⚠️  $FAIL checks failed ($PASS/$TOTAL passed) — re-run with --verbose to see every check"
  fi
else
  echo "  – Checks skipped (targeted start). Run ./start.sh --checks to verify the whole stack."
fi

if $DOCTOR; then
  if [ "$FAIL" -eq 0 ]; then exit 0; else exit 1; fi
fi

WA_PASS=$(env_get "$ENV_FILE" TAK_WEBADMIN_PASSWORD)
WA_MASKED="${WA_PASS:0:4}***"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       FastTAK is running                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  TAK Server:  https://${SERVER_ADDRESS}:${TAKSERVER_ADMIN_PORT}"
echo "               webadmin / ${WA_MASKED}"
if [ "$DEPLOY_MODE" = "direct" ]; then
  echo "  Monitor:     https://${SERVER_ADDRESS}:${MONITOR_PORT}"
else
  MONITOR_SUB=$(env_get "$ENV_FILE" MONITOR_SUBDOMAIN)
  MONITOR_SUB="${MONITOR_SUB:-monitor}"
  echo "  Monitor:     https://${MONITOR_SUB}.${SERVER_ADDRESS}"
fi
echo ""
echo "  Passwords:   cat .env"
echo "  Stop:        just down"
echo ""
