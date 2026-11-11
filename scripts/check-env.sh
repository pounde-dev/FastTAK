#!/bin/bash
# scripts/check-env.sh — Preflight .env validator for FastTAK.
# Exits 0 on success, 1 with a clear error message on failure.
# Usage: ./scripts/check-env.sh <path-to-.env>
#
# Every rule below applies unconditionally: none of them varies by DEPLOY_MODE.
# Security defaults are universal; DEPLOY_MODE stays a pure routing/cert choice
# (DD-029). DEPLOY_MODE's own *value* is validated — that is not the same thing
# as varying a rule by it.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/lib-env.sh"
# TAK_VERSION_FLOOR comes from the library too — see its header.
# shellcheck source=scripts/lib-tak-version.sh
. "$SCRIPT_DIR/lib-tak-version.sh"

usage() {
  cat <<'EOF'
Usage: ./scripts/check-env.sh [<path-to-.env>]

Preflight validator. Exits 0 when .env is safe to start on, 1 with the reason
otherwise. Defaults to ./.env.

  -h, --help    show this help
  --report [<path>]   list keys new in this release and keys this release no longer uses; always exits 0
EOF
}

case "${1:-}" in -h|--help) usage; exit 0 ;; esac

# ── --report: .env drift, both directions ─────────────────────────────────
# A set difference each way; never modifies .env; always exits 0 so a launch
# is never blocked by an advisory.
if [ "${1:-}" = "--report" ]; then
  ENV_FILE="${2:-.env}"
  EXAMPLE="$SCRIPT_DIR/../.env.example"
  if [ ! -f "$ENV_FILE" ]; then
    echo "No .env at $ENV_FILE — nothing to compare."
    exit 0
  fi
  example_keys=$(env_keys "$EXAMPLE" | grep -v '^FASTAK_MON_' || true)
  env_keys_here=$(env_keys "$ENV_FILE" | grep -v '^FASTAK_MON_' || true)
  new_keys=$(comm -23 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$env_keys_here") | sed '/^$/d')
  stale_keys=$(comm -13 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$env_keys_here") | sed '/^$/d')
  if [ -z "$new_keys" ] && [ -z "$stale_keys" ]; then
    echo "No drift: $ENV_FILE and .env.example carry the same keys."
    exit 0
  fi
  if [ -n "$new_keys" ]; then
    echo "New in this release, not in $ENV_FILE ($(printf '%s\n' "$new_keys" | wc -l | tr -d ' ')):"
    printf '%s\n' "$new_keys" | sed 's/^/  /'
    echo "  See .env.example for what each does."
  fi
  if [ -n "$stale_keys" ]; then
    echo "In $ENV_FILE, not used by this release ($(printf '%s\n' "$stale_keys" | wc -l | tr -d ' ')) — safe to remove if you didn't add them:"
    printf '%s\n' "$stale_keys" | sed 's/^/  /'
  fi
  exit 0
fi

ENV_FILE="${1:-.env}"
DEFAULT_WEBADMIN_PASSWORD="FastTAK-Admin-1!"

# Shared with ensure-secrets.sh via lib-env.sh, so the validator and the
# provisioner cannot disagree about whether a key is set.
get_env_value() {
  env_get "$ENV_FILE" "$1"
}

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  echo "Run ./setup.sh <takserver-docker-X.X.zip> first." >&2
  exit 1
fi

SERVER_ADDRESS=$(get_env_value SERVER_ADDRESS)
DEPLOY_MODE=$(get_env_value DEPLOY_MODE)
WEBADMIN_PASSWORD=$(get_env_value TAK_WEBADMIN_PASSWORD)
TOKENS_API_SECRET=$(get_env_value TOKENS_API_SECRET)
TAK_VERSION=$(get_env_value TAK_VERSION)
MEDIAMTX_VERSION=$(get_env_value MEDIAMTX_VERSION)

# ── SERVER_ADDRESS ─────────────────────────────────────────────────────────
if [ -z "$SERVER_ADDRESS" ] || [ "$SERVER_ADDRESS" = "tak.example.com" ]; then
  echo "ERROR: SERVER_ADDRESS is unset or still the placeholder in $ENV_FILE." >&2
  echo "Edit $ENV_FILE and set SERVER_ADDRESS to your IP, hostname, or FQDN." >&2
  exit 1
fi

# ── DEPLOY_MODE ────────────────────────────────────────────────────────────
# Every consumer — start.sh, the justfile's up/down recipes, init-config —
# tests for "direct" and treats everything else as "subdomain". So a typo does
# not fail: DEPLOY_MODE=dirct silently drops docker-compose.direct.yml, caddy
# comes up without the Monitor, Node-RED and MediaMTX port publishings, and the
# start reports success. This is the one place that can tell a typo from a
# choice.
#
# Empty stays valid: it is the documented "unset means subdomain" default that
# every consumer already spells `${DEPLOY_MODE:-subdomain}`.
case "$DEPLOY_MODE" in
  ""|direct|subdomain) ;;
  *)
    cat >&2 <<EOF
