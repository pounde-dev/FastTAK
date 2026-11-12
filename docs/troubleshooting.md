# Troubleshooting

## Start with `just doctor`

```bash
just doctor             # failures only, and what to run next
just doctor --verbose   # every check
```

It runs the same checks `just up` runs after a start, against whatever is
running, without building, starting, or changing anything. Each failure line
says what was checked, what came back, and the next command to run. Before
the summary it lists the ports this host publishes, compared with what your
`DEPLOY_MODE` should bind: an unexpected port fails (look for a stray
`docker-compose.override.yml`), a missing one is noted (shown with
`--verbose`). What the internet can actually reach is that set intersected
with your cloud firewall, which the host cannot see. Exit status is 1 when
anything failed, so it works in cron and in a shell `&&`.

## No TAK client can connect, and every container used to report healthy

**Symptoms**

- ATAK/iTAK/WinTAK clients cannot connect, or connect on port 8089 and then
  receive **no CoT at all**.
- TAK Server's REST API returns HTTP 500 on every request.
- Client-side logs show `BadCredentialsException: Exception performing TAK
  Server authentication`, wrapping
  `IgniteClientDisconnectedException: Client node disconnected`.
- Before this release, `docker compose ps` showed everything **healthy**
  throughout; the container now goes unhealthy within a few intervals (see
  below). Certificates are valid. TLS handshakes succeed.

`tak-server`'s healthcheck probes the API and goes unhealthy on any 5xx, and
it scans the recent log for Ignite disconnects, so the container reports
**unhealthy** within a few intervals and the Monitor alerts. The first failing
check also writes the log tails to `tak/logs/incident/<time>-<pid>-<check>.log`
on the host, once per incident, so the evidence survives rotation; the
directory keeps the newest five snapshots. `just doctor` names the newest
file.

**What is actually wrong**

TAK Server backs its authentication and group lookups with an Apache Ignite
cache shared between its five JVM processes. One process's Ignite client node
has detached from the cluster while the JVM itself stayed alive. Every auth
attempt then throws, surfacing as a 500.

The misleading part is `BadCredentialsException` — nobody's credentials are
wrong. The failure is in the shared auth path, upstream of any individual
client's certificate.

The silent-stream symptom has the same cause: TAK registers each client
subscription in that same Ignite cache
(`IgniteCacheHolder.cacheRemoteSubscription`). The socket connects, the
subscription never registers, so nothing routes.

**Confirm it**

```bash
docker exec fasttak-tak-server-1 sh -c \
  'grep -iE "IgniteClientDisconnected|ClusterTopologyException|Failed to connect to node" \
   /opt/tak/logs/takserver-*.log | tail -20'
```

**Fix**

Restart the stack. The Ignite cluster re-forms on startup.

```bash
docker compose restart tak-server
```

It does not recover on its own. An Ignite client reconnects with a **new** node
ID, so the messaging process can hold references to the old one indefinitely,
retrying against a dead peer.

**Why nothing warned you**

The container healthcheck verifies that processes are running, ports accept
connections, certificates are valid, and no `OutOfMemoryError` appears in the
log. All of those pass during this failure — the API is running, it is just
answering 500 to everything. Improving that detection is tracked in the issue
tracker, along with tuning Ignite's failure-detection timeouts, which default to
600 seconds even though every node here is on loopback.

**Collect this before restarting** if you can spare a minute — the error storm
rotates the useful lines away quickly:

```bash
docker exec fasttak-tak-server-1 sh -c \
  'grep "Topology snapshot" /opt/tak/logs/takserver-messaging.log | tail -30'
docker exec fasttak-tak-server-1 sh -c 'ls -la /opt/tak/logs/'
```

## ATAK reports "Unable to validate Truststore" but still connects

The dialog reads `TAK Server connectivity issue: Unable to validate
Truststore. Open network settings now?` on every launch, yet the server dot is
green and the user sees other users' positions.

