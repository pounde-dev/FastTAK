"""setup.sh describes an upgrade honestly instead of prescribing a sequence.

FastTAK has no path that carries the databases across a TAK Server version
change. The 5.6 → 5.8 move was done by hand, once, as a fresh start; the
migration tooling built for it was deleted in 869093c. So the closing
instruction cannot be a procedure — it says what changed, tells the operator to
back up, and states that nothing carries the databases across. See #109.

Three things decide which closing instruction an operator gets: whether this is
an existing deployment (tak/ or .env was there), whether TAK_VERSION actually
changed, and whether the deployment is this checkout's own or one set up
elsewhere with -d. A re-run with the same bundle changes nothing and must not
imply otherwise; a -d deployment must not be told to run just recipes that act
on this checkout instead (#104).

These tests run setup.sh end to end with a stub `docker` on PATH — no daemon,
no images, no network.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "setup.sh"
VERSION = "5.8-RELEASE-65"


def _fake_bundle(tmp_path: Path) -> Path:
    """A minimal hardened bundle that passes every check setup.sh makes.

    Built once per tmp_path: a test taking both fixtures runs setup.sh twice.
    """
    zip_path = tmp_path / f"takserver-docker-hardened-{VERSION}.zip"
    if zip_path.exists():
        return zip_path

    root = tmp_path / "src" / f"takserver-docker-hardened-{VERSION}"
    (root / "tak").mkdir(parents=True)
    (root / "docker").mkdir(parents=True)
    (root / "tak" / "version.txt").write_text(f"{VERSION}\n")
    (root / "docker" / "Dockerfile.hardened-takserver").write_text("FROM scratch\n")
    (root / "docker" / "Dockerfile.hardened-takserver-db").write_text("FROM scratch\n")

    subprocess.run(
        ["zip", "-q", "-r", str(zip_path), f"takserver-docker-hardened-{VERSION}"],
        cwd=tmp_path / "src",
        check=True,
    )
    return zip_path


def _stub_docker(tmp_path: Path) -> Path:
    """`docker build` succeeds and prints a plausible last line."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "Successfully tagged stub"\nexit 0\n')
    docker.chmod(0o755)
    return bin_dir


def _mini_repo(tmp_path: Path) -> Path:
    """A copy of what setup.sh needs from its own directory, so it can run
    with no -d — TARGET_DIR == SCRIPT_DIR — without touching the real repo."""
    repo = tmp_path / "repo"
    if repo.exists():
        return repo
    repo.mkdir()
    shutil.copy(SETUP, repo / "setup.sh")
    shutil.copy(REPO / ".env.example", repo / ".env.example")
    shutil.copytree(REPO / "scripts", repo / "scripts")
    shutil.copytree(REPO / "tak-server", repo / "tak-server")
    return repo


def _run_setup(tmp_path: Path, target: Path | None) -> subprocess.CompletedProcess:
    """target=None runs setup.sh from a mini repo with no -d (the operator's
    case); a path runs the real setup.sh with -d target (the harness's case)."""
    bin_dir = _stub_docker(tmp_path)
    if target is None:
        cmd = ["/bin/bash", str(_mini_repo(tmp_path) / "setup.sh"), str(_fake_bundle(tmp_path))]
    else:
        cmd = ["/bin/bash", str(SETUP), "-d", str(target), str(_fake_bundle(tmp_path))]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        timeout=120,
    )


def _existing(target: Path, version: str) -> None:
    (target / "tak").mkdir(parents=True, exist_ok=True)
    (target / "tak" / "version.txt").write_text(f"{version}\n")
    (target / ".env").write_text(_env_at_version(version))


def _env_at_version(version: str) -> str:
    """.env.example with TAK_VERSION set to `version`.

    Replacing the whole line, not the key: .env.example ships a value, so
    substituting the key alone leaves the old one appended to the new.
    """
    return re.sub(
        r"^TAK_VERSION=.*$",
        f"TAK_VERSION={version}",
        (REPO / ".env.example").read_text(),
        flags=re.M,
    )


