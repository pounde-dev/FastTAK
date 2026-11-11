# Backup and Restore

FastTAK can produce an encrypted backup tarball that captures every piece of
state needed to rebuild a deployment on a fresh host: both Postgres
databases, all TAK certificates and keys, the Node-RED data volume, and
your `.env`. Restoring is a documented manual procedure — there is no
"one button restore" because every restore should be done with a human
reading every step.

## What gets backed up

- TAK Server database (`cot` on `tak-database`) — CoT history, mission data,
  data packages.
- LLDAP database (`lldap` on `app-db`) — users and groups.
- Node-RED database (`nodered` on `app-db`) — data your flows have stored in Postgres.
- FastTAK audit database (`fastak` on `app-db`) — the audit/health event store.
- TAK certificates and CA key (`tak/certs/`) — without these, every device
  must be re-enrolled.
- TAK Server config (`tak/CoreConfig.xml`, `tak/TAKIgniteConfig.xml`) —
  operator customization (federation, channels, auth tweaks, Ignite
  cluster settings). Without these, the restored stack reverts to
  defaults derived from the `*.example.xml` files.
- Node-RED data volume (`nodered-data`) — flow definitions, credentials,
  installed packages, monitor-written cert PEMs.
- `.env` — the entire stack's configuration, including secrets.

**Not** backed up:

- Caddy data — TLS certificates re-issue on first boot.
- The FastTAK source tree — already in version control.
- The extracted `tak/` directory minus `certs/` — re-extracted from the
  TAK Server release zip via `setup.sh`.


## Keep the TAK Server images

The backup does not contain the two TAK Server images, and they cannot always
be built again. The bundle's Dockerfiles install the newest packages from
several repositories at build time; when those repositories are out of step,
the build fails until they catch up, whatever FastTAK version you run.

After a successful `just setup`, export the images and keep the file with your
backups:

```bash
TAK_VERSION=$(grep '^TAK_VERSION=' .env | cut -d= -f2)
docker save "takserver:${TAK_VERSION}" "takserver-database:${TAK_VERSION}" | gzip > "tak-images-${TAK_VERSION}.tar.gz"
```

On a new host, load them before `just setup`, which then reuses them and
builds nothing:

```bash
docker load < tak-images-<version>.tar.gz
```

The images hold no secrets and no data: certificates, `CoreConfig.xml` and the
databases live outside them. They are specific to the CPU architecture they
were built on (x86-64 or ARM).

## First-time setup

1. Start the stack normally (`just up`).
2. Visit the **Backups** page in the monitor dashboard (you must be a
   member of the `monitor_admin` LDAP group — the bootstrap auto-creates
   this group and adds `webadmin` to it on first boot, so the default
   admin account works out of the box).
3. Click **Run backup now**. Wait for it to complete — depending on data
   size this may take anywhere from a few seconds to several minutes.
4. Click **Download backup key**. **This is critical.** The age private
   key is what makes your backups recoverable. Save it somewhere off this
   host — a password manager, a thumb drive, a separate machine.
5. Click **Download** on the new backup tarball. Save it off-host too.

After your first key download, the warning banner disappears.

You can re-download the key any time. There is no rotation — the same
identity encrypts every backup.

The key is also kept on the host, in the backup directory, next to the archives
it decrypts. Encryption therefore protects copies of the archives that leave the
host, not the host itself: anyone who can read that directory, or a disk
snapshot of it, has both.

## Routine operations

**Manually:** `just backup run` from the host runs a backup via the monitor
container.

**Scheduled:** add the operator's preferred cron entry on the host:

```cron
0 3 * * *   cd /path/to/FastTAK && just backup run >> /var/log/fasttak-backup.log 2>&1
```

**From the dashboard:** the **Run backup now** button on the **Backups**
page kicks off a backup in the background. The status indicator polls
every 5 seconds while it's running.

**Retention** keeps the newest 14 backups by default. Override via
`BACKUP_RETENTION_KEEP` in `.env`. Stale `.partial` files older than 6
hours are also reaped — the default covers a multi-GB `cot` dump
without prematurely clobbering an in-flight backup. Operators with
larger DBs (or stricter cleanup) can override via
`BACKUP_PARTIAL_REAP_AGE_SECONDS` in `.env`.

**Manual prune** is available via `just backup prune` (uses the
`BACKUP_RETENTION_KEEP` default) or `just backup prune --keep 5` (override to
keep the newest 5). Useful for one-off cleanup before a release; routine
ops should not need it.

