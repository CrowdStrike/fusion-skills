"""Tests for auth.py — credential loading and FalconPy client factories."""

import builtins
import runpy
import subprocess
import sys
import types
from unittest.mock import patch, MagicMock

import pytest

import auth


class TestGetCredentials:
    """Test credential retrieval and resolution order."""

    def test_returns_credentials_from_env(self, fake_credentials):
        cid, csec, burl = auth.get_credentials()
        assert cid == "fake_client_id_1234567890abcdef"
        assert csec == "fake_secret_abcdef1234567890"
        assert burl == "https://api.crowdstrike.com"

    def test_default_base_url_when_unset(self, monkeypatch):
        monkeypatch.setenv("FALCON_CLIENT_ID", "test_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "test_secret")
        _, _, burl = auth.get_credentials()
        assert burl == auth.DEFAULT_BASE_URL

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("FALCON_CLIENT_ID", "test")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "test")
        monkeypatch.setenv("FALCON_BASE_URL", "https://api.crowdstrike.com/")
        _, _, burl = auth.get_credentials()
        assert not burl.endswith("/")

    def test_exits_without_credentials(self):
        with pytest.raises(SystemExit):
            auth.get_credentials()

    def test_credentials_from_toml_profile(self, tmp_path, monkeypatch):
        """With no env vars, credentials come from the TOML profile file."""
        toml_file = tmp_path / "credentials.toml"
        toml_file.write_text(
            'default = "us-1"\n\n'
            "[us-1]\n"
            'client_id = "toml_id"\n'
            'client_secret = "toml_secret"\n'
            'base_url = "https://api.us-2.crowdstrike.com"\n'
        )
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        cid, csec, burl = auth.get_credentials()
        assert cid == "toml_id"
        assert csec == "toml_secret"
        assert burl == "https://api.us-2.crowdstrike.com"

    def test_falcon_profile_selects_toml_section(self, tmp_path, monkeypatch):
        """FALCON_PROFILE overrides the file's `default` key."""
        toml_file = tmp_path / "credentials.toml"
        toml_file.write_text(
            'default = "us-1"\n\n'
            "[us-1]\n"
            'client_id = "us1_id"\n'
            'client_secret = "us1_secret"\n\n'
            "[eu-1]\n"
            'client_id = "eu1_id"\n'
            'client_secret = "eu1_secret"\n'
        )
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        monkeypatch.setenv("FALCON_PROFILE", "eu-1")
        cid, csec, _ = auth.get_credentials()
        assert cid == "eu1_id"
        assert csec == "eu1_secret"

    def test_toml_base_url_defaults_when_absent(self, tmp_path, monkeypatch):
        """A TOML profile with no base_url falls back to DEFAULT_BASE_URL."""
        toml_file = tmp_path / "credentials.toml"
        toml_file.write_text(
            'default = "us-1"\n\n'
            "[us-1]\n"
            'client_id = "toml_id"\n'
            'client_secret = "toml_secret"\n'
        )
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        _, _, burl = auth.get_credentials()
        assert burl == auth.DEFAULT_BASE_URL

    def test_env_vars_outrank_toml(self, tmp_path, monkeypatch):
        """Environment variables win over the TOML profile file."""
        toml_file = tmp_path / "credentials.toml"
        toml_file.write_text(
            'default = "us-1"\n\n'
            "[us-1]\n"
            'client_id = "toml_id"\n'
            'client_secret = "toml_secret"\n'
        )
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        monkeypatch.setenv("FALCON_CLIENT_ID", "env_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "env_secret")
        cid, csec, _ = auth.get_credentials()
        assert cid == "env_id"
        assert csec == "env_secret"

    def test_incomplete_toml_profile_falls_through_to_error(
        self, tmp_path, monkeypatch
    ):
        """A TOML profile missing a secret is not usable, so auth exits."""
        toml_file = tmp_path / "credentials.toml"
        toml_file.write_text(
            'default = "us-1"\n\n'
            "[us-1]\n"
            'client_id = "toml_id"\n'
        )
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        with pytest.raises(SystemExit):
            auth.get_credentials()


class TestGetClient:
    """Test the FalconPy Workflows and NGSIEM client factories."""

    @patch("falconpy.Workflows")
    def test_returns_workflows_instance(self, mock_workflows, fake_credentials):
        mock_instance = MagicMock()
        mock_workflows.return_value = mock_instance
        client = auth.get_client()
        assert client is mock_instance
        mock_workflows.assert_called_once_with(
            client_id="fake_client_id_1234567890abcdef",
            client_secret="fake_secret_abcdef1234567890",
            base_url="https://api.crowdstrike.com",
        )

    @patch("falconpy.Workflows")
    def test_workflows_client_is_singleton(self, mock_workflows, fake_credentials):
        mock_workflows.return_value = MagicMock()
        first = auth.get_client()
        second = auth.get_client()
        assert first is second
        assert mock_workflows.call_count == 1

    @patch("falconpy.NGSIEM")
    def test_returns_ngsiem_instance(self, mock_ngsiem, fake_credentials):
        mock_instance = MagicMock()
        mock_ngsiem.return_value = mock_instance
        client = auth.get_ngsiem_client()
        assert client is mock_instance
        assert mock_ngsiem.call_count == 1

    @patch("falconpy.NGSIEM")
    @patch("falconpy.Workflows")
    def test_clients_are_independent(
        self, mock_workflows, mock_ngsiem, fake_credentials
    ):
        """Requesting one client must not construct the other."""
        mock_workflows.return_value = MagicMock()
        auth.get_client()
        assert mock_ngsiem.call_count == 0

    @patch("falconpy.Workflows")
    def test_reset_clears_singleton(self, mock_workflows, fake_credentials):
        mock_workflows.return_value = MagicMock()
        first = auth.get_client()
        auth.reset_client()
        mock_workflows.return_value = MagicMock()
        second = auth.get_client()
        assert first is not second
        assert mock_workflows.call_count == 2

    @patch("falconpy.NGSIEM")
    def test_ngsiem_client_is_singleton(self, mock_ngsiem, fake_credentials):
        mock_ngsiem.return_value = MagicMock()
        first = auth.get_ngsiem_client()
        second = auth.get_ngsiem_client()
        assert first is second
        assert mock_ngsiem.call_count == 1

    @patch("falconpy.NGSIEM")
    def test_ngsiem_built_with_credentials(self, mock_ngsiem, fake_credentials):
        mock_ngsiem.return_value = MagicMock()
        auth.get_ngsiem_client()
        mock_ngsiem.assert_called_once_with(
            client_id="fake_client_id_1234567890abcdef",
            client_secret="fake_secret_abcdef1234567890",
            base_url="https://api.crowdstrike.com",
        )

    @patch("falconpy.NGSIEM")
    def test_reset_clears_ngsiem_singleton(self, mock_ngsiem, fake_credentials):
        mock_ngsiem.return_value = MagicMock()
        first = auth.get_ngsiem_client()
        auth.reset_client()
        mock_ngsiem.return_value = MagicMock()
        second = auth.get_ngsiem_client()
        assert first is not second
        assert mock_ngsiem.call_count == 2


class TestLoadToml:
    """Test the TOML parser helper _load_toml()."""

    def test_missing_file_returns_none(self):
        assert auth._load_toml("/nonexistent/does/not/exist.toml") is None

    def test_malformed_toml_returns_none(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("this is = = not [[[ valid toml\n")
        assert auth._load_toml(str(bad)) is None

    def test_uses_tomli_when_tomllib_missing(self, tmp_path, monkeypatch):
        """The tomli fallback path works even when tomli isn't installed.

        Inject a fake `tomli` module so the branch is exercised regardless of
        environment — this tests the fallback logic, not "is tomli installed."
        """
        toml_file = tmp_path / "c.toml"
        toml_file.write_text('[us-1]\nclient_id = "a"\nclient_secret = "b"\n')
        fake_tomli = types.ModuleType("tomli")
        fake_tomli.load = lambda f: {"us-1": {"client_id": "a", "client_secret": "b"}}
        monkeypatch.setitem(sys.modules, "tomli", fake_tomli)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "tomllib":
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = auth._load_toml(str(toml_file))
        assert result == {"us-1": {"client_id": "a", "client_secret": "b"}}

    def test_no_toml_parser_returns_none(self, tmp_path, monkeypatch):
        """If neither tomllib nor tomli import, TOML parsing is skipped."""
        toml_file = tmp_path / "c.toml"
        toml_file.write_text('[us-1]\nclient_id = "a"\n')
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("tomllib", "tomli"):
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert auth._load_toml(str(toml_file)) is None


class TestCredsFromToml:
    """Test profile-selection edge cases in _creds_from_toml()."""

    def test_no_profile_and_no_default_returns_none(self, tmp_path, monkeypatch):
        """Without FALCON_PROFILE or a top-level `default`, there is no profile."""
        toml_file = tmp_path / "c.toml"
        toml_file.write_text('[us-1]\nclient_id = "a"\nclient_secret = "b"\n')
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        assert auth._creds_from_toml() is None

    def test_default_points_at_missing_section_returns_none(
        self, tmp_path, monkeypatch
    ):
        """A `default` naming a nonexistent section yields no credentials."""
        toml_file = tmp_path / "c.toml"
        toml_file.write_text(
            'default = "nope"\n\n[us-1]\nclient_id = "a"\nclient_secret = "b"\n'
        )
        monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))
        assert auth._creds_from_toml() is None

    def test_missing_toml_file_returns_none(self, tmp_path, monkeypatch):
        """A nonexistent TOML file means _load_toml returns None (not a dict)."""
        monkeypatch.setattr(
            auth, "TOML_CREDENTIALS_PATH", str(tmp_path / "absent.toml")
        )
        assert auth._creds_from_toml() is None


