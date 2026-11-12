#!/bin/bash
# setup.sh — Set up FastTAK from a tak.gov Docker release ZIP.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# TAK_VERSION_FLOOR comes from the library too — see its header.
# shellcheck source=scripts/lib-tak-version.sh
. "$SCRIPT_DIR/scripts/lib-tak-version.sh"
# The shared .env reader, so this script agrees with check-env.sh and start.sh
# about what a key's value is — quoted values included.
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/scripts/lib-env.sh"
usage() {
  cat <<'EOF'
Usage: ./setup.sh [-d <dir>] [--rebuild] <takserver-docker-hardened-X.X-RELEASE-N.zip>

Extract the TAK Server release, build the images, and create or update .env.

  -d <dir>      target directory for tak/ and .env (default: this directory)
  --rebuild     build the TAK images even when Docker already has them for
                this TAK Server version (the default is to reuse them)
  -h, --help    show this help
EOF
}

TARGET_DIR="$SCRIPT_DIR"
REBUILD=false
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
# getopts has no long options, so take --rebuild out of the arguments first.
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=true ;;
    *) ARGS+=("$arg") ;;
  esac
done
set -- "${ARGS[@]}"
while getopts ":d:" opt; do
  case "$opt" in
    d) TARGET_DIR="$OPTARG" ;;
    *) usage >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
