"""setup.sh builds each TAK Server image once and reuses it afterwards.

The bundle's Dockerfiles install the newest packages from several repositories
at build time, so a build that worked last week can fail today with nothing
changed on this side. An image that already exists for this TAK Server version
must therefore not be rebuilt unless the operator asks for it.

These tests run setup.sh end to end with a stub `docker` that records its
calls: no daemon, no images, no network.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_setup_upgrade_guidance import SETUP, VERSION, _fake_bundle

IMAGES = [f"takserver-database:{VERSION}", f"takserver:{VERSION}"]


def _run(
    tmp_path: Path,
    *flags: str,
    have_images: bool,
    build_fails: bool = False,
    flags_first: bool = False,
):
    """Run setup.sh -d <target> against a stub docker; return (result, calls)."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'if [ "$1" = image ] && [ "$2" = inspect ]; then\n'
        f"  exit {0 if have_images else 1}\n"
        "fi\n"
        'if [ "$1" = build ]; then\n'
        f'  echo "stub build output"; exit {1 if build_fails else 0}\n'
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(0o755)
    target = ["-d", str(tmp_path / "target")]
    args = [*flags, *target] if flags_first else [*target, *flags]
    result = subprocess.run(
        ["/bin/bash", str(SETUP), *args, str(_fake_bundle(tmp_path))],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        timeout=120,
    )
    lines = calls.read_text().splitlines() if calls.exists() else []
    return result, lines


def _builds(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("build ")]


def test_existing_images_are_reused(tmp_path):
    result, calls = _run(tmp_path, have_images=True)
    assert result.returncode == 0, result.stderr
    assert _builds(calls) == []
    for image in IMAGES:
        assert f"Using existing {image}" in result.stdout


def test_missing_images_are_built(tmp_path):
    result, calls = _run(tmp_path, have_images=False)
    assert result.returncode == 0, result.stderr
    built = _builds(calls)
    assert len(built) == 2
    for image, call in zip(IMAGES, built, strict=True):
        assert f"-t {image} " in call


@pytest.mark.parametrize("flags_first", [False, True])
def test_rebuild_builds_even_when_images_exist(tmp_path, flags_first):
    """--rebuild works on either side of -d, since getopts never sees it."""
    result, calls = _run(tmp_path, "--rebuild", have_images=True, flags_first=flags_first)
    assert result.returncode == 0, result.stderr
    assert len(_builds(calls)) == 2
    assert "Using existing" not in result.stdout


def test_failed_build_says_the_cause_is_upstream_and_how_to_proceed(tmp_path):
    result, calls = _run(tmp_path, have_images=False, build_fails=True)
    assert result.returncode == 1
    assert len(_builds(calls)) == 1  # stops at the first failure
    assert "stub build output" in result.stderr  # the log tail is still shown
    assert "docker load" in result.stderr
    assert "`just up` does not build this image" in result.stderr
    assert not (tmp_path / "target" / ".env").exists()