def _write_profile(tmp_path, monkeypatch, body):
    """Write a us-1 TOML profile with `body` and point auth at it."""
    toml_file = tmp_path / "credentials.toml"
    toml_file.write_text('default = "us-1"\n\n[us-1]\n' + body)
    monkeypatch.setattr(auth, "TOML_CREDENTIALS_PATH", str(toml_file))


def _op_result(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["op"], returncode, stdout, stderr)


def _fake_inject(values):
    """A subprocess.run stand-in for `op inject` that resolves from `values`."""

    def run(args, input, **_kwargs):  # pylint: disable=redefined-builtin
        assert args == ["op", "inject"]
        refs = [line[3:-3] for line in input.splitlines()]  # strip "{{ " and " }}"
        return _op_result("".join(values[r] + "\n" for r in refs))

    return run


class TestSecretReferences:
    """Test 1Password op:// resolution for client_id and client_secret."""

    def test_plain_values_never_call_op(self, tmp_path, monkeypatch):
        _write_profile(
            tmp_path, monkeypatch, 'client_id = "plain_id"\nclient_secret = "plain_secret"\n'
        )
        with patch("auth.subprocess.run") as run:
            cid, csec, _ = auth.get_credentials()
        run.assert_not_called()
        assert (cid, csec) == ("plain_id", "plain_secret")

    def test_toml_op_references_resolve_in_one_op_call(self, tmp_path, monkeypatch):
        _write_profile(
            tmp_path,
            monkeypatch,
            'client_id = "op://Vault/Falcon API/username"\n'
            'client_secret = "op://Vault/Falcon API/credential"\n',
        )
        values = {
            "op://Vault/Falcon API/username": "resolved_id",
            "op://Vault/Falcon API/credential": "resolved_secret",
        }
        with patch("auth.subprocess.run", side_effect=_fake_inject(values)) as run:
            cid, csec, _ = auth.get_credentials()
        assert (cid, csec) == ("resolved_id", "resolved_secret")
        # One approval per run, and an argument list, never a shell string.
        assert run.call_count == 1
        assert "shell" not in run.call_args.kwargs

    def test_says_it_is_waiting_on_1password(self, monkeypatch, capsys):
        monkeypatch.setenv("FALCON_CLIENT_ID", "env_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        with patch("auth.subprocess.run", return_value=_op_result("secret\n")):
            auth.get_credentials()
        captured = capsys.readouterr()
        assert "Approve the 1Password prompt" in captured.err
        assert captured.out == ""  # stdout stays clean for JSON output

    def test_only_op_values_go_to_op(self, monkeypatch):
        monkeypatch.setenv("FALCON_CLIENT_ID", "env_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        values = {"op://Vault/Falcon API/credential": "env_secret"}
        with patch("auth.subprocess.run", side_effect=_fake_inject(values)) as run:
            cid, csec, _ = auth.get_credentials()
        assert (cid, csec) == ("env_id", "env_secret")
        assert run.call_args.kwargs["input"] == "{{ op://Vault/Falcon API/credential }}\n"

    def test_failed_op_call_exits_with_clear_error(self, tmp_path, monkeypatch, capsys):
        _write_profile(
            tmp_path,
            monkeypatch,
            'client_id = "plain_id"\nclient_secret = "op://Vault/Falcon API/credential"\n',
        )
        failed = _op_result(returncode=1, stderr="account is not signed in")
        with patch("auth.subprocess.run", return_value=failed), pytest.raises(SystemExit):
            auth.get_credentials()
        err = capsys.readouterr().err
        assert "Couldn't read client_secret from 1Password" in err
        assert "op signin" in err
        assert "account is not signed in" in err

    def test_failed_op_call_never_prints_partial_secret(self, monkeypatch, capsys):
        monkeypatch.setenv("FALCON_CLIENT_ID", "env_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        partial = _op_result(stdout="half_a_secret\n", returncode=1)
        with patch("auth.subprocess.run", return_value=partial), pytest.raises(SystemExit):
            auth.get_credentials()
        captured = capsys.readouterr()
        assert "half_a_secret" not in captured.out + captured.err

    def test_missing_or_empty_output_is_an_error(self, monkeypatch, capsys):
        monkeypatch.setenv("FALCON_CLIENT_ID", "op://Vault/Falcon API/username")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        # Two references but only one (blank) line back.
        with patch("auth.subprocess.run", return_value=_op_result("\n")), pytest.raises(
            SystemExit
        ):
            auth.get_credentials()
        assert "FALCON_CLIENT_ID and FALCON_CLIENT_SECRET" in capsys.readouterr().err

    def test_blank_resolved_value_is_an_error(self, monkeypatch, capsys):
        monkeypatch.setenv("FALCON_CLIENT_ID", "op://Vault/Falcon API/username")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        with patch("auth.subprocess.run", return_value=_op_result("id\n \n")), pytest.raises(
            SystemExit
        ):
            auth.get_credentials()
        assert "Couldn't read" in capsys.readouterr().err

    def test_missing_op_cli_exits_with_install_hint(self, monkeypatch, capsys):
        monkeypatch.setenv("FALCON_CLIENT_ID", "env_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        with patch("auth.subprocess.run", side_effect=FileNotFoundError), pytest.raises(
            SystemExit
        ):
            auth.get_credentials()
        assert "1Password CLI (op) isn't installed" in capsys.readouterr().err

    def test_op_timeout_exits_with_clear_error(self, monkeypatch, capsys):
        monkeypatch.setenv("FALCON_CLIENT_ID", "env_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        timeout = subprocess.TimeoutExpired(cmd="op", timeout=120)
        with patch("auth.subprocess.run", side_effect=timeout), pytest.raises(SystemExit):
            auth.get_credentials()
        assert "Timed out reading FALCON_CLIENT_SECRET" in capsys.readouterr().err


class TestCredentialCache:
    """Test that credentials resolve once per process and report their source."""

    def test_op_references_are_read_once_for_both_clients(self, monkeypatch):
        monkeypatch.setenv("FALCON_CLIENT_ID", "op://Vault/Falcon API/username")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "op://Vault/Falcon API/credential")
        with patch("auth.subprocess.run", return_value=_op_result("id\nsecret\n")) as run, patch(
            "falconpy.Workflows"
        ), patch("falconpy.NGSIEM"):
            auth.get_client()
            auth.get_ngsiem_client()
            auth.get_credentials()
        assert run.call_count == 1

    def test_reset_client_clears_cached_credentials(self, monkeypatch):
        monkeypatch.setenv("FALCON_CLIENT_ID", "first_id")
        monkeypatch.setenv("FALCON_CLIENT_SECRET", "first_secret")
        assert auth.get_credentials()[0] == "first_id"
        monkeypatch.setenv("FALCON_CLIENT_ID", "second_id")
        assert auth.get_credentials()[0] == "first_id"
        auth.reset_client()
        assert auth.get_credentials()[0] == "second_id"

    def test_source_names_env_vars(self, fake_credentials):
        assert auth.get_credentials_source() == "environment variables"

    def test_source_names_profile_and_1password(self, tmp_path, monkeypatch):
        _write_profile(
            tmp_path, monkeypatch, 'client_id = "a"\nclient_secret = "op://Vault/Falcon API/credential"\n'
        )
        with patch("auth.subprocess.run", return_value=_op_result("secret\n")):
            source = auth.get_credentials_source()
        assert source == f"profile us-1 in {tmp_path / 'credentials.toml'} (1Password)"

    def test_source_for_plain_profile_has_no_1password_suffix(self, tmp_path, monkeypatch):
        _write_profile(tmp_path, monkeypatch, 'client_id = "a"\nclient_secret = "b"\n')
        assert auth.get_credentials_source().endswith("credentials.toml")


class TestCaBundle:
    """Test the per-profile ca_bundle option for TLS-inspecting proxies."""

    def test_ca_bundle_sets_requests_ca_bundle(self, tmp_path, monkeypatch):
        # Registers the key with monkeypatch so the value auth sets is undone.
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "")
        bundle = tmp_path / "ca.pem"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n")
        _write_profile(
            tmp_path,
            monkeypatch,
            f'client_id = "a"\nclient_secret = "b"\nca_bundle = "{bundle}"\n',
        )
        auth.get_credentials()
        assert auth.os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)

    def test_profile_ca_bundle_overrides_inherited_env(self, tmp_path, monkeypatch):
        bundle = tmp_path / "ca.pem"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n")
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/inherited/wrong.pem")
        _write_profile(
            tmp_path,
            monkeypatch,
            f'client_id = "a"\nclient_secret = "b"\nca_bundle = "{bundle}"\n',
        )
        auth.get_credentials()
        assert auth.os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)
        assert auth.describe_ca_bundle() == (
            f"{bundle} (profile ca_bundle, overrides REQUESTS_CA_BUNDLE)"
        )

    def test_describe_profile_ca_bundle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "")
        bundle = tmp_path / "ca.pem"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n")
        _write_profile(
            tmp_path,
            monkeypatch,
            f'client_id = "a"\nclient_secret = "b"\nca_bundle = "{bundle}"\n',
        )
        auth.get_credentials()
        assert auth.describe_ca_bundle() == f"{bundle} (profile ca_bundle)"

    def test_describe_env_ca_bundles(self, monkeypatch):
        monkeypatch.setenv("CURL_CA_BUNDLE", "/curl.pem")
        assert auth.describe_ca_bundle() == "/curl.pem (CURL_CA_BUNDLE)"
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/env.pem")
        assert auth.describe_ca_bundle() == "/env.pem (REQUESTS_CA_BUNDLE)"

    def test_describe_default_ca_bundle(self):
        assert auth.describe_ca_bundle() == "default (certifi)"

    def test_missing_ca_bundle_file_exits(self, tmp_path, monkeypatch, capsys):
        _write_profile(
            tmp_path,
            monkeypatch,
            'client_id = "a"\nclient_secret = "b"\nca_bundle = "/nonexistent/ca.pem"\n',
        )
        with pytest.raises(SystemExit):
            auth.get_credentials()
        assert "ca_bundle file doesn't exist" in capsys.readouterr().err
        assert "REQUESTS_CA_BUNDLE" not in auth.os.environ


