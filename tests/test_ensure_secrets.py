# tests/test_ensure_secrets.py
"""Required-secret provisioning tests.

Covers scripts/ensure-secrets.sh:
- a key absent from .env is appended (the upgrade case — a secret added by a
  later release has no line to fill)
- a key present but empty is filled, in every form env_get treats as empty
- a key already set is left alone
- writes are literal, atomic, and preserve file mode
- failures are reported as failures

The write helpers are exercised directly by sourcing the script's functions, so
values that the fixed openssl generators can never produce (metacharacters,
newlines) can still be tested.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENSURE = REPO / "scripts" / "ensure-secrets.sh"

# Keys the script is responsible for, and the hex width each should end up with.
REQUIRED = {
    "TAK_DB_PASSWORD": 32,
    "APP_DB_PASSWORD": 32,
    "LDAP_BIND_PASSWORD": 32,
    "TOKENS_API_SECRET": 64,
    "MEDIAMTX_VIEWER_PASSWORD": 32,
    "MEDIAMTX_PUBLISHER_PASSWORD": 32,
    "MEDIAMTX_API_PASSWORD": 32,
}

# A populated .env with every required secret already set, so tests can vary one
# key at a time without tripping the others.
BASE = "\n".join(
    [
        "SERVER_ADDRESS=tak.example.org",
        "TAK_DB_PASSWORD=aaaa",
        "APP_DB_PASSWORD=bbbb",
        "LDAP_BIND_PASSWORD=cccc",
        "TOKENS_API_SECRET=dddd",
        "MEDIAMTX_VIEWER_PASSWORD=eeee",
        "MEDIAMTX_PUBLISHER_PASSWORD=ffff",
        "MEDIAMTX_API_PASSWORD=abab",
    ]
)


def _run(env_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(ENSURE), str(env_file)],
        capture_output=True,
        text=True,
    )


def _write(tmp_path: Path, content: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(content)
    return env_file


def _value_of(env_file: Path, key: str) -> str:
    """Last-wins read, mirroring Compose dotenv semantics."""
    found = ""
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        if stripped.startswith(f"{key}="):
            found = stripped[len(key) + 1 :]
    return found


@pytest.fixture(scope="module")
def helpers_src(tmp_path_factory) -> Path:
    """A sourceable copy of the script containing only its helper definitions.

    Sourcing the script whole would run its body and exit. Kept outside the repo
    so an interrupted run cannot leave a stray *.sh behind for shellcheck to
    trip over.
    """
    src = ENSURE.read_text()
    path = tmp_path_factory.mktemp("ensure") / "funcs.sh"
    path.write_text(src[: src.index("generated=0")])
    return path


def _call_helper(helpers_src: Path, fn: str, env_file: Path, key: str, value: str):
    """Invoke one of the script's write helpers directly.

    Sourcing rather than executing gives the tests a seam for values the fixed
    `openssl rand -hex` generators cannot produce — metacharacters and newlines.
    """
    script = f'. "{helpers_src}"; {fn} "$1" "$2" "$3"'
    return subprocess.run(
        ["/bin/bash", "-c", script, "bash", str(env_file), key, value],
        capture_output=True,
        text=True,
    )


# ── The upgrade case: key absent entirely ──────────────────────────────────


def test_absent_key_is_appended(tmp_path):
    """The scenario this exists for: TOKENS_API_SECRET has no line at all."""
    env_file = _write(tmp_path, BASE.replace("TOKENS_API_SECRET=dddd", "") + "\n")
    assert "TOKENS_API_SECRET" not in env_file.read_text()

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert len(_value_of(env_file, "TOKENS_API_SECRET")) == 64
    assert "TOKENS_API_SECRET" in result.stdout


def test_append_to_file_without_trailing_newline(tmp_path):
    """Without the newline guard the key lands on the end of the last line."""
    env_file = _write(tmp_path, BASE.replace("TOKENS_API_SECRET=dddd", "").rstrip())

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert len(_value_of(env_file, "TOKENS_API_SECRET")) == 64
    for line in env_file.read_text().splitlines():
        assert line.count("=") <= 1 or line.startswith("#"), f"joined line: {line!r}"


def test_commented_key_is_treated_as_absent(tmp_path):
    """`#KEY=` is not an assignment; it should be added, not uncommented."""
    env_file = _write(
        tmp_path, BASE.replace("TOKENS_API_SECRET=dddd", "#TOKENS_API_SECRET=") + "\n"
    )

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert len(_value_of(env_file, "TOKENS_API_SECRET")) == 64
    assert "#TOKENS_API_SECRET=" in env_file.read_text()


# ── The empty-value cases ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "form",
    [
        "TOKENS_API_SECRET=",
        "export TOKENS_API_SECRET=",
        'TOKENS_API_SECRET=""',
        "TOKENS_API_SECRET=   ",
    ],
    ids=["bare", "export", "empty-quotes", "trailing-space"],
)
def test_empty_forms_are_filled(tmp_path, form):
    """setup.sh's old sed matched only the bare form; the other three no-opped."""
    env_file = _write(tmp_path, BASE.replace("TOKENS_API_SECRET=dddd", form) + "\n")

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert len(_value_of(env_file, "TOKENS_API_SECRET")) == 64


def test_all_required_secrets_generated_from_blank(tmp_path):
    env_file = _write(tmp_path, "SERVER_ADDRESS=tak.example.org\n")

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    for key, width in REQUIRED.items():
        assert len(_value_of(env_file, key)) == width, key


# ── Idempotence and non-interference ───────────────────────────────────────


def test_existing_values_are_untouched(tmp_path):
    env_file = _write(tmp_path, BASE + "\n")
    before = env_file.read_text()

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert env_file.read_text() == before
    assert result.stdout.strip() == ""


