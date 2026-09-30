# Upgrading FastTAK

There are two kinds of upgrade, and only one of them is supported today.

**A FastTAK upgrade** — same TAK Server release, new FastTAK code — is:

```bash
git pull
just up
```

No `just down` first. `just up` rebuilds the images FastTAK builds itself and
runs `docker compose up`, which recreates only the containers whose image or
configuration changed and leaves the rest running. The databases are untouched.

**A TAK Server upgrade** — a new release ZIP from tak.gov — has no supported
path yet. `just setup <new-zip>` will extract the new release, rebuild the
images and preserve your `.env`, certificates and `CoreConfig.xml`, but nothing
carries the *databases* across a TAK Server version change: no migration, no
schema handling, no verification that the new server accepts the old volumes.

!!! warning "Before running `just setup` against a new TAK Server release"
    Take a backup first (`just backup run && just backup list`) and keep it somewhere
    off the host. If the new server refuses the existing volumes, your options
    are to restore that backup onto the old release or to start the databases
    fresh — and starting fresh loses the CoT history, the LLDAP accounts, the
    Node-RED flows and the audit history. Your certificates, `CoreConfig.xml`
    and `UserAuthenticationFile.xml` live on the host under `tak/` and are
    preserved either way.

Building that path — new ZIP, `git pull`, one command that updates every piece —
is tracked in [#109](https://github.com/pounde/FastTAK/issues/109).

The rest of this page covers the parts that are **not** automatic even for a
FastTAK upgrade: the ones that depend on decisions you made, which no script can
make for you.

## Before you upgrade

### 1. Check who can reach the Monitor (v0.28.1 and later)

As of v0.28.1 the Monitor is admin-only, end to end. Every JSON API and every
dashboard page requires membership in `ADMIN_GROUP` (default `monitor_admin`);
only `/api/ping` is open. See [Authentication](authentication.md#authorization-who-may-use-the-monitor).

`webadmin` is added to that group automatically by the identity bootstrap, so if
that is the account you log in with, you keep access.

**Any other admin account must be added to the group before you upgrade**, or it
will get a 403 on every page afterwards. Add users to `monitor_admin` from the
Monitor's Users page, or directly in LLDAP.

!!! warning "Renaming `ADMIN_GROUP`"
    If you set `ADMIN_GROUP` to a custom name, make sure the stack has been
    restarted at least once on v0.28.2 or later. Earlier versions bootstrapped
    only `BACKUP_ADMIN_GROUP`, so a renamed `ADMIN_GROUP` was never created and
    nobody was ever added to it — locking every account out of the Monitor with
    no way back in through the UI. Recovery is to add the group and membership
    in LLDAP directly, or to unset `ADMIN_GROUP` and restart.

### 2. Know what a version bump changes

Check the release notes for the versions you are skipping. FastTAK follows
semantic versioning, and `fix:` releases will not change behaviour you depend on
— but a service being *removed* from the stack is worth knowing about in
advance, because of the next item.

### 3. Set `MEDIAMTX_VERSION=1.21.1` (this release and later)

Image pins live in your `.env`, so a new release does not change them for you.
This release needs MediaMTX 1.21 or later; `./start.sh` refuses an older pin
and says so. Edit `.env`:

    MEDIAMTX_VERSION=1.21.1

MoQ and SRT are now explicitly off. Neither port was published, so nothing
that worked before stops working.

### 4. MediaMTX requires credentials (this release and later)

- `./start.sh` adds `MEDIAMTX_VIEWER_PASSWORD`, `MEDIAMTX_PUBLISHER_PASSWORD`
  and `MEDIAMTX_API_PASSWORD` to your `.env`.
- Re-import the DroneSense flow (see its page, "Upgrading from an earlier
  release"). Until you do, its video returns 401.
- Anything that publishes into MediaMTX from outside the stack now needs
  `MEDIAMTX_PUBLISH_IPS` and the publisher password. Browsers opening HLS
  are asked for the viewer password.
- If you hid the MediaMTX ports in an override because they had no
  authentication, you can publish 8554 again for viewing; publishing stays off.

## What the scripts now handle for you

These used to be manual steps and are no longer:

| Rule | Where it is enforced |
| ---- | -------------------- |
| Remove containers for services deleted from the compose file | `--remove-orphans` in `start.sh`, `reconfig.sh`, and `just up` |
| Generate any newly required secrets on a fresh install | `setup.sh` |
| Create and populate the Monitor's gate groups | `init-identity` on every stack start |
| Refuse to start on a missing or unsafe `.env` value | `scripts/check-env.sh`, called from `start.sh` |

### Why `--remove-orphans` matters

`docker compose up` does **not** remove a container whose service has been
deleted from the compose file. It keeps running on its old configuration,
invisible to `docker compose ps`, surviving every subsequent upgrade.

This is not hypothetical. `tak-portal` was removed from the stack in
[DD-043](decisions.md) because it had become unauthenticated after Authentik was
replaced — and on at least one deployment the container kept running for months
afterwards, because nothing removed it.

If you upgraded before this was fixed, check once:

```bash
docker ps --format '{{.Names}}'
```

Anything not in `docker compose config --services` is an orphan. Remove it:

```bash
docker compose up -d --remove-orphans
```

## After you upgrade

Confirm the stack is healthy and that you can still get in:

```bash
just doctor                  # every check just up runs, the Monitor included, plus the published-ports report
```

Then load the Monitor dashboard in a browser and confirm the pages render. A 403
on every page means the account you are logged in with is not in `ADMIN_GROUP` —
see step 1 above.

## Recovering from a Monitor lockout

If every page 403s and you have no admin account left in the group, you can fix
it from LLDAP without the Monitor. LLDAP's own web UI is not exposed by default,
so work through the container:

1. Confirm which group the Monitor is gating on — the value of `ADMIN_GROUP` in
   `.env`, or `monitor_admin` if unset.
2. Add your account to that group in LLDAP.
3. Restart the monitor so nothing is cached: `docker compose restart monitor`.

The group name is read per request, so renaming it in `.env` takes effect on the
next request after a monitor restart — no rebuild needed.
