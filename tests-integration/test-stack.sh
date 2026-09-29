#!/usr/bin/env bash
# test-stack.sh — The isolated integration stack: stand it up, run the
# assertions, tear it down, or do the whole cycle.
#
# Usage:
#   ./tests-integration/test-stack.sh up [--foreground] [--no-up]
#   ./tests-integration/test-stack.sh run
#   ./tests-integration/test-stack.sh down [<project>]
#   ./tests-integration/test-stack.sh cycle
#
# Each stack is a unique compose project under /tmp/fastak-test-<ts>/ with
# its own tak/ and .env, and ports offset by +10000 so it can run beside a
# development stack. `up` prints the project name as its last stdout line.
#
# cmd_* are called indirectly below (cmd="cmd_$1"; "$cmd" "$@"), so shellcheck
# can't see the call sites and flags every definition/call as argument-less
# (SC2119/SC2120). SC2012/SC2153 further down are also false positives
# (glob-then-newest via ls is intentional; TEST_DIR comes from a sourced
# state file, not a typo). This directive must precede `set` to apply
# file-wide.
# shellcheck disable=SC2119,SC2120,SC2012,SC2153

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: ./tests-integration/test-stack.sh <command> [options]

  up [--foreground] [--no-up]   stand up an isolated test stack (detached)
      --foreground              re-attach after it is healthy; containers stop
                                when this process dies (for background agents)
      --no-up                   scaffold tak/, .env and the state file only
  run                           run the assertions against the running stack
  down [<project>]              tear down every test stack, or just one
  cycle                         fast suite, then up → run → down

  -h, --help                    show this help
EOF
}

# ── up ─────────────────────────────────────────────────────────────────────
cmd_up() {
  local no_up=false foreground=false arg
  for arg in "$@"; do
    case "$arg" in
      --no-up) no_up=true ;;
      --foreground) foreground=true ;;
      *) echo "up: unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
  done

  local ts project test_dir timeout=300 interval=10
  ts=$(date +%s)
  project="fastak-test-${ts}"
  test_dir="/tmp/${project}"

  export TAK_HOST_PATH="${test_dir}/tak"
  mkdir -p "${test_dir}/backups"
  export BACKUP_DIR="${test_dir}/backups"
  # docker-compose.test.yml mounts the monitor's /host/.env from here.
  export HOST_ENV_FILE="${test_dir}/.env"

  local compose="docker compose -p ${project} -f ${REPO_DIR}/docker-compose.yml -f ${REPO_DIR}/docker-compose.test.yml --env-file ${test_dir}/.env"

  # One stack at a time: tear down anything left from an earlier run.
  cmd_down >&2

  echo "=== Setting up test stack: ${project} ===" >&2
  local zip
  zip=$(find "${REPO_DIR}" -maxdepth 1 -name 'takserver-docker-*.zip' | head -1)
  if [ -z "${zip}" ]; then
    echo "FAIL: No takserver-docker-*.zip found in ${REPO_DIR}" >&2
    exit 1
  fi
  "${REPO_DIR}/setup.sh" -d "${test_dir}" "${zip}" >&2

  sed -i.bak "s/^SERVER_ADDRESS=.*/SERVER_ADDRESS=test.fastak.local/" "${test_dir}/.env"
  sed -i.bak "s/^DEPLOY_MODE=.*/DEPLOY_MODE=direct/" "${test_dir}/.env"
  # Tests publish from the host; the default (publishing off) would refuse
  # them. test_mediamtx_auth.py checks the default separately.
  echo "MEDIAMTX_PUBLISH_IPS=0.0.0.0/0" >> "${test_dir}/.env"
  rm -f "${test_dir}/.env.bak"

  # Copied into TAK_HOST_PATH so the single volume mount picks them up —
  # overlaying file bind mounts onto the TAK volume fails on Docker Desktop
  # virtiofs when TAK_HOST_PATH is on a different filesystem.
  cp "${REPO_DIR}/tak-server/healthcheck.sh" "${TAK_HOST_PATH}/healthcheck.sh"
  cp "${REPO_DIR}/tak-server/register-api-cert.sh" "${TAK_HOST_PATH}/register-api-cert.sh"

  # Written before `up` so --no-up callers and a teardown after a failed up
  # can both find the state.
  cat > "${test_dir}/.test-state" <<EOF
