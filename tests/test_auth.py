"""Tests for auth.py — credential loading and FalconPy client factories."""

import builtins
import runpy
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

    @patch("falconpy.Workflows")
    def test_self_test_workflows_token_expired(
        self, mock_wf, fake_credentials, capsys
    ):
        wf = MagicMock()
        wf.token_expired.return_value = True
        mock_wf.return_value = wf
        with pytest.raises(SystemExit):
            runpy.run_path(auth.__file__, run_name="__main__")
        assert "Workflows" in capsys.readouterr().err

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
