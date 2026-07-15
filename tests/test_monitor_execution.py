"""Tests for monitor_execution.py — polling until a terminal state."""

import json
from unittest.mock import MagicMock

import pytest

import monitor_execution
import get_execution_results


class TestMonitor:
    """Test the polling loop (time.sleep mocked for speed)."""

    def test_monitor_returns_terminal_result(self, monkeypatch):
        """A terminal Succeeded status ends the loop and returns the record."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [{"status": "Succeeded", "output": {"k": "v"}}], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(monitor_execution.time, "sleep", lambda _s: None)
        result = monitor_execution.monitor("exec_123", interval=0.1, timeout=5)
        assert result is not None
        assert result["status"] == "Succeeded"

    def test_monitor_in_progress_then_complete(self, monkeypatch):
        """The loop keeps polling while non-terminal, then returns on completion."""
        statuses = ["Running", "Running", "Succeeded"]
        calls = {"n": 0}

        def mock_execution_results(**_kwargs):
            idx = min(calls["n"], len(statuses) - 1)
            calls["n"] += 1
            return {
                "status_code": 200,
                "body": {"resources": [{"status": statuses[idx]}], "errors": []},
                "headers": {},
            }

        mock_client = MagicMock()
        mock_client.execution_results = mock_execution_results
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(monitor_execution.time, "sleep", lambda _s: None)
        result = monitor_execution.monitor("exec_123", interval=0.01, timeout=5)
        assert result["status"] == "Succeeded"
        assert calls["n"] >= 3

    def test_monitor_timeout_returns_none(self, monkeypatch):
        """A never-terminal status eventually times out and returns None."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [{"status": "Running"}], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(monitor_execution.time, "sleep", lambda _s: None)
        result = monitor_execution.monitor("exec_123", interval=0.1, timeout=1)
        assert result is None

    def test_monitor_transient_error_does_not_abort(self, monkeypatch):
        """A poll error is reported but the loop continues to a terminal state."""
        responses = [
            {"status_code": 500, "body": {"resources": [], "errors": [{"message": "flaky"}]}, "headers": {}},
            {"status_code": 200, "body": {"resources": [{"status": "Succeeded"}], "errors": []}, "headers": {}},
        ]
        calls = {"n": 0}

        def mock_execution_results(**_kwargs):
            resp = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return resp

        mock_client = MagicMock()
        mock_client.execution_results = mock_execution_results
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(monitor_execution.time, "sleep", lambda _s: None)
        result = monitor_execution.monitor("exec_123", interval=0.01, timeout=5)
        assert result["status"] == "Succeeded"
        assert calls["n"] >= 2

    def test_monitor_status_updates_go_to_stderr(self, monkeypatch, capsys):
        """Status lines are written to stderr, keeping stdout clean."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [{"status": "Succeeded"}], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(monitor_execution.time, "sleep", lambda _s: None)
        monitor_execution.monitor("exec_123", interval=0.1, timeout=5)
        captured = capsys.readouterr()
        assert "Status: Succeeded" in captured.err
        assert captured.out == ""


class TestMain:
    """Test the CLI entry point and exit codes."""

    def test_main_succeeded_exits_0(self, monkeypatch, capsys):
        """A Succeeded terminal state exits 0 and prints output."""
        monkeypatch.setattr(
            "sys.argv",
            ["monitor_execution.py", "--execution-id", "exec_123"],
        )
        monkeypatch.setattr(
            monitor_execution,
            "monitor",
            lambda *_a, **_k: {"status": "Succeeded", "output": {"done": True}},
        )
        with pytest.raises(SystemExit) as exc:
            monitor_execution.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Succeeded" in out
        assert "done" in out

    def test_main_failed_exits_1(self, monkeypatch):
        """A non-succeeded terminal state exits non-zero for CI."""
        monkeypatch.setattr(
            "sys.argv",
            ["monitor_execution.py", "--execution-id", "exec_123"],
        )
        monkeypatch.setattr(
            monitor_execution,
            "monitor",
            lambda *_a, **_k: {"status": "Failed"},
        )
        with pytest.raises(SystemExit) as exc:
            monitor_execution.main()
        assert exc.value.code == 1

    def test_main_timeout_exits_1(self, monkeypatch):
        """A None result (timeout) exits non-zero."""
        monkeypatch.setattr(
            "sys.argv",
            ["monitor_execution.py", "--execution-id", "exec_123"],
        )
        monkeypatch.setattr(monitor_execution, "monitor", lambda *_a, **_k: None)
        with pytest.raises(SystemExit) as exc:
            monitor_execution.main()
        assert exc.value.code == 1

    def test_main_json_output(self, monkeypatch, capsys):
        """--json prints the record as parseable JSON on stdout."""
        monkeypatch.setattr(
            "sys.argv",
            ["monitor_execution.py", "--execution-id", "exec_123", "--json"],
        )
        monkeypatch.setattr(
            monitor_execution,
            "monitor",
            lambda *_a, **_k: {"status": "Succeeded", "output": {"a": 1}},
        )
        with pytest.raises(SystemExit) as exc:
            monitor_execution.main()
        assert exc.value.code == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "Succeeded"

    def test_main_json_timeout_output(self, monkeypatch, capsys):
        """--json on timeout emits a status:timeout object and exits 1."""
        monkeypatch.setattr(
            "sys.argv",
            ["monitor_execution.py", "--execution-id", "exec_123", "--json"],
        )
        monkeypatch.setattr(monitor_execution, "monitor", lambda *_a, **_k: None)
        with pytest.raises(SystemExit) as exc:
            monitor_execution.main()
        assert exc.value.code == 1
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["status"] == "timeout"

    def test_main_requires_execution_id(self, monkeypatch):
        """argparse enforces the required --execution-id argument."""
        monkeypatch.setattr("sys.argv", ["monitor_execution.py"])
        with pytest.raises(SystemExit):
            monitor_execution.main()
