"""MediaMTX authentication end-to-end (DD-056).

Runs against the test stack, which sets MEDIAMTX_PUBLISH_IPS=0.0.0.0/0 so the
host may publish. Requires ffmpeg + ffprobe on PATH.
"""

import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

RTSP_PORT = 28554
RTMP_PORT = 21935
HLS_PORT = 28888
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not on PATH"),
]


def mediamtx_secret(stack_info, key: str) -> str:
    return subprocess.run(
        [f"{stack_info.repo_dir}/scripts/env-get.sh", stack_info.env_file, key],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _url(path: str, user: str | None = None, password: str | None = None) -> str:
    auth = f"{user}:{password}@" if user else ""
    return f"rtsp://{auth}localhost:{RTSP_PORT}/{path}"


def _publish(url: str, seconds: int = 3) -> subprocess.CompletedProcess:
    transport = (
        ["-f", "flv"] if url.startswith("rtmp:") else ["-f", "rtsp", "-rtsp_transport", "tcp"]
    )
    return subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            *transport,
            url,
        ],
        capture_output=True,
        text=True,
        timeout=seconds + 20,
    )


def _probe(url: str) -> subprocess.CompletedProcess:
    transport = ["-rtsp_transport", "tcp"] if url.startswith("rtsp:") else []
    return subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            *transport,
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.fixture
def live_path(stack_info, run_id):
    """A path with a stream on it, published with the publisher credential."""
    path = f"authtest-{run_id}"
    url = _url(path, "publisher", mediamtx_secret(stack_info, "MEDIAMTX_PUBLISHER_PASSWORD"))
    thread = threading.Thread(target=_publish, args=(url, 20), daemon=True)
    thread.start()
    time.sleep(4)
    yield path
    thread.join(timeout=30)


def test_publisher_can_publish(stack_info, run_id):
    pw = mediamtx_secret(stack_info, "MEDIAMTX_PUBLISHER_PASSWORD")
    result = _publish(_url(f"pub-{run_id}", "publisher", pw))
    assert result.returncode == 0, result.stderr


def test_anonymous_publish_refused(run_id):
    result = _publish(_url(f"anon-{run_id}"))
    assert result.returncode != 0
    assert "401" in result.stderr


def test_viewer_cannot_publish(stack_info, run_id):
    pw = mediamtx_secret(stack_info, "MEDIAMTX_VIEWER_PASSWORD")
    result = _publish(_url(f"viewpub-{run_id}", "viewer", pw))
    assert result.returncode != 0
    assert "401" in result.stderr


def test_viewer_can_read(stack_info, live_path):
    pw = mediamtx_secret(stack_info, "MEDIAMTX_VIEWER_PASSWORD")
    result = _probe(_url(live_path, "viewer", pw))
    assert result.returncode == 0, result.stderr
    assert "h264" in result.stdout


def test_anonymous_read_refused(live_path):
    result = _probe(_url(live_path))
    assert result.returncode != 0
    assert "401" in result.stderr


def test_wrong_viewer_password_refused(live_path):
    result = _probe(_url(live_path, "viewer", "wrong"))
    assert result.returncode != 0
    assert "401" in result.stderr


def _rtmp_url(path: str, user: str | None = None, password: str | None = None) -> str:
    query = f"?user={user}&pass={password}" if user else ""
    return f"rtmp://localhost:{RTMP_PORT}/{path}{query}"


def test_publisher_can_publish_over_rtmp(stack_info, run_id):
    """The control for the refusals below: RTMP ingest works with the credential."""
    pw = mediamtx_secret(stack_info, "MEDIAMTX_PUBLISHER_PASSWORD")
    result = _publish(_rtmp_url(f"rtmp-pub-{run_id}", "publisher", pw))
    assert result.returncode == 0, result.stderr


def test_anonymous_rtmp_publish_refused(run_id):
    assert _publish(_rtmp_url(f"rtmp-anon-{run_id}")).returncode != 0


def test_anonymous_rtmp_read_refused(live_path):
    assert _probe(_rtmp_url(live_path)).returncode != 0


def test_viewer_can_read_over_rtmp(stack_info, live_path):
    pw = mediamtx_secret(stack_info, "MEDIAMTX_VIEWER_PASSWORD")
    result = _probe(_rtmp_url(live_path, "viewer", pw))
    assert result.returncode == 0, result.stderr


def _hls_status(path: str, auth: tuple[str, str] | None = None) -> int:
    """Status of the playlist request once MediaMTX has decided on it.

    MediaMTX answers the first playlist request with 302 to the same URL plus
    `?cookieCheck=1`, before it looks at credentials, to learn whether the
    client keeps cookies. The decision (401, 404, 200) comes on the request
    after that, so follow the redirect. A 302 as the final status would mean
    it never decided, and no test here accepts one.
    """
    url = f"http://localhost:{HLS_PORT}/{path}/index.m3u8"
    with httpx.Client(auth=auth, timeout=20, follow_redirects=True, max_redirects=3) as client:
        return client.get(url).status_code


def test_anonymous_hls_read_refused(live_path):
    assert _hls_status(live_path) == 401


def test_wrong_hls_password_refused(live_path):
    assert _hls_status(live_path, ("viewer", "wrong")) == 401


