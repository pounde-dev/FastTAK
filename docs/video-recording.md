# Video Recording

FastTAK can record every video stream that flows through MediaMTX — NodeRed feeds proxied through MediaMTX [[flows-library]] and anything else published to MediaMTX via RTSP, RTMP, or the API.

Recording is **off by default**. Enable it with two lines in `.env`.

MediaMTX requires credentials: publish as `publisher:<password>@`, play as `viewer:<password>@` (see [MediaMTX access](configuration.md#mediamtx-access)). Publishing from outside the stack is off until `MEDIAMTX_PUBLISH_IPS` is set.

## Quick Start

```bash
# .env
MEDIAMTX_RECORD=true
MEDIAMTX_RECORD_RETENTION=168h        # keep 7 days
```

Then recreate `mediamtx`:

```bash
docker compose up -d --force-recreate mediamtx
```

Files start appearing under `./recordings/<stream-path>/`.

## Where files land

```
recordings/
├── live/
│   └── uas1/                      ← a source that publishes to live/uas1
│       ├── 2026-05-22_14-30-00-000000.mp4
│       └── 2026-05-22_14-40-00-000000.mp4
└── ds/
    └── <sensor-id>/               ← a DroneSense stream
        └── ...
```

One directory per stream path. Filenames are UTC timestamps with a microsecond suffix to keep them unique across container restarts.

The host directory is `./recordings` by default — set `MEDIAMTX_RECORDINGS_DIR` to override:

```bash
MEDIAMTX_RECORDINGS_DIR=/mnt/storage/fastak-recordings
```

The path resolves relative to the `docker-compose.yml` directory.

## Disk usage

A typical 1080p stream runs **1–2 GB per hour**.

Recording stays running when the disk fills — MediaMTX will log write errors but won't stop. Size the disk before turning recording on, or set `MEDIAMTX_RECORDINGS_DIR` to a dedicated volume.

## Retention

Retention is time-based. MediaMTX sweeps periodically (~5 min interval) and deletes whole segment files older than `MEDIAMTX_RECORD_RETENTION`.

Format is a Go duration string:

| Value  | Meaning          |
| ------ | ---------------- |
| `24h`  | 1 day            |
| `168h` | 7 days (default) |
| `720h` | 30 days          |

**Constraint:** `MEDIAMTX_RECORD_RETENTION` must be `>= 10m` (the segment duration). MediaMTX refuses to start otherwise — you'll see a crashloop with `ERR: 'recordDeleteAfter' cannot be lower than 'recordSegmentDuration'`. The smallest practical retention is `15m`.

## Segments

Files rotate every **10 minutes**. The cap bounds individual file size and limits loss if a stream drops mid-segment. To change it, edit `recordSegmentDuration` in `mediamtx/mediamtx.yml` directly — there's no `.env` knob for it.

A 10-minute 1080p segment is ~100–200 MB. A 30-second stream produces a single short file (the recorder closes the segment when the publisher disconnects).

## Watching while recording

The live RTSP feed is unaffected by recording — clients connect to `rtsp://viewer:<password>@<server>:8554/<path>` exactly as they would without it. Recording is a parallel write off the same demuxed packets.

To replay an in-progress segment from disk, open the most recent `.mp4` in VLC:

```bash
open -a VLC "$(ls -t recordings/<stream>/*.mp4 | head -1)"
```

MediaMTX flushes a playable "part" to disk every 1 second, so partial files are playable while the recorder is still writing.

For programmatic access, MediaMTX also has a [playback HTTP server](https://github.com/bluenviron/mediamtx#recording) on port 9996 that serves recorded segments and HLS. It's not exposed by Caddy in FastTAK by default — see [`mediamtx/mediamtx.yml`](https://github.com/pounde/FastTAK/blob/main/mediamtx/mediamtx.yml) and add a route if you want to use it.

## Format

Default is **fragmented MP4** (`fmp4`):

- Browser-playable via HLS
- Smaller than MPEG-TS
- Each `recordPartDuration` (1s) flush is a self-contained, playable fragment

For streams that drop connection often and you don't want any chance of an unfinalized MP4, switch to `mpegts` by editing `recordFormat` in `mediamtx/mediamtx.yml`. MPEG-TS files are tolerant of arbitrary byte-cuts and concatenate with `cat *.ts > combined.ts`.

## Disabling

Either set `MEDIAMTX_RECORD=false` in `.env` (or remove the line entirely — the default is off), then recreate mediamtx:

```bash
docker compose up -d --force-recreate mediamtx
```

Existing recordings stay on disk and continue to age out per `MEDIAMTX_RECORD_RETENTION` until that's also disabled — to keep them indefinitely, delete the env var or move the files outside `MEDIAMTX_RECORDINGS_DIR`.

## File ownership

The `bluenviron/mediamtx` image is scratch-based and runs as root, so recording files land on the host owned by root. This matches the existing pattern for other Docker-managed directories (e.g., the `app-db-data` volume).

If this is a problem (e.g., for an unprivileged operator to clean up old files), `sudo chown -R $USER recordings/` after stopping mediamtx, or use a dedicated volume with permissive ownership.

## Scope and limits

- **All streams or no streams.** Recording is configured via MediaMTX `pathDefaults`, so it applies to every published path. There's no per-path opt-in or opt-out at the `.env` level. For finer control, edit `paths:` in `mediamtx/mediamtx.yml` and override `record: no` on specific paths.
- **Time-based retention only.** No count-based cap (e.g., "keep newest 100 files"). MediaMTX doesn't support it.
- **No backup integration.** Recordings are operational data, not part of the FastTAK backup tarball — they're time-bounded, expendable, and large (wrong shape for encrypted backups).
- **No dashboard UI.** Recordings are filesystem artifacts. Browse the disk directly.