def test_second_run_is_a_no_op(tmp_path):
    env_file = _write(tmp_path, "SERVER_ADDRESS=tak.example.org\n")
    assert _run(env_file).returncode == 0
    after_first = env_file.read_text()

    second = _run(env_file)

    assert second.returncode == 0
    assert env_file.read_text() == after_first
    assert second.stdout.strip() == ""


def test_webadmin_password_is_never_generated(tmp_path):
    """Empty means 'skip webadmin creation' — filling it undoes that choice."""
    env_file = _write(tmp_path, BASE + "\nTAK_WEBADMIN_PASSWORD=\n")

    _run(env_file)

    assert _value_of(env_file, "TAK_WEBADMIN_PASSWORD") == ""


def test_no_secret_value_is_printed(tmp_path):
    env_file = _write(tmp_path, "SERVER_ADDRESS=tak.example.org\n")

    result = _run(env_file)

    for key in REQUIRED:
        value = _value_of(env_file, key)
        assert value not in result.stdout
        assert value not in result.stderr


# ── Write mechanism ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "a&b",
        r"a\1b",
        r"back\slash",
        "pipe|value",
        'quote"value',
        "single'quote",
        "with spaces",
        "amp&and\\back|pipe",
    ],
)
def test_values_are_written_literally(tmp_path, helpers_src, value):
    """`&` and `\\1` are why this uses concatenation, not sub()/gsub()."""
    env_file = _write(tmp_path, "TOKENS_API_SECRET=\n")

    result = _call_helper(helpers_src, "env_set", env_file, "TOKENS_API_SECRET", value)

    assert result.returncode == 0, result.stderr
    assert _value_of(env_file, "TOKENS_API_SECRET") == value


def test_newline_in_value_is_rejected_and_file_untouched(tmp_path, helpers_src):
    """A newline would split the record — `a\\nSERVER_ADDRESS=evil` injects a key."""
    env_file = _write(tmp_path, "TOKENS_API_SECRET=\n")
    before = env_file.read_text()

    result = _call_helper(
        helpers_src, "env_set", env_file, "TOKENS_API_SECRET", "evil\nSERVER_ADDRESS=attacker"
    )

    assert result.returncode != 0
    assert env_file.read_text() == before
    assert "newline" in result.stderr.lower()


def test_newline_rejected_on_append_too(tmp_path, helpers_src):
    env_file = _write(tmp_path, "SERVER_ADDRESS=tak.example.org\n")
    before = env_file.read_text()

    result = _call_helper(helpers_src, "env_append", env_file, "NEW_KEY", "evil\nINJECTED=yes")

    assert result.returncode != 0
    assert env_file.read_text() == before


def test_only_the_last_duplicate_is_rewritten(tmp_path, helpers_src):
    """Compose is last-wins, so rewriting an earlier line would leave it empty."""
    env_file = _write(tmp_path, "TOKENS_API_SECRET=operator-value\nTOKENS_API_SECRET=\n")

    result = _call_helper(helpers_src, "env_set", env_file, "TOKENS_API_SECRET", "generated")

    assert result.returncode == 0, result.stderr
    lines = env_file.read_text().splitlines()
    assert lines[0] == "TOKENS_API_SECRET=operator-value"
    assert lines[1] == "TOKENS_API_SECRET=generated"


def test_export_prefix_is_preserved(tmp_path, helpers_src):
    env_file = _write(tmp_path, "export TOKENS_API_SECRET=\n")

    result = _call_helper(helpers_src, "env_set", env_file, "TOKENS_API_SECRET", "value")

    assert result.returncode == 0, result.stderr
    assert env_file.read_text().splitlines()[0] == "export TOKENS_API_SECRET=value"


def test_other_lines_are_not_disturbed(tmp_path, helpers_src):
    content = (
        "# a comment\n"
        "SERVER_ADDRESS=tak.example.org\n"
        "TOKENS_API_SECRET=\n"
        "\n"
        "# trailing comment\n"
        "OTHER=untouched\n"
    )
    env_file = _write(tmp_path, content)

    result = _call_helper(helpers_src, "env_set", env_file, "TOKENS_API_SECRET", "value")

    assert result.returncode == 0, result.stderr
    got = env_file.read_text()
    assert got == content.replace("TOKENS_API_SECRET=\n", "TOKENS_API_SECRET=value\n")


def test_file_mode_is_preserved(tmp_path):
    """.env is a secrets file; the rewrite must not widen it to the umask."""
    env_file = _write(tmp_path, "TOKENS_API_SECRET=\n")
    os.chmod(env_file, 0o600)

    result = _run(env_file)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


# ── Failure reporting ──────────────────────────────────────────────────────


def test_script_is_executable():
    """setup.sh and start.sh invoke it directly, not via `bash <path>`.

    The rest of these tests run it through /bin/bash, which works without the
    executable bit — so without this check a non-executable script passes the
    whole suite and fails on every real install.
    """
    assert os.access(ENSURE, os.X_OK), f"{ENSURE} is not executable"


def test_missing_env_file_fails(tmp_path):
    result = _run(tmp_path / "nope.env")

    assert result.returncode == 1
    assert "not found" in result.stderr.lower()


def test_no_argument_fails(tmp_path):
    result = subprocess.run(["/bin/bash", str(ENSURE)], capture_output=True, text=True)

    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_unwritable_directory_reports_failure(tmp_path):
    """A failed write must not report success — the bug class this script fixes."""
    env_file = _write(tmp_path, "TOKENS_API_SECRET=\n")
    os.chmod(tmp_path, 0o500)
    try:
        result = _run(env_file)
    finally:
        os.chmod(tmp_path, 0o700)

    assert result.returncode != 0
    assert "TOKENS_API_SECRET" in result.stderr