PROJECT="${project}"
TEST_DIR="${test_dir}"
TAK_HOST_PATH="${TAK_HOST_PATH}"
REPO_DIR="${REPO_DIR}"
ENV_FILE="${test_dir}/.env"
EOF

  if $no_up; then
    echo "=== Test stack scaffolded (no-up mode) ===" >&2
    echo "  Project: ${project}" >&2
    echo "  State:   ${test_dir}/.test-state" >&2
    echo "${project}"
    return 0
  fi

  echo "=== Starting stack ===" >&2
  ${compose} up -d --build >&2

  echo "=== Waiting for services (timeout: ${timeout}s) ===" >&2
  local elapsed=0 unhealthy
  while [ "$elapsed" -lt "$timeout" ]; do
    unhealthy=$(${compose} ps --format json 2>/dev/null | python3 -c '
import sys, json
bad = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    svc = json.loads(line)
    health, status, name = svc.get("Health", ""), svc.get("State", ""), svc.get("Service", "")
    if status == "exited" and svc.get("ExitCode", 1) == 0:
        continue
    if health == "" and status == "running":
        continue
    if health not in ("healthy", ""):
        bad.append(f"{name}({health})")
    elif status not in ("running", "exited"):
        bad.append(f"{name}({status})")
print(",".join(bad))
' 2>/dev/null || echo "parse-error")
    if [ -z "$unhealthy" ]; then
      echo "  All services healthy after ${elapsed}s" >&2
      break
    fi
    echo "  Waiting... (${elapsed}s) unhealthy: ${unhealthy}" >&2
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  if [ -n "$unhealthy" ]; then
    echo "FAIL: Timed out waiting for services" >&2
    ${compose} ps >&2
    exit 1
  fi

  echo "=== Test stack ready ===" >&2
  echo "  Project: ${project}" >&2
  echo "  State:   ${test_dir}/.test-state" >&2
  echo "${project}"

  if $foreground; then
    echo "=== Switching to foreground (containers stop when this process dies) ===" >&2
    ${compose} stop >&2
    exec ${compose} up
  fi
}

# ── run ────────────────────────────────────────────────────────────────────
cmd_run() {
  [ $# -eq 0 ] || { echo "run takes no arguments" >&2; usage >&2; exit 2; }
  # The stack's own shell exported these; pytest inherits nothing, so derive
  # them from the newest state file. Not FASTAK_TEST_PROJECT: the
  # backup-restore test replaces the stack mid-run, and conftest's glob
  # always finds the live one.
  local state
  state=$(ls -t /tmp/fastak-test-*/.test-state 2>/dev/null | head -1 || true)
  if [ -n "$state" ]; then
    # shellcheck disable=SC1090
    source "$state"
    export TAK_HOST_PATH
    export BACKUP_DIR="${TEST_DIR}/backups"
    export HOST_ENV_FILE="${ENV_FILE}"
  fi
  cd "${REPO_DIR}"
  uv run pytest tests-integration/ -v
}

# ── down ───────────────────────────────────────────────────────────────────
teardown_one() {
  local dir="$1" name
  name=$(basename "$dir")
  echo "=== Tearing down: ${name} ==="
  # Subshell so a sourced state file cannot leak into the next teardown.
  # By project name only — the original compose files may be gone.
  (
    project_name="${name}"
    if [ -f "${dir}/.test-state" ]; then
      # shellcheck disable=SC1090,SC1091
      source "${dir}/.test-state" 2>/dev/null && project_name="${PROJECT:-${name}}"
    fi
    docker compose -p "${project_name}" down -v --remove-orphans 2>/dev/null || true
  )
  rm -rf "$dir"
}

cmd_down() {
  if [ $# -gt 1 ]; then echo "down takes at most one project name" >&2; usage >&2; exit 2; fi
  if [ -n "${1:-}" ]; then
    [[ "$1" =~ ^fastak-test-[0-9]+$ ]] || { echo "down: '$1' is not a test project name" >&2; usage >&2; exit 2; }
    if [ -d "/tmp/$1" ]; then
      teardown_one "/tmp/$1"
    else
      echo "=== Tearing down: $1 (no state directory) ==="
      docker compose -p "$1" down -v --remove-orphans 2>/dev/null || true
    fi
    return 0
  fi
  local found=false dir
  for dir in /tmp/fastak-test-*/; do
    [ -d "$dir" ] || continue
    found=true
    teardown_one "${dir%/}"
  done
  $found || echo "No running test stacks found."
}

# ── cycle ──────────────────────────────────────────────────────────────────
cmd_cycle() {
  [ $# -eq 0 ] || { echo "cycle takes no arguments" >&2; usage >&2; exit 2; }
  "${REPO_DIR}/scripts/test.sh"
  # Armed before up: under set -e a setup failure exits on the spot, and a
  # trap installed afterwards would not exist yet.
  trap 'echo ""; echo "=== Cleaning up ==="; cmd_down 2>/dev/null || true' EXIT INT TERM
  cmd_up >/dev/null
  cmd_run
}

# ── dispatch ───────────────────────────────────────────────────────────────
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") usage >&2; exit 2 ;;
  up|run|down|cycle) cmd="cmd_$1"; shift; "$cmd" "$@" ;;
  *) echo "Unknown command: $1" >&2; usage >&2; exit 2 ;;
esac