ATAK checks the truststore two ways. The native comms layer reads the
`truststore.p12` with OpenSSL and connects. A separate Java-side check loads
the same file as a Java keystore, which only counts certificate entries that
carry a `friendlyName`. A truststore without one loads as empty there, and
the dialog fires.

Two things produce that state:

- **Data packages built by v0.30.3 or earlier.** Their `truststore.p12` had no
  friendly name. Regenerate the package from the dashboard; the current
  builder writes a Java-layout truststore. Check a package with keytool —
  a good one shows one `trustedCertEntry`:

  ```bash
  unzip -p package.zip certs/truststore.p12 > truststore.p12
  keytool -list -storetype PKCS12 -keystore truststore.p12 -storepass atakatak
  ```

- **Importing through the general Import Manager.** That path applies
  `config.pref` and points ATAK's global default truststore at the file, which
  is what the Java-side check validates. Import through Network Preferences →
  TAK Servers → New Connection → Data Package instead; that route stores the
  certs against the server entry and skips the global preference. See
  [Importing a data package into ATAK](certificates.md#importing-a-data-package-into-atak).

Either way, remove the existing server entry in ATAK before re-importing so
the stale truststore is not left behind.

## `just setup` fails while building a TAK Server image

**Symptoms**

`just setup` (or `just test-stack up`, which runs it) stops with
`ERROR: build of takserver...:<version> failed`, and the log above it shows a
`404` for a repository, or a package that cannot be installed.

**What is actually wrong**

The Dockerfiles come from the TAK Server bundle. They start from a fixed Red
Hat image and then update every package, which can move the image to a newer
minor release than the Rocky and PostgreSQL repositories have published yet.
The build then asks those repositories for a release that does not exist.
Nothing in FastTAK or in your bundle changed; the same bundle built before and
will build again when the repositories catch up.

**Fix**

- A running deployment needs no fix. `just up` does not build these images.
- If Docker on this machine still has the images, `just setup` reuses them.
  Check with `docker images | grep takserver`.
- If another machine has them, or you kept an export, load it. See
  [Keep the TAK Server images](backup-and-restore.md#keep-the-tak-server-images).
- Otherwise retry later, or look for a newer bundle on tak.gov.

## Every Monitor page returns 403

The Monitor is admin-only as of v0.28.1. The account you are logged in with is
not a member of `ADMIN_GROUP` (default `monitor_admin`).

See [Upgrading](upgrading.md#recovering-from-a-monitor-lockout) for recovery,
and [Authentication](authentication.md#authorization-who-may-use-the-monitor)
for what the gate covers.

## A container is running that is not in the compose file

`docker compose up` does not remove containers whose service was deleted from
the compose file. Compare what is running against what is defined:

```bash
docker ps --format '{{.Names}}'
docker compose config --services
```

Remove orphans with `docker compose up -d --remove-orphans`. `start.sh`,
`reconfig.sh`, and `just up` now pass this automatically.

## The database keeps growing

TAK Server keeps all CoT forever by default — `cot_router` grows without bound
and nothing prunes it. Autovacuum tuning does **not** delete rows; it only
reclaims space from rows something else deleted, so a stack with zero deletions
will show zero dead tuples and reclaim nothing.

To enable pruning, set `COT_RETENTION_DAYS` (and optionally
`GEOCHAT_RETENTION_DAYS`) in `.env` and restart so `init-config` templates the
retention policy. See [Database Management](database-management.md).

!!! warning "First run after a long period"
    Enabling retention on a table with millions of accumulated rows deletes them
    all in one pass at the scheduled time. That creates a large dead-tuple load,
    and the space will not return to the OS without a `VACUUM FULL`, which takes
    an exclusive lock. Consider a shorter first window, or pruning in batches.

High-volume automated feeds are the usual reason for unexpected growth. An
ADS-B or weather flow publishing every few seconds inserts far more rows than a
handful of human users ever will, and those CoT payloads are large and highly
repetitive — they compress well on disk, so the network traffic to Postgres can
be far larger than the resulting table. Throttling or trimming what the flow
publishes reduces both.