def test_viewer_credential_passes_hls_auth(stack_info, run_id):
    """The probe path has no stream, so passing auth yields 404, not 401."""
    pw = mediamtx_secret(stack_info, "MEDIAMTX_VIEWER_PASSWORD")
    assert _hls_status(f"hls-empty-{run_id}", ("viewer", pw)) == 404


def _api_status(compose_exec, auth: str | None) -> str:
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}"]
    if auth:
        cmd += ["-u", auth]
    cmd.append("http://mediamtx:9997/v3/paths/list")
    return compose_exec("nodered", cmd).stdout.strip()


def test_api_requires_the_api_credential(stack_info, compose_exec):
    pw = mediamtx_secret(stack_info, "MEDIAMTX_API_PASSWORD")
    assert _api_status(compose_exec, None) == "401"
    assert _api_status(compose_exec, "api:wrong") == "401"
    assert _api_status(compose_exec, f"api:{pw}") == "200"


def _set_env(env_file: Path, key: str, value: str | None) -> None:
    """Replace (or with None, remove) KEY in the test .env."""
    lines = [ln for ln in env_file.read_text().splitlines() if not ln.startswith(f"{key}=")]
    if value is not None:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")


def _up(compose_cmd, compose_env, *services: str) -> None:
    subprocess.run(
        [*compose_cmd, "up", "-d", *services],
        check=True,
        capture_output=True,
        timeout=120,
        env=compose_env,
    )
    time.sleep(3)  # mediamtx has no healthcheck


def _container_id(compose_cmd, compose_env, service: str) -> str:
    return subprocess.run(
        [*compose_cmd, "ps", "-q", service],
        capture_output=True,
        text=True,
        env=compose_env,
        check=True,
    ).stdout.strip()


def test_default_publish_ips_refuse_the_publisher(stack_info, compose_cmd, compose_env, run_id):
    env_file = Path(stack_info.env_file)
    original = env_file.read_text()
    try:
        _set_env(env_file, "MEDIAMTX_PUBLISH_IPS", None)
        _up(compose_cmd, compose_env, "mediamtx")
        pw = mediamtx_secret(stack_info, "MEDIAMTX_PUBLISHER_PASSWORD")
        result = _publish(_url(f"off-{run_id}", "publisher", pw))
        assert result.returncode != 0
        assert "401" in result.stderr
    finally:
        env_file.write_text(original)
        _up(compose_cmd, compose_env, "mediamtx")


def test_rotating_the_viewer_password_is_edit_and_up(
    stack_info, compose_cmd, compose_env, live_path
):
    env_file = Path(stack_info.env_file)
    original = env_file.read_text()
    old_pw = mediamtx_secret(stack_info, "MEDIAMTX_VIEWER_PASSWORD")
    new_pw = "0123456789abcdef0123456789abcdef"
    before = {s: _container_id(compose_cmd, compose_env, s) for s in ("mediamtx", "nodered")}
    try:
        _set_env(env_file, "MEDIAMTX_VIEWER_PASSWORD", new_pw)
        _up(compose_cmd, compose_env)
        after = {s: _container_id(compose_cmd, compose_env, s) for s in ("mediamtx", "nodered")}
        assert after["mediamtx"] != before["mediamtx"], "mediamtx was not recreated"
        assert after["nodered"] != before["nodered"], "nodered was not recreated"
        # The recreate dropped live_path's publisher; publish again to read.
        pub = mediamtx_secret(stack_info, "MEDIAMTX_PUBLISHER_PASSWORD")
        thread = threading.Thread(
            target=_publish, args=(_url(live_path, "publisher", pub), 12), daemon=True
        )
        thread.start()
        time.sleep(4)
        assert "401" in _probe(_url(live_path, "viewer", old_pw)).stderr
        assert _probe(_url(live_path, "viewer", new_pw)).returncode == 0
        thread.join(timeout=30)
    finally:
        env_file.write_text(original)
        _up(compose_cmd, compose_env)


def test_config_file_alone_admits_nobody(stack_info):
    """Without Compose's overrides, the shipped file's entries are unusable.

    The refusal comes from the file's loopback-only `ips` (127.0.0.1/32,
    MediaMTX's own loopback), not from the password: `overridden-by-compose`
    is public in the repo by design, and the probe uses it as a remote client
    would. The probe path has no stream, so a successful auth would yield 404,
    which makes 401 discriminating.
    """
    version = mediamtx_secret(stack_info, "MEDIAMTX_VERSION")
    name = f"mtx-failclosed-{int(time.time())}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            "127.0.0.1::8554",
            "-v",
            f"{stack_info.repo_dir}/mediamtx/mediamtx.yml:/mediamtx.yml:ro",
            f"bluenviron/mediamtx:{version}",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    try:
        time.sleep(3)
        port = (
            subprocess.run(
                ["docker", "port", name, "8554/tcp"], capture_output=True, text=True, check=True
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        for user in ("viewer", "publisher", "api"):
            result = _probe(f"rtsp://{user}:overridden-by-compose@127.0.0.1:{port}/any")
            assert "401" in result.stderr, user
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def test_shipped_config_validates(stack_info):
    version = mediamtx_secret(stack_info, "MEDIAMTX_VERSION")
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{stack_info.repo_dir}/mediamtx/mediamtx.yml:/mediamtx.yml:ro",
            f"bluenviron/mediamtx:{version}",
            "--validate-conf=/mediamtx.yml",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