ERROR: DEPLOY_MODE=$DEPLOY_MODE in $ENV_FILE is not a known deployment mode.

Valid values are:
  direct      port-based routing through Caddy with self-signed TLS
  subdomain   subdomain routing with Let's Encrypt TLS (the default when unset)

Nothing rejects an unknown value at runtime — every consumer reads "not direct"
as subdomain — so "$DEPLOY_MODE" would come up in subdomain mode without the
direct overlay's Monitor, Node-RED and MediaMTX ports, and report success.

Set DEPLOY_MODE to direct or subdomain in $ENV_FILE.
EOF
    exit 1
    ;;
esac

# ── TAK_WEBADMIN_PASSWORD ──────────────────────────────────────────────────
# Empty is permitted — it's the existing "skip webadmin user creation" escape
# hatch from .env.example, preserved so cert-only deployments keep working.
# The documented default is always rejected — it is public knowledge (README
# and .env.example history), so any install running it has effectively
# published its admin credentials.
if [ "$WEBADMIN_PASSWORD" = "$DEFAULT_WEBADMIN_PASSWORD" ]; then
  cat >&2 <<EOF
ERROR: TAK_WEBADMIN_PASSWORD is set to the documented default in $ENV_FILE.

The default password ($DEFAULT_WEBADMIN_PASSWORD) is public knowledge —
documented in prior README versions and .env.example — and must be
changed before the stack can start.

Generate a random replacement:
  NEW_PW=\$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)
  sed -i.bak "s|^TAK_WEBADMIN_PASSWORD=.*|TAK_WEBADMIN_PASSWORD=\${NEW_PW}|" $ENV_FILE && rm -f ${ENV_FILE}.bak

Or set your own strong password in $ENV_FILE.
Or leave it empty to skip webadmin user creation entirely.
EOF
  exit 1
fi

# ── TOKENS_API_SECRET ──────────────────────────────────────────────────────
# ldap-proxy calls log.Fatal without it, init-ldap-ready waits on ldap-proxy
# and tak-server waits on init-ldap-ready, so an empty value takes the whole
# stack down. ensure-secrets.sh normally fills it before this check runs; this
# rule is the backstop for a .env reached some other way, so the failure is a
# named key rather than a stack that never comes up.
if [ -z "$TOKENS_API_SECRET" ]; then
  cat >&2 <<EOF
ERROR: TOKENS_API_SECRET is unset or empty in $ENV_FILE.

ldap-proxy refuses to start without it, and tak-server waits on ldap-proxy,
so the whole stack stays down. An unauthenticated token API would let any
workload on the Docker network mint LDAP credentials for any user (DD-050).

Generate one:
  ./scripts/ensure-secrets.sh $ENV_FILE

Or set it by hand:
  openssl rand -hex 32
EOF
  exit 1
fi

# ── MediaMTX credentials ───────────────────────────────────────────────────
# MediaMTX has no anonymous access (DD-056); docker-compose.yml refuses to
# start without these. The viewer password is embedded in RTSP URLs inside
# CoT, where "@", ":" or "/" would break parsing, so only letters and digits.
for key in MEDIAMTX_VIEWER_PASSWORD MEDIAMTX_PUBLISHER_PASSWORD MEDIAMTX_API_PASSWORD; do
  value=$(get_env_value "$key")
  if [ -z "$value" ]; then
    cat >&2 <<EOF
ERROR: $key is unset or empty in $ENV_FILE.

MediaMTX has no anonymous access, so the stack will not start without it.

Generate it:
  ./scripts/ensure-secrets.sh $ENV_FILE
EOF
    exit 1
  fi
  case "$value" in
    *[!A-Za-z0-9]*)
      cat >&2 <<EOF
ERROR: $key in $ENV_FILE must be letters and digits only.

It is embedded in RTSP URLs, where characters like @ : / break parsing.
Clear it and regenerate:
  ./scripts/ensure-secrets.sh $ENV_FILE
EOF
      exit 1
      ;;
  esac
done

# ── Secrets between services ───────────────────────────────────────────────
# docker-compose.yml refuses to start without these; naming the key here gives
# `--doctor` and a hand-edited .env the same answer before anything starts.
for key in MONITOR_PROXY_SECRET LLDAP_JWT_SECRET; do
  if [ -z "$(get_env_value "$key")" ]; then
    cat >&2 <<EOF
ERROR: $key is unset or empty in $ENV_FILE.

The stack will not start without it. Generate it:
  ./scripts/ensure-secrets.sh $ENV_FILE
EOF
    exit 1
  fi
done

# Caddy pastes MONITOR_PROXY_SECRET into its config as text before parsing it,
# so a space or a brace would change the directive instead of the value.
case "$(get_env_value MONITOR_PROXY_SECRET)" in
  *[!A-Za-z0-9_-]*)
    cat >&2 <<EOF