class TestFalconPyVersionGuard:
    """Test the minimum-FalconPy-version guard in get_client/get_ngsiem_client."""

    def _fake_falconpy(self, version_str):
        """Build a fake `falconpy` module reporting the given version()."""
        mod = types.ModuleType("falconpy")
        mod.version = lambda: version_str
        mod.Workflows = MagicMock(return_value=MagicMock())
        mod.NGSIEM = MagicMock(return_value=MagicMock())
        return mod

    def test_version_parses_from_version_fn(self):
        fake = self._fake_falconpy("1.6.3")
        assert auth._falconpy_version(fake) == (1, 6, 3)

    def test_version_falls_back_to_dunder(self):
        mod = types.ModuleType("falconpy")
        mod.__version__ = "1.7.0"  # no version() callable
        assert auth._falconpy_version(mod) == (1, 7, 0)

    def test_unparseable_version_is_treated_as_too_old(self):
        mod = types.ModuleType("falconpy")
        mod.version = lambda: "not-a-version"
        # Fails closed: unknown version parses to (0, 0, 0), below the floor.
        assert auth._falconpy_version(mod) == (0, 0, 0)

    def test_below_floor_raises_with_clear_message(self, fake_credentials, monkeypatch):
        fake = self._fake_falconpy("1.6.1")
        monkeypatch.setitem(sys.modules, "falconpy", fake)
        with pytest.raises(RuntimeError) as excinfo:
            auth.get_client()
        msg = str(excinfo.value)
        assert "1.6.3" in msg          # the required floor
        assert "1.6.1" in msg          # the version found
        assert "pip install -U crowdstrike-falconpy" in msg
        # A too-old SDK must not construct a client.
        assert fake.Workflows.call_count == 0

    def test_adequate_version_passes(self, fake_credentials, monkeypatch):
        fake = self._fake_falconpy("1.6.3")
        monkeypatch.setitem(sys.modules, "falconpy", fake)
        client = auth.get_client()
        assert client is fake.Workflows.return_value
        assert fake.Workflows.call_count == 1

    def test_newer_version_passes(self, fake_credentials, monkeypatch):
        fake = self._fake_falconpy("2.0.0")
        monkeypatch.setitem(sys.modules, "falconpy", fake)
        assert auth.get_client() is fake.Workflows.return_value

    def test_ngsiem_below_floor_raises(self, fake_credentials, monkeypatch):
        fake = self._fake_falconpy("1.6.1")
        monkeypatch.setitem(sys.modules, "falconpy", fake)
        with pytest.raises(RuntimeError):
            auth.get_ngsiem_client()
        assert fake.NGSIEM.call_count == 0

    def test_ngsiem_adequate_version_passes(self, fake_credentials, monkeypatch):
        fake = self._fake_falconpy("1.6.3")
        monkeypatch.setitem(sys.modules, "falconpy", fake)
        assert auth.get_ngsiem_client() is fake.NGSIEM.return_value


