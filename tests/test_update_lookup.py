"""Tests for update_lookup.py."""

import json
import sys

import pytest

import update_lookup


class TestValidateFile:
    """Test file validation (same logic as create_lookup)."""

    def test_file_not_found(self):
        ok, msg = update_lookup.validate_file("/nonexistent/file.csv")
        assert not ok
        assert "not found" in msg.lower()

    def test_valid_csv(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        ok, _msg = update_lookup.validate_file(str(f))
        assert ok

    def test_unexpected_extension(self, tmp_path):
        f = tmp_path / "test.xlsx"
        f.write_text("data")
        ok, msg = update_lookup.validate_file(str(f))
        assert not ok
        assert "unexpected" in msg.lower()


class TestUpdateLookup:
    """Test update API call handling."""

    def test_successful_update(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "update_lookup_file": lambda self, **kw: {
                "status_code": 200,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(update_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = update_lookup.update_lookup(str(f), "test.csv")
        assert success
        assert "updated" in msg.lower()

    def test_failed_update(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "update_lookup_file": lambda self, **kw: {
                "status_code": 404,
                "body": {"errors": [{"message": "File not found"}]},
            }
        })()
        monkeypatch.setattr(update_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = update_lookup.update_lookup(str(f), "missing.csv")
        assert not success
        assert "not found" in msg.lower()

    def test_bad_status_without_errors(self, tmp_path, monkeypatch, fake_credentials):
        """A non-success status with no errors array still reports failure."""
        f = tmp_path / "test.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "update_lookup_file": lambda self, **kw: {
                "status_code": 500,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(update_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = update_lookup.update_lookup(str(f), "test.csv")
        assert not success
        assert "500" in msg

    def test_passes_filename_and_no_search_domain(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n")
        captured = {}

        def _update(self, **kw):
            captured.update(kw)
            return {"status_code": 200, "body": {"errors": []}}

        mock_client = type("Mock", (), {"update_lookup_file": _update})()
        monkeypatch.setattr(update_lookup, "get_ngsiem_client", lambda: mock_client)
        update_lookup.update_lookup(str(f), "remote.csv")
        assert captured["filename"] == "remote.csv"
        # Regression guard: never send search_domain, or match() cannot resolve
        # the updated file.
        assert "search_domain" not in captured


class TestMain:
    """Test the CLI entry point."""

    def test_main_missing_file_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys, "argv",
            ["update_lookup.py", "--name", "x.csv", "--file", "/nope/x.csv", "--json"],
        )
        with pytest.raises(SystemExit) as exc:
            update_lookup.main()
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False

    def test_main_success_json(self, tmp_path, monkeypatch, fake_credentials, capsys):
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n")
        mock_client = type("Mock", (), {
            "update_lookup_file": lambda self, **kw: {
                "status_code": 200,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(update_lookup, "get_ngsiem_client", lambda: mock_client)
        monkeypatch.setattr(
            sys, "argv",
            ["update_lookup.py", "--name", "remote.csv", "--file", str(f), "--json"],
        )
        update_lookup.main()
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["filename"] == "remote.csv"