ERROR: MONITOR_PROXY_SECRET in $ENV_FILE must be letters, digits, "-" and "_" only.

Clear it and regenerate:
  ./scripts/ensure-secrets.sh $ENV_FILE
EOF
    exit 1
    ;;
esac

# ── File mode ──────────────────────────────────────────────────────────────
# Every secret in the stack is in this file. A warning, not an error: some
# hosts share it with a deploy group on purpose.
if [ -n "$(find "$ENV_FILE" -perm -004 2>/dev/null)" ]; then
  echo "WARNING: $ENV_FILE is readable by every user on this host. Run: chmod 600 $ENV_FILE" >&2
fi

# ── TAK_VERSION ────────────────────────────────────────────────────────────
# setup.sh refuses a below-floor release bundle, but .env can be edited by
# hand. Compose interpolates this into the image tags, so an empty value
# produces `takserver:` — an unresolvable tag and a confusing pull error.
if [ -z "$TAK_VERSION" ]; then
  cat >&2 <<EOF
ERROR: TAK_VERSION is unset or empty in $ENV_FILE.

Compose builds the image tags from it, so an empty value leaves the stack
pulling "takserver:" — which does not exist.

Re-run ./setup.sh <takserver-docker-hardened-X.Y-RELEASE-N.zip>, which sets
this from the bundle's tak/version.txt.
EOF
  exit 1
fi

if ! tak_version_major_minor "$TAK_VERSION" >/dev/null; then
  cat >&2 <<EOF
ERROR: TAK_VERSION=$TAK_VERSION in $ENV_FILE could not be parsed.

Expected the form X.Y or X.Y-anything, e.g. 5.8-RELEASE-65.

Check $ENV_FILE for a typo — a patch-style value like 5.8.65 is not the
same as the release-bundle version string 5.8-RELEASE-65.
EOF
  exit 1
fi

if ! tak_version_meets_floor "$TAK_VERSION" "$TAK_VERSION_FLOOR"; then
  cat >&2 <<EOF
ERROR: TAK_VERSION=$TAK_VERSION in $ENV_FILE is below the supported floor of $TAK_VERSION_FLOOR.

FastTAK supports the hardened TAK Server bundle from $TAK_VERSION_FLOOR onward.
Earlier releases place the PostgreSQL data directory outside the volume FastTAK
mounts, so the database would not survive a container recreate.

Re-run ./setup.sh with a current bundle from https://tak.gov/products/tak-server
EOF
  exit 1
fi

# ── MEDIAMTX_VERSION ───────────────────────────────────────────────────────
# Image pins live in each deployment's .env, so a release that needs a newer
# MediaMTX cannot reach an existing install on its own. Below 1.21 MediaMTX
# ignores the MTX_AUTHINTERNALUSERS_* overrides docker-compose.yml relies on
# (DD-056) and every login fails.
MEDIAMTX_VERSION_FLOOR="1.21"

# mediamtx_version_meets_floor <X.Y[.Z]> <X.Y>
# 0 when version >= floor, 1 when below, 2 when the version cannot be parsed.
# Integer compare on major and minor, so 1.100 sorts above 1.21.
mediamtx_version_meets_floor() {
  local v="$1" f="$2" v_major v_rest v_minor f_major f_minor
  v_major="${v%%.*}"
  [ "$v_major" = "$v" ] && return 2
  v_rest="${v#*.}"
  v_minor="${v_rest%%.*}"
  case "$v_major" in ''|*[!0-9]*) return 2 ;; esac
  case "$v_minor" in ''|*[!0-9]*) return 2 ;; esac
  f_major="${f%%.*}"; f_minor="${f#*.}"

  [ "$v_major" -gt "$f_major" ] && return 0
  [ "$v_major" -lt "$f_major" ] && return 1
  [ "$v_minor" -ge "$f_minor" ] && return 0
  return 1
}

floor_rc=0
mediamtx_version_meets_floor "$MEDIAMTX_VERSION" "$MEDIAMTX_VERSION_FLOOR" || floor_rc=$?
case $floor_rc in
  0) ;;
  2)
    cat >&2 <<EOF
ERROR: MEDIAMTX_VERSION=$MEDIAMTX_VERSION in $ENV_FILE could not be parsed.

Expected a release number such as 1.21.1 (no leading "v", not "latest").

Set it in $ENV_FILE:
  MEDIAMTX_VERSION=1.21.1
EOF
    exit 1
    ;;
  *)
    cat >&2 <<EOF
ERROR: MEDIAMTX_VERSION=$MEDIAMTX_VERSION in $ENV_FILE is below $MEDIAMTX_VERSION_FLOOR.

This release configures MediaMTX credentials through environment overrides
that older versions ignore; video would fail for every client.

Set it in $ENV_FILE:
  MEDIAMTX_VERSION=1.21.1
EOF
    exit 1
    ;;
esac

exit 0