@pytest.fixture
def fresh(tmp_path):
    """Fresh install of this checkout's own deployment (no -d)."""
    result = _run_setup(tmp_path, None)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def upgrade(tmp_path):
    """This checkout's own deployment, already on an older release."""
    _existing(_mini_repo(tmp_path), "5.8-RELEASE-64")
    result = _run_setup(tmp_path, None)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def unchanged(tmp_path):
    """This checkout's own deployment, already on the bundle's version."""
    _existing(_mini_repo(tmp_path), VERSION)
    result = _run_setup(tmp_path, None)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def fresh_elsewhere(tmp_path):
    """Fresh install into another directory with -d."""
    result = _run_setup(tmp_path, tmp_path / "elsewhere")
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def upgrade_elsewhere(tmp_path):
    """An older deployment in another directory, upgraded with -d."""
    target = tmp_path / "elsewhere-existing"
    _existing(target, "5.8-RELEASE-64")
    result = _run_setup(tmp_path, target)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def upgrade_without_tak_dir(tmp_path):
    """An existing deployment whose tak/ is gone: .env is all that is left.

    A forced clean re-extract removes tak/, and a tak/ on a mount that did not
    land is simply missing. The .env, the volumes and the containers are still
    there in both cases, so this is an upgrade — and calling it a fresh install
    hands a live deployment a bare start onto stale volumes.
    """
    target = tmp_path / "no-tak-dir"
    target.mkdir(parents=True)
    (target / ".env").write_text(_env_at_version("5.8-RELEASE-64"))
    result = _run_setup(tmp_path, target)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_env_without_tak_dir_is_still_an_upgrade(upgrade_without_tak_dir):
    """.env alone means a live deployment: volumes and containers are still
    there, so this cannot get the bare fresh-install instruction."""
    assert "TAK Server 5.8-RELEASE-64 → 5.8-RELEASE-65" in upgrade_without_tak_dir


def test_fresh_install_says_just_up(fresh):
    assert "just up" in fresh
    assert "./start.sh" not in fresh
    assert "back up" not in fresh.lower()


def test_upgrade_never_advises_destroying_the_volumes(upgrade):
    """The old guidance ended in `docker compose down -v`, which described the
    by-hand 5.6 → 5.8 move as though it were supported. It never was."""
    assert "down -v" not in upgrade


def test_upgrade_reports_what_changed(upgrade):
    assert "TAK Server 5.8-RELEASE-64 → 5.8-RELEASE-65" in upgrade


def test_upgrade_says_to_back_up_first(upgrade):
    """The backup is the only thing standing between the operator and a server
    that refuses the old volumes."""
    flat = " ".join(upgrade.split()).lower()
    assert "back up before you start" in flat
    assert upgrade.index("just backup run") < upgrade.index("just up")


def test_upgrade_names_recipes_that_exist(upgrade):
    """v0.30.0 printed `just backup && just backups`: the second recipe was
    removed and the first errors without a subcommand."""
    assert "just backups" not in upgrade
    assert "just backup &&" not in upgrade
    assert "./start.sh" not in upgrade


def test_a_deployment_elsewhere_is_not_told_to_run_this_checkouts_recipes(
    fresh_elsewhere, upgrade_elsewhere, tmp_path
):
    """The just recipes act on this checkout's own deployment. Printing them
    for a -d target points the operator at the wrong stack (#104)."""
    for out in (fresh_elsewhere, upgrade_elsewhere):
        assert "just up" not in out
        assert "just backup" not in out
        assert "./start.sh" not in out
        assert str(tmp_path) in out, "the closing block names the directory it set up"
    assert "TAK Server 5.8-RELEASE-64 → 5.8-RELEASE-65" in upgrade_elsewhere
    assert "back up" in upgrade_elsewhere.lower()


def test_upgrade_says_no_path_carries_the_databases(upgrade):
    """An operator who reads a version bump as routine needs to know that the
    databases are on their own. Normalised: setup.sh wraps these lines."""
    flat = " ".join(upgrade.split()).lower()
    assert "no supported path for carrying them across" in flat
    assert "no automated migration" in flat


def test_an_unchanged_bundle_is_not_treated_as_an_upgrade(unchanged):
    """Re-running setup.sh with the same bundle changes nothing. Implying
    otherwise is what made the old guidance fire on every existing deployment."""
    assert "back up before you start" not in unchanged.lower()
    assert "just up" in unchanged


def test_upgrade_points_at_the_full_procedure(upgrade):
    assert "docs/upgrading.md" in upgrade


def test_the_two_paths_do_not_give_the_same_instruction(fresh, upgrade):
    assert fresh != upgrade


def _env_value(env_file: Path, key: str) -> str:
    return next(
        line.partition("=")[2]
        for line in env_file.read_text().splitlines()
        if line.startswith(f"{key}=")
    )


def test_fresh_install_gets_its_own_lldap_key_seed(tmp_path):
    result = _run_setup(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(
        r"[0-9a-f]{64}", _env_value(_mini_repo(tmp_path) / ".env", "LLDAP_KEY_SEED")
    )


def test_upgrade_leaves_lldap_key_seed_empty(tmp_path):
    """A seed on an existing directory would invalidate every password (DD-060)."""
    repo = _mini_repo(tmp_path)
    _existing(repo, "5.8-RELEASE-64")
    result = _run_setup(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert _env_value(repo / ".env", "LLDAP_KEY_SEED") == ""
    assert re.fullmatch(r"[0-9a-f]{64}", _env_value(repo / ".env", "LLDAP_JWT_SECRET"))