class TestSelfTestMain:
    """Exercise the __main__ self-test block via runpy."""

    @patch("falconpy.NGSIEM")
    @patch("falconpy.Workflows")
    def test_self_test_success(self, mock_wf, mock_ngsiem, fake_credentials, capsys):
        wf = MagicMock()
        wf.token_expired.return_value = False
        mock_wf.return_value = wf
        ng = MagicMock()
        ng.token_expired.return_value = False
        mock_ngsiem.return_value = ng
        runpy.run_path(auth.__file__, run_name="__main__")
        out = capsys.readouterr().out
        assert "Authentication successful (FalconPy Workflows client)" in out
        assert "Authentication successful (FalconPy NGSIEM client)" in out
        assert "7890" not in out  # no part of the secret is printed

    @patch("falconpy.NGSIEM")
    @patch("falconpy.Workflows")
    def test_self_test_shows_discovered_base_url(
        self, mock_wf, mock_ngsiem, fake_credentials, capsys
    ):
        wf = MagicMock(base_url="https://api.us-2.crowdstrike.com")
        wf.token_expired.return_value = False
        mock_wf.return_value = wf
        mock_ngsiem.return_value.token_expired.return_value = False
        runpy.run_path(auth.__file__, run_name="__main__")
        assert (
            "Base URL  : https://api.us-2.crowdstrike.com (discovered)"
            in capsys.readouterr().out
        )

    @patch("falconpy.NGSIEM")
    @patch("falconpy.Workflows")
    def test_self_test_configured_base_url_has_no_note(
        self, mock_wf, mock_ngsiem, fake_credentials, capsys
    ):
        wf = MagicMock(base_url="https://api.crowdstrike.com")
        wf.token_expired.return_value = False
        mock_wf.return_value = wf
        mock_ngsiem.return_value.token_expired.return_value = False
        runpy.run_path(auth.__file__, run_name="__main__")
        out = capsys.readouterr().out
        assert "Base URL  : https://api.crowdstrike.com\n" in out

    @patch("falconpy.Workflows")
    def test_self_test_workflows_token_expired(
        self, mock_wf, fake_credentials, capsys
    ):
        wf = MagicMock()
        wf.token_expired.return_value = True
        mock_wf.return_value = wf
        with pytest.raises(SystemExit):
            runpy.run_path(auth.__file__, run_name="__main__")
        captured = capsys.readouterr()
        assert "Workflows" in captured.err
        assert "check the CA" in captured.err  # points at the CA bundle line
        assert "CA bundle : default (certifi)" in captured.out

    @patch("falconpy.NGSIEM")
    @patch("falconpy.Workflows")
    def test_self_test_ngsiem_token_expired(
        self, mock_wf, mock_ngsiem, fake_credentials, capsys
    ):
        wf = MagicMock()
        wf.token_expired.return_value = False
        mock_wf.return_value = wf
        ng = MagicMock()
        ng.token_expired.return_value = True
        mock_ngsiem.return_value = ng
        with pytest.raises(SystemExit):
            runpy.run_path(auth.__file__, run_name="__main__")
        assert "NGSIEM" in capsys.readouterr().err

    @patch("falconpy.Workflows")
    def test_self_test_exception_path(self, mock_wf, fake_credentials, capsys):
        mock_wf.side_effect = RuntimeError("boom")
        with pytest.raises(SystemExit):
            runpy.run_path(auth.__file__, run_name="__main__")
        assert "Authentication FAILED" in capsys.readouterr().err
