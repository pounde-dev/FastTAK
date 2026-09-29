"""MediaMTX recording integration tests.

Verifies the opt-in recording feature end-to-end against the test stack:
  - Default-off: published streams produce no recording files
  - Recording-on: published streams produce ffprobe-valid mp4 files

Requires ffmpeg + ffprobe on PATH (used to publish a test pattern and
verify the resulting file). Skipped if unavailable.

The recording-on test toggles ``MEDIAMTX_RECORD`` in the test stack's
``.env`` and force-recreates the mediamtx container, restoring both on
teardown. On Linux, mediamtx writes recording files as root inside the
bind mount — cleanup uses an ephemeral alpine container so it works
regardless of host UID mapping.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Test-stack RTSP port (from docker-compose.test.yml mediamtx ports override)
RTSP_PORT = 28554

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (FFMPEG and FFPROBE),
        reason="ffmpeg/ffprobe not on PATH",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _publisher_password(stack_info) -> str:
    return subprocess.run(
        [
            f"{stack_info.repo_dir}/scripts/env-get.sh",
            stack_info.env_file,
            "MEDIAMTX_PUBLISHER_PASSWORD",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _publish_test_stream(
    stack_info, stream_name: str, duration: int = 3
) -> subprocess.CompletedProcess:
    """Publish a libavfilter test pattern to the test-stack RTSP port as `publisher`."""
    password = _publisher_password(stack_info)
    return subprocess.run(
        [
            FFMPEG,
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-f",
            "rtsp",
            f"rtsp://publisher:{password}@localhost:{RTSP_PORT}/{stream_name}",
        ],
        capture_output=True,
        timeout=duration + 10,
    )


def _wait_for_recording(directory: Path, deadline_seconds: int = 10) -> list[Path]:
    """Poll for .mp4 files to appear under ``directory``."""
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        if directory.exists():
            files = sorted(directory.glob("*.mp4"))
            if files:
                return files
        time.sleep(0.5)
    return sorted(directory.glob("*.mp4")) if directory.exists() else []


def _purge_recording_dir(stream_dir: Path) -> None:
    """Remove ``stream_dir`` even if files are root-owned (Linux bind mounts).

    Uses a one-shot alpine container with the recordings volume mounted, so
    cleanup works regardless of how the host maps mediamtx's root UID.
    """
    if not stream_dir.exists():
        return
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{stream_dir.parent}:/r",
            "alpine",
            "rm",
            "-rf",
            f"/r/{stream_dir.name}",
        ],
        capture_output=True,
        timeout=30,
    )


def _recreate_mediamtx(compose_cmd: list[str], compose_env: dict[str, str]) -> None:
    """Force-recreate the mediamtx service and give it a moment to come up.

    `compose_env` is required: `up` re-reads docker-compose.test.yml, which
    interpolates HOST_ENV_FILE (see the fixture in conftest).
    """
    subprocess.run(
        [*compose_cmd, "up", "-d", "--force-recreate", "mediamtx"],
        check=True,
        capture_output=True,
        timeout=60,
        env=compose_env,
    )
    # mediamtx scratch image has no healthcheck — wait for it to bind ports
    time.sleep(3)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_off_writes_no_recording(stack_info, run_id):
    """Stock test stack (recording off) — publishing writes no files."""
    stream_name = f"test-rec-off-{run_id}"
    recordings_dir = Path(stack_info.repo_dir) / "recordings" / stream_name
    _purge_recording_dir(recordings_dir)

    publish = _publish_test_stream(stack_info, stream_name)
    assert publish.returncode == 0, (
        f"ffmpeg publish failed: {publish.stderr.decode(errors='replace')[-500:]}"
    )

    files = _wait_for_recording(recordings_dir, deadline_seconds=3)
    assert not files, f"Expected no recordings, found {[f.name for f in files]}"


def test_recording_on_writes_valid_mp4(stack_info, compose_cmd, compose_env, run_id):
    """With MEDIAMTX_RECORD=true, publishing produces an ffprobe-valid mp4."""
    env_path = Path(stack_info.env_file)
    original_env = env_path.read_text()

    stream_name = f"test-rec-on-{run_id}"
    recordings_dir = Path(stack_info.repo_dir) / "recordings" / stream_name
    _purge_recording_dir(recordings_dir)

    # Flip the commented default to MEDIAMTX_RECORD=true; if the comment isn't
    # there for any reason, append the override.
    if "# MEDIAMTX_RECORD=false" in original_env:
        modified = original_env.replace("# MEDIAMTX_RECORD=false", "MEDIAMTX_RECORD=true")
    else:
        modified = original_env.rstrip() + "\nMEDIAMTX_RECORD=true\n"
    env_path.write_text(modified)

    try:
        _recreate_mediamtx(compose_cmd, compose_env)

        publish = _publish_test_stream(stack_info, stream_name)
        assert publish.returncode == 0, (
            f"ffmpeg publish failed: {publish.stderr.decode(errors='replace')[-500:]}"
        )

        files = _wait_for_recording(recordings_dir, deadline_seconds=10)
        assert files, f"No .mp4 files appeared in {recordings_dir}"

        ffprobe = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration",
                "-of",
                "default=noprint_wrappers=1",
                str(files[0]),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert ffprobe.returncode == 0, f"ffprobe failed: {ffprobe.stderr}"
        assert "mp4" in ffprobe.stdout.lower(), f"Recording is not a valid mp4: {ffprobe.stdout}"
    finally:
        env_path.write_text(original_env)
        _recreate_mediamtx(compose_cmd, compose_env)
        _purge_recording_dir(recordings_dir)
