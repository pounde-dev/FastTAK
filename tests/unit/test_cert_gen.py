"""Tests for service account certificate generation."""

import os
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from app.api.service_accounts.cert_gen import (
    _revoke_via_crl,
    generate_client_cert,
    parse_ca_subject,
    register_admin_cert,
    remove_nodered_pems,
    revoke_cert_by_name,
    revoke_certs_on_disk_for_user,
    unregister_admin_cert,
    write_nodered_pems,
)


class TestParseCaSubject:
    def test_extracts_fields_from_subject(self):
        subject = "C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"
        result = parse_ca_subject(subject)
        assert result == {
            "state": "XX",
            "city": "Default",
            "org_unit": "FastTAK",
        }

    def test_handles_missing_fields(self):
        subject = "C=US, CN=FastTAK-CA"
        result = parse_ca_subject(subject)
        assert result["state"] == "XX"
        assert result["city"] == "Default"
        assert result["org_unit"] == "FastTAK"

    def test_handles_spaces_around_equals(self):
        subject = "C = US, ST = Texas, L = Austin, O = TAK, OU = MyUnit, CN = Test"
        result = parse_ca_subject(subject)
        assert result["state"] == "Texas"
        assert result["city"] == "Austin"
        assert result["org_unit"] == "MyUnit"


class TestGenerateClientCert:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_generates_cert_with_default_validity(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (0, b"cert generated"),
            (0, b"signed"),
            (0, b"bundled"),
        ]

        result = generate_client_cert("svc_test", validity_days=365)
        assert result["success"] is True

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_when_container_missing(self, mock_find):
        mock_find.return_value = None
        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_validates_name(self, mock_find):
        result = generate_client_cert("invalid name!")
        assert result["success"] is False
        assert "Name must" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_csr_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (1, b"openssl error"),
        ]

        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "CSR generation failed" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_signing_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (0, b"ok"),
            (1, b"signing error"),
        ]

        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "Signing failed" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_p12_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (0, b"ok"),
            (0, b"ok"),
            (1, b"p12 error"),
        ]

        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "P12 creation failed" in result["error"]


def _disk_container(paths: list[str], cn_of=lambda path: "jsmith"):
    """A TAK container whose find returns `paths` and whose certs have the given CN."""

    def exec_run(cmd, **_):
        if cmd[0] == "find":
            return 0, "".join(f"{p}\n" for p in paths).encode()
        cn = cn_of(cmd[cmd.index("-in") + 1])
        if cn is None:
            return 1, b"unable to load certificate"
        return 0, f"subject=CN={cn},OU=FastTAK,O=TAK,ST=XX,C=US\n".encode()

    container = MagicMock()
    container.exec_run.side_effect = exec_run
    return container