**List backups** with `just backup list` (lists what's on disk with sizes
and ages). Age is taken from the timestamp in the filename, not the file's
mtime, so a restored or copied archive keeps its real age and the listing
agrees with retention about which backup is newest.

## Restoring to a fresh host

> Before you start, you need: the `.age-identity` file (your key), at
> least one backup tarball, and a host with Docker and Compose ready,
> with FastTAK already cloned.

The canonical procedure is also implemented as a shell script that the
integration test invokes — see `tests-integration/restore.sh`. The steps
below mirror that script.

1. **Extract the TAK Server release**

   Extract the matching TAK Server release so `tak/` and a fresh `.env`
   exist before any container starts:

   ```bash
   just setup <takserver-docker-X.X.zip>
   ```

2. **Verify and decrypt the backup**

   **2a. Verify the archive (optional but recommended).** Every successful
   backup writes a `<archive>.sha256` sidecar in `sha256sum -c` format. If
   you copied it off-host alongside the archive, verify it before
   decrypting:

   ```bash
   sha256sum -c fasttak-backup-...age.sha256
   ```

   **2b. Decrypt.**

   ```bash
   age -d -i fasttak-backup-key.txt fasttak-backup-...age | tar xz -C /tmp/restore
   ```

   This produces `/tmp/restore/MANIFEST.json`, `/tmp/restore/postgres/`,
   `/tmp/restore/tak-certs.tar`, `/tmp/restore/tak-config.tar`,
   `/tmp/restore/nodered-data.tar`, and `/tmp/restore/env`.

3. **Inspect the manifest**

   ```bash
   cat /tmp/restore/MANIFEST.json
   ```

   Confirm `postgres_versions` is compatible with what your fresh stack
   will run. A major-version downgrade (e.g. restoring a `cot` dump from
   PG16 onto a PG15 stack) will fail.

4. **Replace `.env`, keeping this host's `TAK_VERSION`**

   ```bash
   tests-integration/restore-env.sh /tmp/restore/env .env
   ```

   The archive's `.env` has to win for the secrets — its database passwords
   match the role hashes in the dumps. `TAK_VERSION` is the exception: it
   names the images `setup.sh` just built on *this* host, not the data, so
   the helper copies the archive's file and then puts the host's version
   back. A plain `cp` would roll the host back to whatever release the
   archive was taken on, and the stack would refuse to start.

   An archive from a release below the supported floor is refused here,
   before anything else is touched. Restore those on the FastTAK release
   that produced them — the tag is `fasttak_version` in the manifest.

5. **Restore TAK certificates and config**

   ```bash
   mkdir -p tak/certs
   tar -x -C tak/certs -f /tmp/restore/tak-certs.tar
   # CoreConfig.xml and any other operator-customized tak/ files
   tar -x -C tak -f /tmp/restore/tak-config.tar
   ```

6. **Restore the Node-RED volume**

   The compose-project labels matter — without them, the next
   `docker compose down -v` won't recognize this volume as part of the
   project and will leak it across runs. `<project>` is your compose
   project name (typically `fasttak`); confirm with `docker volume ls`
   if you've overridden it.

   ```bash
   PROJECT=fasttak
   docker volume create \
       --label "com.docker.compose.project=${PROJECT}" \
       --label "com.docker.compose.volume=nodered-data" \
       "${PROJECT}_nodered-data"
   docker run --rm -i -v "${PROJECT}_nodered-data:/data" alpine \
       sh -c 'cd /data && tar x' < /tmp/restore/nodered-data.tar
   ```

7. **Start the database services only**

   ```bash
   docker compose up -d tak-database app-db
   ```

   Wait for both to be healthy:

   ```bash
   docker compose ps tak-database app-db
   ```

