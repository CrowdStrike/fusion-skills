"""Tests for get_execution_results.py — single-fetch result retrieval."""

import json
from unittest.mock import MagicMock

import pytest

import get_execution_results


class TestFetchResults:
    """Test the single-fetch envelope parsing."""

    def test_fetch_success_returns_resource(self, monkeypatch):
        """A populated resources list yields (True, 'OK', record)."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {
                "resources": [{"status": "Succeeded", "output": {"k": "v"}}],
                "errors": [],
            },
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        ok, msg, result = get_execution_results.fetch_results("exec_123")
        assert ok is True
        assert msg == "OK"
        assert result["status"] == "Succeeded"

    def test_fetch_errors_returned(self, monkeypatch):
        """API errors become (False, message, None)."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 404,
            "body": {"resources": [], "errors": [{"message": "not found"}]},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        ok, msg, result = get_execution_results.fetch_results("exec_123")
        assert ok is False
        assert "not found" in msg
        assert result is None

    def test_fetch_empty_resources(self, monkeypatch):
        """No record for the ID is reported as a failure."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        ok, msg, result = get_execution_results.fetch_results("exec_123")
        assert ok is False
        assert "No execution record" in msg
        assert result is None

    def test_fetch_handles_exception(self, monkeypatch):
        """Network/runtime errors are caught and returned as failure."""
        mock_client = MagicMock()
        mock_client.execution_results.side_effect = RuntimeError("timeout")
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        ok, msg, result = get_execution_results.fetch_results("exec_123")
        assert ok is False
        assert "timeout" in msg
        assert result is None

    def test_fetch_passes_execution_id(self, monkeypatch):
        """The execution ID is forwarded to the API as the ids argument."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [{"status": "Running"}], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        get_execution_results.fetch_results("exec_abc")
        mock_client.execution_results.assert_called_once_with(ids="exec_abc")


class TestTerminalStatuses:
    """Guard the terminal-status set used by both poller scripts."""

    def test_terminal_statuses_lowercase(self):
        """All entries are lowercase so case-insensitive matching works."""
        for status in get_execution_results.TERMINAL_STATUSES:
            assert status == status.lower()

    def test_expected_terminal_statuses_present(self):
        """The documented terminal states are covered."""
        assert {"succeeded", "failed", "canceled"} <= get_execution_results.TERMINAL_STATUSES


class TestMain:
    """Test the CLI entry point and exit codes."""

    def test_main_prints_status(self, monkeypatch, capsys):
        """A successful fetch prints the status and output."""
        monkeypatch.setattr(
            "sys.argv",
            ["get_execution_results.py", "--execution-id", "exec_123"],
        )
        monkeypatch.setattr(
            get_execution_results,
            "fetch_results",
            lambda _id: (True, "OK", {"status": "Succeeded", "output": {"a": 1}}),
        )
        get_execution_results.main()
        out = capsys.readouterr().out
        assert "Succeeded" in out
        assert "exec_123" in out

    def test_main_json_output(self, monkeypatch, capsys):
        """--json prints the raw record as parseable JSON."""
        monkeypatch.setattr(
            "sys.argv",
            ["get_execution_results.py", "--execution-id", "exec_123", "--json"],
        )
        monkeypatch.setattr(
            get_execution_results,
            "fetch_results",
            lambda _id: (True, "OK", {"status": "Succeeded", "output": {"a": 1}}),
        )
        get_execution_results.main()
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["status"] == "Succeeded"

    def test_main_failure_exits_1(self, monkeypatch, capsys):
        """A fetch failure exits non-zero and reports to stderr."""
        monkeypatch.setattr(
            "sys.argv",
            ["get_execution_results.py", "--execution-id", "exec_123"],
        )
        monkeypatch.setattr(
            get_execution_results,
            "fetch_results",
            lambda _id: (False, "not found", None),
        )
        with pytest.raises(SystemExit) as exc:
            get_execution_results.main()
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_main_failure_json_exits_1(self, monkeypatch, capsys):
        """A fetch failure with --json emits an error object on stdout."""
        monkeypatch.setattr(
            "sys.argv",
            ["get_execution_results.py", "--execution-id", "exec_123", "--json"],
        )
        monkeypatch.setattr(
            get_execution_results,
            "fetch_results",
            lambda _id: (False, "boom", None),
        )
        with pytest.raises(SystemExit) as exc:
            get_execution_results.main()
        assert exc.value.code == 1
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["error"] == "boom"

    def test_main_requires_execution_id(self, monkeypatch):
        """argparse enforces the required --execution-id argument."""
        monkeypatch.setattr("sys.argv", ["get_execution_results.py"])
        with pytest.raises(SystemExit):
            get_execution_results.main()