if [ $# -ne 1 ]; then
  usage >&2
  exit 2
fi
ZIP="$1"

if [ ! -f "$ZIP" ]; then
  echo "ERROR: File not found: $ZIP" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          FastTAK Setup                   ║"
echo "╚══════════════════════════════════════════╝"

# ── Extract release ──────────────────────────────────────────────────────────
echo ""
echo "▸ Extracting release..."
unzip -q "$ZIP" -d "$WORK_DIR"

RELEASE_DIR=$(find "$WORK_DIR" -maxdepth 1 -type d -name 'takserver-docker-*' | head -1)
if [ -z "$RELEASE_DIR" ]; then
  echo "  ERROR: Could not find takserver-docker-* directory in ZIP" >&2
  exit 1
fi

VERSION=$(tr -d '[:space:]' < "$RELEASE_DIR/tak/version.txt" 2>/dev/null)
if [ -z "$VERSION" ]; then
  echo "  ERROR: Could not read version from tak/version.txt" >&2
  exit 1
fi
echo "  TAK Server version: $VERSION"

# A version this parser cannot read is not the same failure as a version below
# the floor, and the two need different fixes: one is a malformed version.txt,
# the other is a genuinely old bundle. Reporting an unparseable string as
# "below 5.8" sends the operator hunting for a newer release they already have.
# check-env.sh draws the same distinction for TAK_VERSION in .env.
if ! tak_version_major_minor "$VERSION" >/dev/null; then
  cat >&2 <<EOF

  ERROR: TAK Server version "$VERSION" could not be parsed.

  Expected the form X.Y or X.Y-anything, e.g. 5.8-RELEASE-65 — the string
  the release bundle writes to tak/version.txt.

  This says nothing about how old the bundle is. Check
  $RELEASE_DIR/tak/version.txt, and re-download the bundle if it looks
  truncated or edited.
EOF
  exit 1
fi

# FastTAK supports the hardened bundle from TAK Server 5.8 onward only. A 5.6
# bundle would build cleanly but put PGDATA at /var/lib/postgresql/15/data
# rather than the volume mount, silently un-persisting the database — so this
# refuses rather than tolerates. See DD-051.
if ! tak_version_meets_floor "$VERSION" "$TAK_VERSION_FLOOR"; then
  cat >&2 <<EOF

  ERROR: TAK Server $VERSION is below the supported floor of $TAK_VERSION_FLOOR.

  FastTAK requires the hardened Docker bundle from TAK Server $TAK_VERSION_FLOOR
  or later (takserver-docker-hardened-X.Y-RELEASE-N.zip).

  Earlier releases place the PostgreSQL data directory outside the volume
  FastTAK mounts, so the database would not survive a container recreate.

  Download a current bundle from https://tak.gov/products/tak-server
EOF
  exit 1
fi

DOCKERFILE_DB="$RELEASE_DIR/docker/Dockerfile.hardened-takserver-db"
DOCKERFILE_SERVER="$RELEASE_DIR/docker/Dockerfile.hardened-takserver"
for f in "$DOCKERFILE_DB" "$DOCKERFILE_SERVER"; do
  if [ ! -f "$f" ]; then
    cat >&2 <<EOF

  ERROR: $(basename "$f") not found in the release bundle.

  FastTAK builds the hardened images. This looks like a standard (non-hardened)
  bundle — download takserver-docker-hardened-X.Y-RELEASE-N.zip instead.
EOF
    exit 1
  fi
done

# ── Build Docker images ─────────────────────────────────────────────────────
# Build one image, or abort with the tail of the build log. A bare
# `docker build ... | tail -1` under `set -e` cannot fail the script: the
# pipeline's status is tail's, so a failed build would print "Setup Complete"
# with no images ever created.
build_image() {
  local tag="$1" dockerfile="$2" log status
  log="$(mktemp)"
  status=0
  docker build -t "$tag" -f "$dockerfile" "$RELEASE_DIR" > "$log" 2>&1 || status=$?
  if [ "$status" -ne 0 ]; then
    echo "" >&2
    echo "  ERROR: build of $tag failed. Last 40 lines:" >&2
    tail -40 "$log" >&2
    rm -f "$log"
    cat >&2 <<EOF

  The Dockerfile comes from the TAK Server bundle, and it installs the newest
  packages from several repositories at build time. A failure with a 404 or
  a package conflict above usually means those repositories are out of step
  with each other, not that FastTAK or your bundle is broken.

  What you can do:
    - Retry later, or look for a newer bundle on tak.gov.
    - If another machine has $tag, copy it over:
        docker save $tag | gzip > image.tar.gz     (there)
        docker load < image.tar.gz                 (here)
      then run this command again: an image that exists is reused.
  A running deployment is not affected: \`just up\` does not build this image.
EOF
    exit 1
  fi
  tail -1 "$log"
  rm -f "$log"
}

# Build an image only when Docker does not have it. The tag carries the full
# TAK Server version, so an existing image came from this same bundle version.
# Rebuilding it gains nothing and can fail: the bundle's Dockerfiles install
# the newest packages from Red Hat, Rocky, EPEL and PGDG, and when those are
# out of step the build breaks for reasons that have nothing to do with the
# bundle (see docs/troubleshooting.md).
ensure_image() {
  local tag="$1" dockerfile="$2"
  if [ "$REBUILD" != true ] && docker image inspect "$tag" >/dev/null 2>&1; then
    echo "  Using existing $tag (--rebuild to build it again)"
    return 0
  fi
  echo "  Building $tag..."
  build_image "$tag" "$dockerfile"
}

echo ""
echo "▸ TAK Server Docker images (a build may take a few minutes)..."
echo "  Note: a build installs packages from the Rocky, EPEL, Adoptium and"
echo "        PGDG repositories, and requires outbound network access."
ensure_image "takserver-database:${VERSION}" "$DOCKERFILE_DB"
ensure_image "takserver:${VERSION}" "$DOCKERFILE_SERVER"

# ── Set up tak/ directory ────────────────────────────────────────────────────
echo ""
# Which closing instruction the operator gets depends on this. See the summary
# below.
#
# Either marker means an existing deployment. tak/ alone is not enough: a forced
# clean re-extract removes it, and a tak/ on a mount that did not land is simply
# missing — in both cases the .env, the volumes and the containers are still
# there, and calling that a fresh install would tell a live deployment to start
# straight onto volumes the new images may not accept. .env is the same marker
# the TAK_VERSION branch below already uses.
IS_UPGRADE=false
if [ -d "$TARGET_DIR/tak" ] || [ -f "$TARGET_DIR/.env" ]; then
  IS_UPGRADE=true
fi

if [ -d "$TARGET_DIR/tak" ]; then
  echo "▸ Upgrading tak/ directory (preserving certs, config, logs)..."

  PRESERVE_DIR=$(mktemp -d)
  for item in certs CoreConfig.xml CoreConfig.example.xml UserAuthenticationFile.xml logs; do
    [ -e "$TARGET_DIR/tak/$item" ] && cp -a "$TARGET_DIR/tak/$item" "$PRESERVE_DIR/"
  done

  rm -rf "$TARGET_DIR/tak"
  cp -a "$RELEASE_DIR/tak" "$TARGET_DIR/tak"

  for item in certs CoreConfig.xml CoreConfig.example.xml UserAuthenticationFile.xml logs; do
    [ -e "$PRESERVE_DIR/$item" ] && cp -a "$PRESERVE_DIR/$item" "$TARGET_DIR/tak/"
  done
  rm -rf "$PRESERVE_DIR"

  echo "  Application files updated. Certs, config, and logs preserved."
else
  if [ "$IS_UPGRADE" = true ]; then
    # .env without tak/: an existing deployment whose tak/ is gone. Extract it
    # fresh — there is nothing left to preserve — but the closing instruction
    # below still has to be the upgrade one.
    echo "▸ Extracting tak/ directory (existing .env — nothing to preserve)..."
  else
    echo "▸ Fresh install — extracting tak/ directory..."
  fi
  cp -a "$RELEASE_DIR/tak" "$TARGET_DIR/tak"

  # The tak.gov release may contain cert files from their build process.
  # Remove them so FastTAK generates a fresh CA on first boot.
  if [ -d "$TARGET_DIR/tak/certs/files" ]; then
    rm -f "$TARGET_DIR/tak/certs/files"/*.pem \
          "$TARGET_DIR/tak/certs/files"/*.key \
          "$TARGET_DIR/tak/certs/files"/*.jks \
          "$TARGET_DIR/tak/certs/files"/*.p12 \
          "$TARGET_DIR/tak/certs/files"/*.csr \
          "$TARGET_DIR/tak/certs/files"/*.cfg \
          "$TARGET_DIR/tak/certs/files"/*.crl \
          "$TARGET_DIR/tak/certs/files"/*.txt \
          "$TARGET_DIR/tak/certs/files"/*.attr 2>/dev/null
  fi
  echo "  Done."
fi

# The release zip stores every file mode 0666 — no execute bit, on any file,
# including the vendor's own *.sh. TAK's Dockerfile.hardened-takserver-db
# compensates at build time (find /opt/tak -name "*.sh" -exec chmod u=rx), but
# FastTAK bind-mounts this host-extracted tak/ over /opt/tak in tak-server,
# shadowing that build-time fix — the container sees the host tree's modes,
# not the image's. The vendor's own README_hardened_docker.md recommends
# `chmod -R u+rwX ./tak` for exactly this reason.
find "$TARGET_DIR/tak" -name "*.sh" -exec chmod u+rx {} +

# Copy scripts into tak/ so file bind mounts overlay correctly on Docker Desktop.
# Without this, Docker creates empty mountpoints that virtiofs can't resolve.
# Must run after both fresh-install and upgrade paths since both replace tak/.
# Uses cat+redirect to avoid cp -i alias prompting in interactive shells.
for script in healthcheck.sh register-api-cert.sh; do
  cat "$SCRIPT_DIR/tak-server/$script" > "$TARGET_DIR/tak/$script"
  chmod +x "$TARGET_DIR/tak/$script"
done

# ── Handle .env ──────────────────────────────────────────────────────────────
if [ ! -f "$TARGET_DIR/.env" ]; then
  echo ""
  echo "▸ Creating .env and generating secrets..."
  cp "$SCRIPT_DIR/.env.example" "$TARGET_DIR/.env"
  sed -i.bak "s/^TAK_VERSION=.*/TAK_VERSION=${VERSION}/" "$TARGET_DIR/.env"
  rm -f "$TARGET_DIR/.env.bak"

  # Fill empty values with generated secrets
  fill_secret() {
    local key="$1" val="$2"
    sed -i.bak "s|^${key}=$|${key}=${val}|" "$TARGET_DIR/.env"
    rm -f "$TARGET_DIR/.env.bak"
  }

  # Fresh install only: empty means "skip webadmin user creation", so this must
  # not be filled on an upgrade. Every other required secret is provisioned
  # below by ensure-secrets.sh, which runs for fresh installs and upgrades
  # alike.
  fill_secret TAK_WEBADMIN_PASSWORD "$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
else
  # Upgrade: update TAK_VERSION if changed
  CURRENT_VERSION=$(env_get "$TARGET_DIR/.env" TAK_VERSION)
  PREVIOUS_VERSION="$CURRENT_VERSION"
  if [ "$CURRENT_VERSION" != "$VERSION" ]; then
    sed -i.bak "s/^TAK_VERSION=.*/TAK_VERSION=${VERSION}/" "$TARGET_DIR/.env"
    rm -f "$TARGET_DIR/.env.bak"
    echo ""
    echo "▸ Updated TAK_VERSION in .env: ${CURRENT_VERSION} → ${VERSION}"
  fi
fi

# Required secrets, for fresh installs and upgrades alike. Outside the branch
# above on purpose: work placed inside the fresh-install arm never runs for an
# existing deployment, which is how a secret added by a later release goes
# missing everywhere it was already installed.
if ! "$SCRIPT_DIR/scripts/ensure-secrets.sh" "$TARGET_DIR/.env"; then
  echo "ERROR: could not provision required secrets in $TARGET_DIR/.env" >&2
  exit 1
fi

# ── Verify ───────────────────────────────────────────────────────────────────
ENV_VERSION=$(env_get "$TARGET_DIR/.env" TAK_VERSION)
if [ "$ENV_VERSION" != "$VERSION" ]; then
  echo ""
  echo "  ⚠ WARNING: .env has TAK_VERSION=${ENV_VERSION} but images are ${VERSION}"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          Setup Complete                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Images:    takserver:${VERSION}  takserver-database:${VERSION}"
echo "  TAK dir:   ${TARGET_DIR}/tak/"
echo "  Config:    ${TARGET_DIR}/.env"
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │ BEFORE YOU START:                                   │"
echo "  │                                                     │"
echo "  │   vim .env ← set SERVER_ADDRESS and DEPLOY_MODE    │"
echo "  │                                                     │"
echo "  │ Admin login (TAK Server):                           │"
echo "  │   User: webadmin                                    │"
echo "  │   Password: in .env as TAK_WEBADMIN_PASSWORD        │"
echo "  │                                                     │"
echo "  │ View:  grep TAK_WEBADMIN_PASSWORD .env              │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
VERSION_CHANGED=false
if [ "$IS_UPGRADE" = true ] && [ "${PREVIOUS_VERSION:-$VERSION}" != "$VERSION" ]; then
  VERSION_CHANGED=true
fi

if $VERSION_CHANGED; then
  # The TAK Server version changed under an existing deployment. FastTAK has no
  # procedure for carrying the databases across that, so say so rather than
  # advising a sequence. See docs/upgrading.md and issue #109.
  echo "  TAK Server ${PREVIOUS_VERSION} → ${VERSION}"
  echo ""
  echo "  Back up before you start — the databases were written by the old"
  echo "  server, and FastTAK has no supported path for carrying them across a"
  echo "  TAK Server version change."
fi

# The just recipes act on this checkout's own deployment. A -d target is a
# different one — the integration harness starts those itself — so naming the
# recipes here would point at the wrong stack (#104).
if [ "$TARGET_DIR" = "$SCRIPT_DIR" ]; then
  if $VERSION_CHANGED; then
    echo "    just backup run"
    echo "    just up"
  else
    echo "  Start FastTAK:"
    echo "    just up"
  fi
else
  echo "  Set up in ${TARGET_DIR}. The just recipes here act on this checkout's"
  echo "  own deployment; a deployment elsewhere is started by whatever created"
  echo "  it (the integration harness does this itself)."
fi

if $VERSION_CHANGED; then
  echo ""
  echo "  If the new server refuses the existing volumes, there is no automated"
  echo "  migration. See docs/upgrading.md before going further."
fi
echo ""
