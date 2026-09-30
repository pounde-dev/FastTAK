"""video-cot.sh against stub binaries: which URL ffmpeg publishes to, and what
the CoT it sends carries. The real openssl/ffmpeg/python3 are replaced on PATH,
so nothing is streamed or sent.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

STUBS = {
    # Records its arguments, then outlives the script's 3 s liveness check
    # before exiting, so the script's final `wait` on it returns.
    "ffmpeg": '#!/bin/sh\necho "$@" > "$STUB_LOG/ffmpeg.args"\nexec sleep 5\n',
    # The script sends CoT with `python3 -c <program>`; keep the program.
    "python3": '#!/bin/sh\nprintf "%s" "$2" > "$STUB_LOG/python3.program"\n',
    "openssl": "#!/bin/sh\nexit 0\n",
    "pkill": "#!/bin/sh\nexit 0\n",
}


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "fasttak"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "video-cot.sh", root / "video-cot.sh")
    for name in ("env-get.sh", "lib-env.sh"):
        shutil.copy(REPO / "scripts" / name, root / "scripts" / name)
    certs = root / "tak" / "certs" / "files"
    certs.mkdir(parents=True)
    (certs / "svc_fasttakapi.p12").write_text("")
    (certs / "ca.pem").write_text("")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    logs = tmp_path / "logs"
    logs.mkdir()
    (tmp_path / "tmp").mkdir()
    return root, bin_dir, logs


def _run(sandbox, env_lines: str, *args):
    root, bin_dir, logs = sandbox
    (root / ".env").write_text("SERVER_ADDRESS=tak.example.org\n" + env_lines)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "STUB_LOG": str(logs),
        "HOME": str(root),
        # mktemp -d lands here, so leaked cert/key material is observable.
        "TMPDIR": str(logs.parent / "tmp"),
    }
    result = subprocess.run(
        ["/bin/sh", str(root / "video-cot.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, logs


CREDS = "MEDIAMTX_VIEWER_PASSWORD=viewpw1\nMEDIAMTX_PUBLISHER_PASSWORD=pubpw1\n"


def test_rtsp_ingest_uses_the_publisher_credential(sandbox):
    result, logs = _run(sandbox, CREDS)
    assert result.returncode == 0, result.stderr
    assert (
        "rtsp://publisher:pubpw1@localhost:8554/live/test1" in (logs / "ffmpeg.args").read_text()
    )


def test_rtmp_ingest_uses_query_credentials(sandbox):
    result, logs = _run(sandbox, CREDS, "--protocol", "rtmp")
    assert result.returncode == 0, result.stderr
    assert (
        "rtmp://localhost:1935/live/test1?user=publisher&pass=pubpw1"
        in (logs / "ffmpeg.args").read_text()
    )


def test_cot_carries_the_viewer_credential(sandbox):
    # COT_XML's \" escapes are shell quoting; python3 receives plain quotes.
    result, logs = _run(sandbox, CREDS)
    assert result.returncode == 0, result.stderr
    assert 'address="viewer:viewpw1@tak.example.org"' in (logs / "python3.program").read_text()


def test_passwords_are_not_printed(sandbox):
    result, _ = _run(sandbox, CREDS)
    assert "viewpw1" not in result.stdout
    assert "pubpw1" not in result.stdout
    assert "viewpw1" not in result.stderr
    assert "pubpw1" not in result.stderr


def test_temp_dir_is_removed_after_a_normal_run(sandbox):
    result, logs = _run(sandbox, CREDS)
    assert result.returncode == 0, result.stderr
    assert list((logs.parent / "tmp").iterdir()) == []


def test_temp_dir_is_removed_when_ffmpeg_exits_early(sandbox):
    _, bin_dir, logs = sandbox
    (bin_dir / "ffmpeg").write_text("#!/bin/sh\nexit 1\n")
    result, _ = _run(sandbox, CREDS)
    assert result.returncode != 0
    assert "MEDIAMTX_PUBLISH_IPS" in result.stderr
    assert list((logs.parent / "tmp").iterdir()) == []


def test_missing_viewer_password_fails(sandbox):
    result, _ = _run(sandbox, "MEDIAMTX_PUBLISHER_PASSWORD=pubpw1\n", "--no-stream")
    assert result.returncode == 1
    assert "MEDIAMTX_VIEWER_PASSWORD" in result.stderr


def test_missing_publisher_password_fails_only_when_streaming(sandbox):
    result, _ = _run(sandbox, "MEDIAMTX_VIEWER_PASSWORD=viewpw1\n")
    assert result.returncode == 1
    assert "MEDIAMTX_PUBLISHER_PASSWORD" in result.stderr
    result, _ = _run(sandbox, "MEDIAMTX_VIEWER_PASSWORD=viewpw1\n", "--no-stream")
    assert result.returncode == 0, result.stderr
