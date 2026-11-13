"""Static checks of mediamtx/mediamtx.yml and how docker-compose.yml wires it.

MediaMTX has no shell in its image and no healthcheck, so a wrong key here shows
up only as a stack that behaves differently. These pin the settings the
security model depends on (DD-056).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((REPO / "mediamtx" / "mediamtx.yml").read_text())
COMPOSE = yaml.safe_load((REPO / "docker-compose.yml").read_text())


def test_unused_protocols_are_off():
    # 1.21 enables MoQ by default; SRT is unpublished and unused until phase 2.
    assert CONFIG["moq"] is False
    assert CONFIG["srt"] is False


def _pin(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.M)
    assert match, pattern
    return match.group(1)


def test_readme_documents_the_shipped_pin():
    env_pin = _pin((REPO / ".env.example").read_text(), r"^MEDIAMTX_VERSION=(\S+)$")
    readme_pin = _pin((REPO / "README.md").read_text(), r"^\| `MEDIAMTX_VERSION` \| `([^`]+)` \|")
    assert env_pin == "1.21.1"
    assert readme_pin == env_pin


USERS = CONFIG["authInternalUsers"]
MTX_ENV = COMPOSE["services"]["mediamtx"]["environment"]
PASSWORD_KEYS = (
    "MEDIAMTX_VIEWER_PASSWORD",
    "MEDIAMTX_PUBLISHER_PASSWORD",
    "MEDIAMTX_API_PASSWORD",
)


def test_no_anonymous_entry():
    assert all(u["user"] != "any" for u in USERS)


def test_user_order_is_the_index_contract():
    # docker-compose.yml addresses these by position.
    assert [u["user"] for u in USERS] == ["viewer", "publisher", "api"]


def test_file_entries_admit_nobody_on_their_own():
    for u in USERS:
        assert u["ips"] == ["127.0.0.1/32"], u["user"]
        assert u["pass"] == "overridden-by-compose", u["user"]


def test_permissions():
    actions = {u["user"]: sorted(p["action"] for p in u["permissions"]) for u in USERS}
    assert actions == {
        "viewer": ["playback", "read"],
        "publisher": ["publish"],
        "api": ["api"],
    }


def test_rtsp_uses_digest_only():
    assert CONFIG["rtspAuthMethods"] == ["digest"]


def test_compose_overrides_pass_and_ips_for_every_user():
    for i in range(len(USERS)):
        assert f"MTX_AUTHINTERNALUSERS_{i}_PASS" in MTX_ENV, i
        assert f"MTX_AUTHINTERNALUSERS_{i}_IPS" in MTX_ENV, i


@pytest.mark.parametrize("index,key", list(enumerate(PASSWORD_KEYS)))
def test_passwords_are_required_not_defaulted(index, key):
    value = MTX_ENV[f"MTX_AUTHINTERNALUSERS_{index}_PASS"]
    assert value.startswith(f"${{{key}:?"), value


def test_publishing_is_off_by_default():
    for i in (0, 2):
        assert MTX_ENV[f"MTX_AUTHINTERNALUSERS_{i}_IPS"] == "0.0.0.0/0,::/0"
    assert MTX_ENV["MTX_AUTHINTERNALUSERS_1_IPS"] == "${MEDIAMTX_PUBLISH_IPS:-127.0.0.1/32}"


# Secrets that other services require. Compose stops at the first one that is
# empty, so these need a value before a MediaMTX password can be the reason.
OTHER_REQUIRED_SECRETS = {
    "MONITOR_PROXY_SECRET": "a1" * 16,
}


def _compose_config(tmp_path, overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env_file = tmp_path / ".env"
    lines = [(REPO / ".env.example").read_text()]
    lines += [f"{k}={v}" for k, v in (OTHER_REQUIRED_SECRETS | overrides).items()]
    env_file.write_text("\n".join(lines) + "\n")
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "--env-file",
            str(env_file),
            "config",
            "--quiet",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
@pytest.mark.parametrize("missing", PASSWORD_KEYS)
def test_compose_refuses_to_start_without_a_password(tmp_path, missing):
    values = {k: "a1" * 16 for k in PASSWORD_KEYS}
    values[missing] = ""
    result = _compose_config(tmp_path, values)
    assert result.returncode != 0
    assert missing in result.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_compose_accepts_all_passwords(tmp_path):
    result = _compose_config(tmp_path, {k: "a1" * 16 for k in PASSWORD_KEYS})
    assert result.returncode == 0, result.stderr
