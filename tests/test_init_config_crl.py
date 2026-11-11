"""init-config points TAK Server at the revocation list (DD-063).

Runs the real section of init-config/start.sh against sample CoreConfig files.
Whether TAK Server then refuses a revoked cert needs a live stack:
tests-integration/test_revocation_enforced.py.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "init-config" / "start.sh").read_text()
SECTION = re.search(r"^# ── 8b\..*?(?=^# ── 9\.)", SCRIPT, re.MULTILINE | re.DOTALL).group(0)
CRL = '<crl _name="TAKServer CA" crlFile="certs/files/ca.crl"/>'
TLS = 'keystoreFile="certs/files/takserver.jks" truststoreFile="certs/files/truststore-root.jks"'


def patch(tmp_path: Path, config: str, *, crl_exists: bool = True) -> tuple[str, str]:
    """Run the section. Returns (patched config, stderr)."""
    files = tmp_path / "certs" / "files"
    files.mkdir(parents=True, exist_ok=True)
    if crl_exists:
        (files / "ca.crl").write_text("crl")
    path = tmp_path / "CoreConfig.xml"
    path.write_text(config)
    result = subprocess.run(
        ["sh", "-eu", "-c", SECTION],
        env={
            "PATH": os.environ["PATH"],
            "CONFIG": str(path),
            "CERT_DIR": str(tmp_path / "certs"),
            "CERT_FILES": str(files),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return path.read_text(), result.stderr


def config(security: str, before: str = "") -> str:
    return (
        f"<Configuration>\n{before}    <security>\n{security}\n    </security>\n</Configuration>\n"
    )


def test_self_closing_tls_gains_the_list(tmp_path):
    patched, _ = patch(tmp_path, config(f"        <tls {TLS}/>"))
    assert f"        <tls {TLS}>{CRL}</tls>\n" in patched


def test_open_tls_gains_the_list_and_keeps_its_children(tmp_path):
    patched, _ = patch(
        tmp_path, config(f"        <tls {TLS}>\n            <other/>\n        </tls>")
    )
    assert f"        <tls {TLS}>{CRL}\n            <other/>\n        </tls>\n" in patched


def test_only_the_tls_inside_security_is_touched(tmp_path):
    """Federation has its own <tls>, and it comes first in some configs."""
    federation = '    <federation-server><tls keystoreFile="fed.jks"/></federation-server>\n'
    patched, _ = patch(tmp_path, config(f"        <tls {TLS}/>", before=federation))
    assert federation in patched
    assert patched.count("crlFile=") == 1


def test_running_twice_changes_nothing(tmp_path):
    once, _ = patch(tmp_path, config(f"        <tls {TLS}/>"))
    twice, _ = patch(tmp_path, once)
    assert twice == once


@pytest.mark.parametrize(
    ("security", "crl_exists", "reason"),
    [
        ('        <certificateSigning CA="TAKServer"/>', True, "no <tls> element"),
        (f"        <tls {TLS}/>", False, "no ca.crl"),
    ],
)
def test_it_says_so_when_revocation_cannot_be_wired(tmp_path, security, crl_exists, reason):
    """TAK Server refuses to start on a missing file, so the config is left alone."""
    original = config(security)
    patched, stderr = patch(tmp_path, original, crl_exists=crl_exists)
    assert patched == original
    assert reason in stderr
    assert "NOT enforced" in stderr


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
def test_the_list_is_written_before_tak_server_needs_it(tmp_path):
    """A fresh install has no ca.crl, and TAK Server must not be pointed at a
    file that is not there."""
    certs = tmp_path / "certs"
    files = certs / "files"
    files.mkdir(parents=True)
    (certs / "config.cfg").write_text(
        "[ ca ]\ndefault_ca = CA_default\n[ CA_default ]\n"
        "database = crl_index.txt\ndefault_md = sha256\ndefault_crl_days = 30\n"
    )

    def run(*args):
        subprocess.run(args, cwd=files, check=True, capture_output=True)

    run("openssl", "genrsa", "-aes256", "-passout", "pass:atakatak",
        "-out", "ca-do-not-share.key", "2048")  # fmt: skip
    run("openssl", "req", "-x509", "-new", "-key", "ca-do-not-share.key",
        "-passin", "pass:atakatak", "-subj", "/CN=ca", "-days", "30",
        "-out", "ca.pem")  # fmt: skip

    patched, stderr = patch(tmp_path, config(f"        <tls {TLS}/>"), crl_exists=False)

    assert CRL in patched, stderr
    listed = subprocess.run(
        ["openssl", "crl", "-in", str(files / "ca.crl"), "-noout", "-nextupdate"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    years_ahead = int(listed.strip().split()[-2]) - time.gmtime().tm_year
    assert years_ahead >= 9, f"the list should outlive any gap between restarts: {listed}"
