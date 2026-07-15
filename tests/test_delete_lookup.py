"""Tests for delete_lookup.py."""

import json
import sys

import pytest

import delete_lookup


class TestDeleteLookup:
    """Test delete API call handling."""

    def test_successful_delete(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "delete_lookup_file": lambda self, **kw: {
                "status_code": 200,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(delete_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = delete_lookup.delete_lookup("test.csv")
        assert success
        assert "deleted" in msg.lower()

    def test_successful_delete_204(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "delete_lookup_file": lambda self, **kw: {
                "status_code": 204,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(delete_lookup, "get_ngsiem_client", lambda: mock_client)
        success, _msg = delete_lookup.delete_lookup("test.csv")
        assert success

    def test_failed_delete(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "delete_lookup_file": lambda self, **kw: {
                "status_code": 404,
                "body": {"errors": [{"message": "File not found"}]},
            }
        })()
        monkeypatch.setattr(delete_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = delete_lookup.delete_lookup("missing.csv")
        assert not success
        assert "not found" in msg.lower()

    def test_bad_status_without_errors(self, monkeypatch, fake_credentials):
        """A non-success status with no errors array still reports failure."""
        mock_client = type("Mock", (), {
            "delete_lookup_file": lambda self, **kw: {
                "status_code": 500,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(delete_lookup, "get_ngsiem_client", lambda: mock_client)
        success, msg = delete_lookup.delete_lookup("test.csv")
        assert not success
        assert "500" in msg


class TestMain:
    """Test the CLI entry point and confirmation logic."""

    def test_json_without_confirm_exits(self, monkeypatch, capsys):
        """--json mode requires --confirm to prevent silent deletes."""
        monkeypatch.setattr(sys, "argv", ["delete_lookup.py", "--name", "test.csv", "--json"])
        with pytest.raises(SystemExit) as exc:
            delete_lookup.main()
        assert exc.value.code == 1
        output = json.loads(capsys.readouterr().out)
        assert output["success"] is False
        assert "--confirm" in output["error"]

    def test_interactive_cancel(self, monkeypatch, capsys):
        """Answering 'n' to the prompt cancels with exit code 0."""
        monkeypatch.setattr(sys, "argv", ["delete_lookup.py", "--name", "test.csv"])
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        with pytest.raises(SystemExit) as exc:
            delete_lookup.main()
        assert exc.value.code == 0
        assert "cancelled" in capsys.readouterr().out.lower()

    def test_confirm_json_success(self, monkeypatch, fake_credentials, capsys):
        mock_client = type("Mock", (), {
            "delete_lookup_file": lambda self, **kw: {
                "status_code": 200,
                "body": {"errors": []},
            }
        })()
        monkeypatch.setattr(delete_lookup, "get_ngsiem_client", lambda: mock_client)
        monkeypatch.setattr(
            sys, "argv",
            ["delete_lookup.py", "--name", "test.csv", "--confirm", "--json"],
        )
        delete_lookup.main()
        output = json.loads(capsys.readouterr().out)
        assert output["success"] is True
        assert output["filename"] == "test.csv"