8. **Restore the databases**

   > **Warning — this drops databases.** The commands below run
   > `DROP DATABASE … WITH (FORCE)`, which terminates any live
   > connections (LLDAP, Node-RED, the monitor) and removes the existing
   > database before restoring. **On a fresh restore host this is what
   > you want.** On a recovery against a partially-running stack, stop
   > dependent services first (`docker compose stop lldap nodered monitor`)
   > so you do not interrupt active sessions.

   The dumps are `--format=plain` (no `--clean`), so the target databases
   must exist and be empty before restore. We drop and recreate each one,
   then pipe the dump in. `psql -h localhost` is required because Unix
   socket auth in these images falls through to peer auth and rejects
   `martiuser`/`fastak`. Every `psql` that reads a dump gets
   `-v ON_ERROR_STOP=1` — without it, `psql` reports each statement error to
   stderr and still exits 0, so a restore that failed on every row would
   otherwise look identical to one that succeeded.

   `cot` is created `ENCODING 'SQL_ASCII' TEMPLATE template0`, matching TAK
   Server's own cluster (`monitor/app/backup/manifest.py` documents this —
   `SHOW server_version` comes back as bytes from it). `TEMPLATE template0`
   is required: a database whose encoding differs from `template1`'s cannot
   be cloned from `template1`.

   ```bash
   # cot (TAK Server)
   docker compose exec -T tak-database \
       sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -v ON_ERROR_STOP=1 -h localhost -U martiuser -d postgres \
              -c "DROP DATABASE IF EXISTS cot WITH (FORCE);" \
              -c "CREATE DATABASE cot OWNER martiuser ENCODING '\''SQL_ASCII'\'' TEMPLATE template0;"'
   ```

   **Before restoring the dump, pre-create its extensions as a superuser.**
   Every `cot` dump carries `CREATE EXTENSION IF NOT EXISTS postgis`, but
   PostGIS is not a trusted extension and `martiuser` (who runs the restore
   below) is not a superuser — restoring straight through fails with
   `permission denied to create extension "postgis"`. Restoring wholesale as
   `postgres` instead isn't a fix either: the dumps are taken with
   `pg_dump --no-owner`, specifically so the restoring role ends up owning
   what it restores, and a `postgres`-owned PostGIS would leave TAK Server
   without write access to its own spatial tables. See
   [DD-054](decisions.md#dd-054-restoring-cot-pre-creates-extensions-as-superuser-then-reassigns-ownership)
   for the full reasoning, including why ownership has to be handed to
   `martiuser` explicitly and not just the extension created.

   The extension set varies by TAK release (this dump also carries
   `pgcrypto`; `postgis_topology`/`fuzzystrmatch` appear on others), so
   discover it from the dump rather than hardcoding `postgis`:

   ```bash
   COT_EXTENSIONS=$(awk '
     /^[[:space:]]*COPY[[:space:]].*[[:space:]]FROM[[:space:]]+stdin;[[:space:]]*$/ { exit }
     /^[[:space:]]*CREATE[[:space:]]+EXTENSION[[:space:]]/ {
       s = $0
       sub(/^[[:space:]]*CREATE[[:space:]]+EXTENSION[[:space:]]+/, "", s)
       sub(/^IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+/, "", s)
       name = ""
       if (match(s, /^"[A-Za-z0-9_-]+"/)) { name = substr(s, RSTART + 1, RLENGTH - 2) }
       else if (match(s, /^[A-Za-z0-9_-]+/)) { name = substr(s, RSTART, RLENGTH) }
       if (name != "" && !(name in seen)) { seen[name] = 1; print name }
     }
   ' /tmp/restore/postgres/cot.sql)
   echo "Extensions in this dump: $COT_EXTENSIONS"
   ```

   Then build the SQL that creates every name in `$COT_EXTENSIONS` and hands
   it to `martiuser`, and run it — over the Unix socket as `postgres` (peer
   auth matches the container's own uid, which is what `docker compose exec`
   runs as; over TCP there is no password FastTAK has for the `postgres`
   role):

   ```bash
   EXT_LIST=$(printf "'%s'," $COT_EXTENSIONS)
   EXT_LIST=${EXT_LIST%,}   # trailing comma off

   docker compose exec -T tak-database psql -q -v ON_ERROR_STOP=1 -U postgres -d cot <<SQL
   DO \$fastak\$
   DECLARE
     target_oid oid;
     ext_name   text;
     ext_oid    oid;
     cfg        oid;
   BEGIN
     SELECT oid INTO target_oid FROM pg_catalog.pg_roles WHERE rolname = 'martiuser';
     FOREACH ext_name IN ARRAY ARRAY[$EXT_LIST] LOOP
       EXECUTE format('CREATE EXTENSION IF NOT EXISTS %I', ext_name);
       SELECT oid INTO ext_oid FROM pg_catalog.pg_extension WHERE extname = ext_name;
       UPDATE pg_catalog.pg_extension SET extowner = target_oid WHERE oid = ext_oid;
       FOR cfg IN SELECT unnest(extconfig) FROM pg_catalog.pg_extension WHERE oid = ext_oid LOOP
         EXECUTE format('ALTER TABLE %s OWNER TO %I', cfg::regclass, 'martiuser');
       END LOOP;
     END LOOP;
   END
   \$fastak\$;
   SQL
   ```

   This is `upgrade_extension_sql` from `tests-integration/restore.sh`
   written out longhand for a shell doing the substitution itself rather than
   the script's `awk`/`printf`. If this step and the script ever disagree, the
   script is canonical — copy its
   `upgrade_dump_extensions`/`upgrade_extension_sql` functions verbatim rather
   than reconciling by hand.

   ```bash
   docker compose exec -T tak-database \
       sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -v ON_ERROR_STOP=1 -h localhost -U martiuser -d cot' \
       < /tmp/restore/postgres/cot.sql

   # app-db (lldap, nodered, fastak)
   for db in lldap nodered fastak; do
       docker compose exec -T app-db \
           sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 -h localhost -U fastak -d postgres \
                  -c \"DROP DATABASE IF EXISTS $db WITH (FORCE);\" \
                  -c \"CREATE DATABASE $db OWNER fastak;\""
       docker compose exec -T app-db \
           sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 -h localhost -U fastak -d $db" \
           < /tmp/restore/postgres/$db.sql
   done
   ```

   `WITH (FORCE)` kicks out live connections (e.g. LLDAP holds an open
   connection to `lldap`). Services reconnect after the `CREATE DATABASE`
   completes. For a quieter operation, stop dependent services
   (`docker compose stop lldap nodered monitor`) before this step and
   start them again after.

   This mirrors `tests-integration/restore.sh` in the repo exactly — treat
   that script as canonical if this doc and the script ever disagree.

9. **Start the rest of the stack**

   ```bash
   just up
   ```

10. **Post-restore checks**

   - Log in to the monitor dashboard with an LLDAP user from before the
     restore.
   - Confirm a known TAK client cert is still listed under Service
     Accounts.
   - Confirm a Node-RED flow you remember is still present at
     `https://nodered.<your-server>`.
   - Check `fastak_events` rows: `docker compose exec app-db psql -U fastak
     -d fastak -c 'SELECT COUNT(*) FROM fastak_events'`. Should match
     roughly what you had before the restore.

## Caveats

- **Backups co-located with data are not really backups.** The default
  `BACKUP_DIR` is on the same disk as the deployment. A single-disk
  failure loses both. Treat the dashboard "Download" button as your
  off-host sync.
- **Cross-database consistency is not guaranteed.** The two Postgres
  instances are dumped sequentially; a few seconds may pass between
  them. A user can be created in LLDAP between dumps and not yet have a
  matching cert. Take backups during low-activity periods.
- **Age key off-host requirement.** If the host dies and the key was only
  on it, the backups are decorative. Save the key somewhere durable and
  separate.
- **Postgres version skew.** A major-version downgrade between backup and
  restore will fail. The manifest records the producing version.
- **`.env` is a credential.** The archive contains every secret your
  stack uses. The age encryption is what keeps that safe at rest. Treat
  the unencrypted form on a restore host with care.
- **Concurrency lock is per-`BACKUP_DIR`, not per-stack.** If two
  FastTAK deployments on the same host share `BACKUP_DIR` (uncommon —
  typically each stack uses its own), the second `just backup run` blocks
  until the first finishes. The default `BACKUP_DIR=./backups` is
  per-checkout, so this only matters if you explicitly point multiple
  stacks at one directory.

## Troubleshooting

- **"Permission denied" on `/api/backup/*`** — your user isn't in the
  backup admin group. The bootstrap creates `monitor_admin` (or whatever
  `BACKUP_ADMIN_GROUP` is set to in `.env`) and adds the `webadmin` user
  to it on first boot. Any additional admins must be added to the same
  group via LLDAP.
- **"a backup is already in progress"** — another `just backup run` or
  dashboard click is mid-run. Wait for it to finish, then retry.
- **`pg_dump: server version mismatch`** — your monitor image's
  `pg_dump` is older than the database server. Rebuild the monitor image
  after updating `postgresql-client` to a matching or newer major in
  `monitor/Dockerfile`.
- **`/host/.env` missing during a backup** — the compose bind mount of
  `.env` isn't in place. Check `docker-compose.yml` for
  `./.env:/host/.env:ro` on the monitor service.
- **Backup files appear root-owned on the host** — the monitor container
  writes them under its container uid (root by default), so removing
  them from the host directly may require `sudo`. Prefer the dashboard
  **Delete** button or `just backup prune`, which run inside the
  container.