class TestRevokeCertsOnDiskForUser:
    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_revokes_all_matching_pems(self, mock_find, mock_revoke):
        mock_find.return_value = _disk_container(
            ["/opt/tak/certs/files/jsmith-laptop.pem", "/opt/tak/certs/files/jsmith-phone.pem"]
        )
        mock_revoke.return_value = {"success": True}

        result = revoke_certs_on_disk_for_user("jsmith")

        assert result["success"] is True
        assert result["revoked"] == 2
        assert result["errors"] == []
        assert mock_revoke.call_count == 2
        # names passed to _revoke_via_crl have .pem stripped
        called_names = [call.args[0] for call in mock_revoke.call_args_list]
        assert "jsmith-laptop" in called_names
        assert "jsmith-phone" in called_names

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_success_when_no_pems_found(self, mock_find, mock_revoke):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"")

        result = revoke_certs_on_disk_for_user("noone")

        assert result["success"] is True
        assert result["revoked"] == 0
        mock_revoke.assert_not_called()

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_reports_per_cert_errors(self, mock_find, mock_revoke):
        mock_find.return_value = _disk_container(
            ["/opt/tak/certs/files/u-a.pem", "/opt/tak/certs/files/u-b.pem"],
            cn_of=lambda path: "u",
        )
        mock_revoke.side_effect = [
            {"success": True},
            {"success": False, "error": "crl regen failed"},
        ]

        result = revoke_certs_on_disk_for_user("u")

        assert result["success"] is False
        assert result["revoked"] == 1
        assert len(result["errors"]) == 1
        assert "u-b" in result["errors"][0]

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_service_account_cert_has_no_suffix(self, mock_find, mock_revoke):
        container = _disk_container(
            ["/opt/tak/certs/files/svc_bot.pem"], cn_of=lambda path: "svc_bot"
        )
        mock_find.return_value = container
        mock_revoke.return_value = {"success": True}

        result = revoke_certs_on_disk_for_user("svc_bot")

        assert result["revoked"] == 1
        assert mock_revoke.call_args.args[0] == "svc_bot"
        find_args = container.exec_run.call_args_list[0].args[0]
        assert "svc_bot.pem" in find_args

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_user_never_matches_an_exact_filename(self, mock_find, mock_revoke):
        """A user named takserver must not reach the server's own takserver.pem."""
        container = _disk_container([], cn_of=lambda path: "takserver")
        mock_find.return_value = container

        revoke_certs_on_disk_for_user("takserver")

        assert "takserver.pem" not in container.exec_run.call_args_list[0].args[0]

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_leaves_another_users_cert_alone(self, mock_find, mock_revoke):
        """Deactivating alice must not revoke alice-b's alice-b-phone.pem."""
        mock_find.return_value = _disk_container(
            ["/opt/tak/certs/files/alice-laptop.pem", "/opt/tak/certs/files/alice-b-phone.pem"],
            cn_of=lambda path: "alice-b" if "alice-b" in path else "alice",
        )
        mock_revoke.return_value = {"success": True}

        result = revoke_certs_on_disk_for_user("alice")

        assert result["revoked"] == 1
        assert [c.args[0] for c in mock_revoke.call_args_list] == ["alice-laptop"]

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_unreadable_cert_is_an_error_not_a_skip(self, mock_find, mock_revoke):
        mock_find.return_value = _disk_container(
            ["/opt/tak/certs/files/jsmith-laptop.pem"], cn_of=lambda path: None
        )
        result = revoke_certs_on_disk_for_user("jsmith")
        assert result["success"] is False
        mock_revoke.assert_not_called()

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_validates_username(self, mock_find):
        result = revoke_certs_on_disk_for_user("bad name!")
        assert result["success"] is False
        assert "Name must" in result["error"]
        mock_find.assert_not_called()

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_when_container_missing(self, mock_find):
        mock_find.return_value = None
        result = revoke_certs_on_disk_for_user("jsmith")
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_find_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (1, b"find: cannot access")

        result = revoke_certs_on_disk_for_user("jsmith")
        assert result["success"] is False
        assert "cannot access" in result["errors"][0]


class TestWriteNoderedPems:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_runs_openssl_extraction(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"")

        result = write_nodered_pems("svc_test")
        assert result["success"] is True
        container.exec_run.assert_called_once()
        # Verify the shell script references both cert and key extraction
        args, kwargs = container.exec_run.call_args
        script = args[0][2]  # ["sh", "-c", "<script>"]
        assert "-nokeys -clcerts" in script
        assert "-nocerts -nodes" in script
        assert kwargs["environment"]["NAME"] == "svc_test"

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_sets_cross_container_readable_perms(self, mock_find):
        """Regression: PEMs must be world-readable for the nodered container
        (running as a different UID) to open them through the bind mount.
        Earlier versions set the key to chmod 640, which produced EACCES in
        nodered. Pin the fix so it doesn't silently drift back.
        """
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"")

        write_nodered_pems("svc_test")
        script = container.exec_run.call_args[0][0][2]
        assert "chmod 755" in script, "NODERED_CERTS dir must be traversable"
        assert "chmod 644" in script, "PEM files must be world-readable"
        # Specifically, both the cert and key need 644 — not 640 on the key.
        assert "chmod 640" not in script, "640 on the key breaks cross-container read"

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (1, b"openssl: wrong password")

        result = write_nodered_pems("svc_test")
        assert result["success"] is False
        assert "wrong password" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_validates_name(self, mock_find):
        result = write_nodered_pems("bad name!")
        assert result["success"] is False
        assert "Name must" in result["error"]
        mock_find.assert_not_called()

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_when_container_missing(self, mock_find):
        mock_find.return_value = None
        result = write_nodered_pems("svc_test")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestRemoveNoderedPems:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_removes_pem_pair(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"")

        result = remove_nodered_pems("svc_test")
        assert result["success"] is True
        args, kwargs = container.exec_run.call_args
        script = args[0][2]
        assert "rm -f" in script
        assert ".cert.pem" in script
        assert ".key.pem" in script

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_idempotent_when_pems_absent(self, mock_find):
        """rm -f doesn't error on missing files, and we don't surface it either."""
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"")

        result = remove_nodered_pems("never_existed")
        assert result["success"] is True

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_validates_name(self, mock_find):
        result = remove_nodered_pems("bad name!")
        assert result["success"] is False
        mock_find.assert_not_called()


