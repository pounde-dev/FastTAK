#!/bin/bash
# App database entrypoint — PostgreSQL shared by LLDAP, Node-RED, and the FastTAK audit store.
#
# On first boot: POSTGRES_DB creates the primary database (lldap).
# This script creates the nodered database if it doesn't exist yet.
#
# On subsequent boots: syncs the role password with POSTGRES_PASSWORD
# in case .env was regenerated while the data volume persisted.
# Works because pg_hba.conf trusts local (Unix socket) connections.
#
# PostGIS is not installed on app-db (DD-036 — official postgres image is
# multi-arch; postgis/postgis is amd64-only). Node-RED flows that need
# spatial queries should point at tak-database, which has PostGIS natively.

# Reap idle connections for good PostgreSQL hygiene.
docker-entrypoint.sh postgres \
  -c max_connections="${PG_APP_MAX_CONNECTIONS:-100}" \
  -c idle_session_timeout=300s \
  -c idle_in_transaction_session_timeout=120s \
  -c tcp_keepalives_idle=60 \
  -c tcp_keepalives_interval=10 \
  -c tcp_keepalives_count=6 &
PG_PID=$!

until pg_isready -U "$POSTGRES_USER" -q; do sleep 1; done

# stop_postgres [<data-directory>]
#
# The fatal branches below exit while that backgrounded postgres is still
# running. PID 1 leaving without stopping it means Docker SIGKILLs the
# postmaster: crash recovery on the next boot, and `restart: unless-stopped`
# loops the container through it. tak-database/start.sh stops the postmaster on
# every fatal branch; this mirrors it.
#
# Prefers the directory the running server reported over $PGDATA, because the
# two disagreeing is exactly what the guard below exists to catch — and pg_ctl
# defaults to $PGDATA. Returns pg_ctl's status so callers warn rather than
# assume a clean shutdown happened.
stop_postgres() {
  local dir="${1:-${PGDATA:-}}" err status
  if [ -n "$dir" ]; then
    err="$(pg_ctl -D "$dir" stop -m fast 2>&1 >/dev/null)"
  else
    err="$(pg_ctl stop -m fast 2>&1 >/dev/null)"
  fi
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "[app-db] pg_ctl stop: ${err:-(no output)}" >&2
  fi
  return "$status"
}

# ── PGDATA persistence guard ────────────────────────────────────────────
# Catches the class of defect that reached review here: a postgres image
# moving PGDATA out from under docker-compose.yml's volume mount, silently.
# Reads the live setting from the running server rather than trusting the
# PGDATA env var, so a mismatch between what's configured and what the
# server actually opened is caught too. See tak-database/start.sh for the
# original pattern this mirrors; the guard script itself is not duplicated.
GUARD="${FASTAK_GUARD:-/opt/fastak/check-pgdata-persistent.sh}"
if [ -x "$GUARD" ]; then
  DATA_DIR="$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SHOW data_directory;" 2>/dev/null | tr -d '[:space:]')"
  if [ -z "$DATA_DIR" ]; then
    echo "[app-db] ERROR: could not read the server's data_directory; the persistence guard cannot run." >&2
    stop_postgres || echo "[app-db] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
    exit 1
  fi
  if ! "$GUARD" "$DATA_DIR"; then
    stop_postgres "$DATA_DIR" || echo "[app-db] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
    exit 1
  fi
  echo "[app-db] PGDATA (${DATA_DIR}) is on a mounted volume."
else
  cat >&2 <<EOF
[app-db] ERROR: ${GUARD} is missing or not executable.

  The guard is bind-mounted from the host, so a mount that did not land or a
  stripped execute bit disables the one check that catches a PGDATA that has
  moved off the volume — while the container still reports a normal startup.
  Continuing would mean running unverified, so this is fatal.

  Confirm app-db's ./tak-database/check-pgdata-persistent.sh mount in
  docker-compose.yml (the guard script is shared, not duplicated), and that
  the file on the host is executable.
EOF
  stop_postgres || echo "[app-db] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
  exit 1
fi

# Clear stale ALTER SYSTEM settings that conflict with command-line args
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "ALTER SYSTEM RESET ALL;" >/dev/null 2>&1

# Sync the superuser password from .env, so rotating APP_DB_PASSWORD takes
# effect. psql does not substitute variables in a -c string, so the password
# goes over stdin and \getenv, which also keeps it out of the argument list.
if ! psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v role="$POSTGRES_USER" <<'SQL'
\getenv newpass POSTGRES_PASSWORD
ALTER ROLE :"role" WITH PASSWORD :'newpass';
SQL
then
  echo "[app-db] WARNING: could not apply APP_DB_PASSWORD from .env; services using it will fail to log in." >&2
fi

# Create nodered database with PostGIS if it doesn't exist
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tc \
  "SELECT 1 FROM pg_database WHERE datname = 'nodered'" |
  grep -q 1 ||
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE DATABASE nodered;" >/dev/null 2>&1

# Create lldap database if it doesn't exist
# This is created by the Docker entrypoint as the default DB
# Left here for completeness and future change protection
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tc \
  "SELECT 1 FROM pg_database WHERE datname = 'lldap'" |
  grep -q 1 ||
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE DATABASE lldap;" >/dev/null 2>&1

# Create fastak database for audit/events store (issue #13)
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tc \
  "SELECT 1 FROM pg_database WHERE datname = 'fastak'" |
  grep -q 1 ||
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE DATABASE fastak;" >/dev/null 2>&1

wait $PG_PID
