"""Tests for release_workflow.py — enabling a workflow definition by ID,
API response handling, output formats, and exit codes."""

import json
from unittest.mock import MagicMock

import pytest

import release_workflow


class TestReleaseWorkflow:
    """Test the release_workflow() core against a mocked FalconPy client."""

    def test_success_returns_resources(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.workflow_definition_action.return_value = {
            "status_code": 200,
            "body": {"resources": [{"id": "def_1"}], "errors": []},
        }
        monkeypatch.setattr(release_workflow, "get_client", lambda: mock_client)
        ok, msg, resources = release_workflow.release_workflow("def_1")
        assert ok is True
        assert msg == "OK"
        assert resources == [{"id": "def_1"}]

    def test_calls_enable_action_with_id(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.workflow_definition_action.return_value = {
            "body": {"resources": [], "errors": []},
        }
        monkeypatch.setattr(release_workflow, "get_client", lambda: mock_client)
        release_workflow.release_workflow("abc999")
        _, kwargs = mock_client.workflow_definition_action.call_args
        assert kwargs["action_name"] == "enable"
        assert kwargs["body"] == {"ids": ["abc999"]}

    def test_api_errors_reported(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.workflow_definition_action.return_value = {
            "status_code": 400,
            "body": {"resources": [], "errors": [{"message": "not found"}]},
        }
        monkeypatch.setattr(release_workflow, "get_client", lambda: mock_client)
        ok, msg, resources = release_workflow.release_workflow("missing")
        assert ok is False
        assert msg == "not found"
        assert resources is None

    def test_empty_success_body(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.workflow_definition_action.return_value = {
            "body": {"resources": [], "errors": []},
        }
        monkeypatch.setattr(release_workflow, "get_client", lambda: mock_client)
        ok, msg, resources = release_workflow.release_workflow("def_1")
        assert ok is True
        assert resources == []

    def test_exception_is_caught(self, monkeypatch):
        def boom():
            raise RuntimeError("token expired")

        monkeypatch.setattr(release_workflow, "get_client", boom)
        ok, msg, resources = release_workflow.release_workflow("def_1")
        assert ok is False
        assert "token expired" in msg
        assert resources is None


class TestMain:
    """Test the CLI main flow, output formats, and exit codes."""

    def test_id_is_required(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["release_workflow.py"])
        with pytest.raises(SystemExit) as exc:
            release_workflow.main()
        assert exc.value.code == 2  # argparse error for missing --id

    def test_success_human_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            release_workflow, "release_workflow", lambda i: (True, "OK", [{"id": i}])
        )
        monkeypatch.setattr("sys.argv", ["release_workflow.py", "--id", "def_42"])
        release_workflow.main()  # no SystemExit on success
        out = capsys.readouterr().out
        assert "def_42" in out
        assert "enabled" in out

    def test_success_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            release_workflow, "release_workflow", lambda i: (True, "OK", [{"id": i}])
        )
        monkeypatch.setattr(
            "sys.argv", ["release_workflow.py", "--id", "def_42", "--json"]
        )
        with pytest.raises(SystemExit) as exc:
            release_workflow.main()
        assert exc.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["id"] == "def_42"
        assert payload["released"] is True
        assert payload["resources"] == [{"id": "def_42"}]

    def test_failure_human_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(
            release_workflow, "release_workflow", lambda i: (False, "boom", None)
        )
        monkeypatch.setattr("sys.argv", ["release_workflow.py", "--id", "bad"])
        with pytest.raises(SystemExit) as exc:
            release_workflow.main()
        assert exc.value.code == 1
        assert "RELEASE FAILED" in capsys.readouterr().err

    def test_failure_json_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(
            release_workflow, "release_workflow", lambda i: (False, "boom", None)
        )
        monkeypatch.setattr(
            "sys.argv", ["release_workflow.py", "--id", "bad", "--json"]
        )
        with pytest.raises(SystemExit) as exc:
            release_workflow.main()
        assert exc.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["released"] is False
        assert payload["resources"] == []
