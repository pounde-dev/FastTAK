"""Static checks of mediamtx/mediamtx.yml and how docker-compose.yml wires it.

MediaMTX has no shell in its image and no healthcheck, so a wrong key here shows
up only as a stack that behaves differently. These pin the settings the
security model depends on (DD-056).
"""

import re
from pathlib import Path

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