class TestRegisterAdminCert:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_runs_certmod(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"ROLE_ADMIN granted")

        result = register_admin_cert("svc_admin")
        assert result["success"] is True

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (1, b"InvocationTargetException")

        result = register_admin_cert("svc_admin")
        assert result["success"] is False

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_when_container_missing(self, mock_find):
        mock_find.return_value = None
        result = register_admin_cert("svc_admin")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestNameCollisions:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_generation_never_writes_over_existing_files(self, mock_find, tmp_path):
        (tmp_path / "ca-do-not-share.key").write_bytes(b"the CA key")
        with patch("app.api.service_accounts.cert_gen.CERT_FILES_PATH", tmp_path):
            result = generate_client_cert("ca-do-not-share")
        assert result["success"] is False
        mock_find.assert_not_called()

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_revoke_by_name_refuses_another_users_cert(self, mock_find, mock_revoke):
        mock_find.return_value = _disk_container([], cn_of=lambda path: "alice-b")
        result = revoke_cert_by_name("alice-b-phone", owner="alice")
        assert result["success"] is False
        mock_revoke.assert_not_called()

    @patch("app.api.service_accounts.cert_gen._revoke_via_crl")
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_revoke_by_name_accepts_the_owners_cert(self, mock_find, mock_revoke):
        mock_find.return_value = _disk_container([], cn_of=lambda path: "alice")
        mock_revoke.return_value = {"success": True}
        assert revoke_cert_by_name("alice-phone", owner="alice")["success"] is True
        mock_revoke.assert_called_once_with("alice-phone")


class TestUnregisterAdminCert:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_runs_certmod_delete(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"ok")
        assert unregister_admin_cert("svc_ops")["success"] is True
        cmd = container.exec_run.call_args.args[0]
        assert cmd[-3:] == ["certmod", "-D", "/opt/tak/certs/files/svc_ops.pem"][-3:]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_reports_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (1, b"no such user")
        assert unregister_admin_cert("svc_ops")["success"] is False


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
class TestRevokeViaCrlWithRealOpenssl:
    """Runs the real shell and openssl. A mock cannot show what the && chain does."""

    @pytest.fixture
    def pki(self, tmp_path, monkeypatch):
        def run(*args):
            subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

        (tmp_path / "config.cfg").write_text(
            "[ ca ]\ndefault_ca = CA_default\n[ CA_default ]\n"
            "database = crl_index.txt\ndefault_md = sha256\ndefault_crl_days = 730\n"
        )
        run("openssl", "genrsa", "-aes256", "-passout", "pass:atakatak",
            "-out", "ca-do-not-share.key", "2048")  # fmt: skip
        run("openssl", "req", "-x509", "-new", "-key", "ca-do-not-share.key",
            "-passin", "pass:atakatak", "-subj", "/CN=ca", "-days", "30",
            "-out", "ca.pem")  # fmt: skip
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "c.key",
            "-subj", "/C=US/ST=XX/O=TAK/OU=F/CN=svc_bot", "-out", "c.csr")  # fmt: skip
        run("openssl", "x509", "-req", "-in", "c.csr", "-CA", "ca.pem",
            "-CAkey", "ca-do-not-share.key", "-passin", "pass:atakatak",
            "-set_serial", "0x1D9070DD44F99BE7", "-days", "5", "-out", "svc_bot.pem")  # fmt: skip
        monkeypatch.setattr("app.api.service_accounts.cert_gen.CERT_FILES", str(tmp_path))
        monkeypatch.setattr("app.api.service_accounts.cert_gen.CERT_DIR", str(tmp_path))
        return tmp_path

    @staticmethod
    def _container():
        def exec_run(cmd, environment=None):
            # BSD sed reads "-i pattern" differently, and these PEMs have no blank lines.
            script = cmd[2].replace('sed -i "/^$/d" "${NAME}.pem"', "true")
            done = subprocess.run(
                ["sh", "-c", script],
                env={**os.environ, **environment},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            return done.returncode, done.stdout

        container = MagicMock()
        container.exec_run.side_effect = exec_run
        return container

    def test_second_revoke_succeeds(self, pki):
        container = self._container()
        assert _revoke_via_crl("svc_bot", container=container)["success"] is True
        assert _revoke_via_crl("svc_bot", container=container)["success"] is True

    def test_retry_rebuilds_a_missing_crl(self, pki):
        """The revoke can land in the index with the CRL write then failing."""
        container = self._container()
        _revoke_via_crl("svc_bot", container=container)
        (pki / "ca.crl").unlink()
        assert _revoke_via_crl("svc_bot", container=container)["success"] is True
        listed = subprocess.run(
            ["openssl", "crl", "-in", str(pki / "ca.crl"), "-noout", "-text"],
            capture_output=True,
            text=True,
        )
        assert "1D9070DD44F99BE7" in listed.stdout

    def test_other_failures_are_still_failures(self, pki):
        result = _revoke_via_crl("no_such_cert", container=self._container())
        assert result["success"] is False
