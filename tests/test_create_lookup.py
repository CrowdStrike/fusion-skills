"""Tests for create_lookup.py."""

import json
import sys

import pytest

import create_lookup


class TestValidateFile:
    """Test file validation logic."""

    def test_file_not_found(self):
        ok, msg = create_lookup.validate_file("/nonexistent/file.csv")
        assert not ok
        assert "not found" in msg.lower()

    def test_valid_csv(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        ok, _msg = create_lookup.validate_file(str(f))
        assert ok

    def test_valid_json(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('[{"a": 1}]')
        ok, _msg = create_lookup.validate_file(str(f))
        assert ok

    def test_valid_txt(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        ok, _msg = create_lookup.validate_file(str(f))
        assert ok

    def test_unexpected_extension(self, tmp_path):
        f = tmp_path / "test.xlsx"
        f.write_text("data")
        ok, msg = create_lookup.validate_file(str(f))
        assert not ok
        assert "unexpected" in msg.lower()


class TestCreateLookup:
    """Test create API call handling."""

    def test_successful_create(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "create_lookup_file": lambda self, **kw: {
                "status_code": 200,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = create_lookup.create_lookup(str(f))
        assert success
        assert "created" in msg.lower()

    def test_failed_create_with_error_message(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "create_lookup_file": lambda self, **kw: {
                "status_code": 409,
                "body": {"errors": [{"message": "File already exists"}]},
            }
        })()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = create_lookup.create_lookup(str(f))
        assert not success
        assert "already exists" in msg.lower()

    def test_failed_create_bad_status(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "create_lookup_file": lambda self, **kw: {
                "status_code": 500,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = create_lookup.create_lookup(str(f))
        assert not success
        assert "500" in msg

    def test_default_filename_from_path(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "blocklist.csv"
        f.write_text("a,b\n1,2\n")
        captured = {}

        def _create(self, **kw):
            captured.update(kw)
            return {"status_code": 200, "body": {"errors": []}}

        mock_client = type("Mock", (), {"create_lookup_file": _create})()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        create_lookup.create_lookup(str(f))
        assert captured["filename"] == "blocklist.csv"

    def test_custom_filename_and_no_search_domain(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n")
        captured = {}

        def _create(self, **kw):
            captured.update(kw)
            return {"status_code": 201, "body": {"errors": []}}

        mock_client = type("Mock", (), {"create_lookup_file": _create})()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        success, _msg = create_lookup.create_lookup(str(f), filename="remote.csv")
        assert success
        assert captured["filename"] == "remote.csv"
        # A lookup must NOT be scoped to a search domain, or CQL match() cannot
        # resolve it. Regression guard: never send search_domain.
        assert "search_domain" not in captured


class TestMain:
    """Test the CLI entry point."""

    def test_main_missing_file_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["create_lookup.py", "--file", "/nope/x.csv", "--json"])
        with pytest.raises(SystemExit) as exc:
            create_lookup.main()
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    def test_main_success_json(self, tmp_path, monkeypatch, fake_credentials, capsys):
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "create_lookup_file": lambda self, **kw: {
                "status_code": 200,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        monkeypatch.setattr(sys, "argv", ["create_lookup.py", "--file", str(f), "--json"])
        create_lookup.main()
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["filename"] == "data.csv"

    def test_main_failure_exits(self, tmp_path, monkeypatch, fake_credentials, capsys):
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "create_lookup_file": lambda self, **kw: {
                "status_code": 409,
                "body": {"errors": [{"message": "exists"}]},
            }
        })()
        monkeypatch.setattr(create_lookup, "get_ngsiem_client", lambda: mock_client)
        monkeypatch.setattr(sys, "argv", ["create_lookup.py", "--file", str(f), "--json"])
        with pytest.raises(SystemExit) as exc:
            create_lookup.main()
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
