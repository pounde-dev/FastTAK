#!/bin/bash
# scripts/ensure-secrets.sh — provision required secrets in a .env file.
#
# Usage: ./scripts/ensure-secrets.sh <path-to-.env>
#
# Desired-state, not scenario-based: for every required key, if it is absent
# from the file it is appended; if it is present but empty it is filled;
# anything already set is left alone. Fresh installs and upgrades run the same
# code, so a secret introduced by a later release reaches deployments that
# already exist — a fresh-install-only branch cannot do that, which is how
# TOKENS_API_SECRET came to be missing from every upgraded .env.
#
# Prints the name of each key generated. Never prints a value.
#
# Note: the rewrite path replaces the file's inode. Containers that bind-mount
# .env directly (docker-compose.yml mounts ./.env into the monitor) keep the old
# inode until recreated, so provisioning is expected to run before the stack is
# brought up, which is where both callers invoke it.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/lib-env.sh"

ENV_FILE="${1:-}"
if [ -z "$ENV_FILE" ]; then
  echo "Usage: $0 <path-to-.env>" >&2
  exit 2
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  echo "Run ./setup.sh <takserver-docker-X.X.zip> first." >&2
  exit 1
fi

# Required secrets, and the number of random bytes each gets. Parallel arrays
# rather than an associative array: macOS ships bash 3.2, which has no
# `declare -A`.
#
# TAK_WEBADMIN_PASSWORD is deliberately absent. Empty means "skip webadmin user
# creation" (see .env.example), so filling it on an upgrade would undo a
# deliberate choice. setup.sh still generates it on fresh installs only.
SECRET_KEYS=(TAK_DB_PASSWORD APP_DB_PASSWORD LDAP_BIND_PASSWORD TOKENS_API_SECRET MEDIAMTX_VIEWER_PASSWORD MEDIAMTX_PUBLISHER_PASSWORD MEDIAMTX_API_PASSWORD MONITOR_PROXY_SECRET)
SECRET_BYTES=(16 16 16 32 16 16 16 32)

# env_reject_newline <key> <value>
#
# .env is line-oriented, so a value containing a newline cannot round-trip: the
# write splits the record and the remainder is parsed by Compose as its own key.
# `a<newline>SERVER_ADDRESS=evil` is key injection. Refuse rather than write
# something unreadable.
env_reject_newline() {
  case "$2" in
    *"
"*)
      echo "ERROR: refusing to write $1 — value contains a newline" >&2
      return 1
      ;;
  esac
  return 0
}

# env_set <file> <key> <value>
#
# Rewrites the last line assigning <key>, preserving whatever prefix that line
# carries (leading whitespace, `export`) and replacing only the value. Last
# rather than first because Compose is last-wins and env_get reads with
# `tail -n 1`; rewriting an earlier line would leave the effective value empty.
env_set() {
  local file="$1" key="$2" value="$3" tmp
  env_reject_newline "$key" "$value" || return 1

  tmp="$(mktemp "${file}.XXXXXX")" || return 1
  # Seed the temp file from the original so it carries the same mode and owner,
  # then truncate it with the redirect below. Avoids stat(1), whose flags differ
  # between macOS and GNU, and avoids `mv` widening a 0600 .env to the umask.
  cp -p "$file" "$tmp" || { rm -f "$tmp"; return 1; }

  # The value is passed through the environment and *concatenated*. It is never
  # used as a sub()/gsub() replacement string: there, `&` expands to the matched
  # text and `\1` to a backreference, which would corrupt any value containing
  # them. Passing via ENVIRON rather than -v also avoids awk interpreting
  # backslash escapes in the value.
  if ! FASTAK_KEY="$key" FASTAK_VALUE="$value" awk '
    BEGIN { key = ENVIRON["FASTAK_KEY"]; val = ENVIRON["FASTAK_VALUE"] }
    { line[NR] = $0 }
    $0 ~ "^[[:space:]]*(export[[:space:]]+)?" key "=" { last = NR }
    END {
      for (i = 1; i <= NR; i++) {
        if (i == last) {
          eq = index(line[i], "=")
          printf "%s%s\n", substr(line[i], 1, eq), val
        } else {
          print line[i]
        }
      }
    }
  ' "$file" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
}

# env_append <file> <key> <value>
#
# Adds a key that is not in the file at all — the case that matters for an
# upgrade, where a newly required secret has no line to fill.
env_append() {
  local file="$1" key="$2" value="$3"
  env_reject_newline "$key" "$value" || return 1

  # Without a trailing newline the new assignment would land on the end of the
  # last line. Command substitution strips trailing newlines, so this is empty
  # exactly when the file already ends in one.
  if [ -s "$file" ] && [ -n "$(tail -c 1 "$file")" ]; then
    printf '\n' >> "$file" || return 1
  fi
  printf '%s=%s\n' "$key" "$value" >> "$file" || return 1
}

generated=0
i=0
while [ "$i" -lt "${#SECRET_KEYS[@]}" ]; do
  key="${SECRET_KEYS[$i]}"
  bytes="${SECRET_BYTES[$i]}"
  i=$((i + 1))

  [ -n "$(env_get "$ENV_FILE" "$key")" ] && continue

  if ! value="$(openssl rand -hex "$bytes")" || [ -z "$value" ]; then
    echo "ERROR: could not generate a value for $key" >&2
    exit 1
  fi

  if env_has "$ENV_FILE" "$key"; then
    if ! env_set "$ENV_FILE" "$key" "$value"; then
      echo "ERROR: failed to set $key in $ENV_FILE" >&2
      exit 1
    fi
  else
    if ! env_append "$ENV_FILE" "$key" "$value"; then
      echo "ERROR: failed to add $key to $ENV_FILE" >&2
      exit 1
    fi
  fi

  echo "  Generated $key"
  generated=$((generated + 1))
done

if [ "$generated" -gt 0 ]; then
  echo "  $generated secret(s) generated on this device."
fi

exit 0
