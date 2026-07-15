"""Tests for trigger_workflow.py — parameter handling, execution, and polling."""

import inspect
import json
from unittest.mock import MagicMock

import pytest

import trigger_workflow
import get_execution_results


class TestExecuteWorkflow:
    """Test workflow execution request building and response parsing."""

    def test_execute_workflow_signature(self):
        """The execute_workflow function exposes the expected parameters."""
        sig = inspect.signature(trigger_workflow.execute_workflow)
        params = list(sig.parameters.keys())
        assert "definition_id" in params
        assert "params" in params
        assert "depth" in params

    def test_execution_id_parsed_from_bare_string(self, monkeypatch):
        """The execute endpoint returns resources as bare ID strings, not dicts."""
        mock_client = MagicMock()
        mock_client.execute.return_value = {
            "status_code": 200,
            "body": {"resources": ["6c0b22c26ec8358b5df2098ddad0e304"], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        ok, exec_id, _ = trigger_workflow.execute_workflow("def_id", {})
        assert ok is True
        assert exec_id == "6c0b22c26ec8358b5df2098ddad0e304"

    def test_execution_id_parsed_from_dict(self, monkeypatch):
        """Still handle the object shape defensively."""
        mock_client = MagicMock()
        mock_client.execute.return_value = {
            "status_code": 200,
            "body": {"resources": [{"id": "abc123"}], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        ok, exec_id, _ = trigger_workflow.execute_workflow("def_id", {})
        assert ok is True
        assert exec_id == "abc123"

    def test_execute_workflow_errors_returned(self, monkeypatch):
        """API errors are surfaced as (False, None, message)."""
        mock_client = MagicMock()
        mock_client.execute.return_value = {
            "status_code": 400,
            "body": {"resources": [], "errors": [{"message": "bad definition id"}]},
            "headers": {},
        }
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        ok, exec_id, msg = trigger_workflow.execute_workflow("def_id", {})
        assert ok is False
        assert exec_id is None
        assert "bad definition id" in msg

    def test_execute_workflow_empty_resources(self, monkeypatch):
        """No resources means success but no execution ID."""
        mock_client = MagicMock()
        mock_client.execute.return_value = {
            "status_code": 200,
            "body": {"resources": [], "errors": []},
            "headers": {},
        }
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        ok, exec_id, _ = trigger_workflow.execute_workflow("def_id", {})
        assert ok is True
        assert exec_id is None

    def test_execute_workflow_handles_exception(self, monkeypatch):
        """Network/runtime errors are caught and returned as failure."""
        mock_client = MagicMock()
        mock_client.execute.side_effect = ConnectionError("network down")
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        ok, exec_id, msg = trigger_workflow.execute_workflow("def_id", {})
        assert ok is False
        assert exec_id is None
        assert "network down" in msg


class TestGetWorkflowParamsSchema:
    """Test schema retrieval used for interactive parameter prompts."""

    def test_schema_extracted_from_definition(self, monkeypatch):
        """The parameter properties are pulled from the trigger schema."""
        mock_client = MagicMock()
        mock_client.search_definitions.return_value = {
            "body": {
                "resources": [
                    {
                        "trigger": {
                            "parameters": {
                                "properties": {"device_id": {"type": "string"}}
                            }
                        }
                    }
                ]
            }
        }
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        schema = trigger_workflow.get_workflow_params_schema("def_id")
        assert schema == {"device_id": {"type": "string"}}

    def test_schema_none_when_no_resources(self, monkeypatch):
        """A definition ID with no matches returns None."""
        mock_client = MagicMock()
        mock_client.search_definitions.return_value = {"body": {"resources": []}}
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        assert trigger_workflow.get_workflow_params_schema("def_id") is None

    def test_schema_none_on_exception(self, monkeypatch):
        """Errors while fetching the schema degrade to None."""
        mock_client = MagicMock()
        mock_client.search_definitions.side_effect = RuntimeError("boom")
        monkeypatch.setattr(trigger_workflow, "get_client", lambda: mock_client)
        assert trigger_workflow.get_workflow_params_schema("def_id") is None


class TestPromptForParams:
    """Test interactive parameter prompting and type coercion."""

    def test_integer_coercion(self, monkeypatch):
        """Integer fields are cast from the entered string."""
        schema = {"count": {"type": "integer"}}
        monkeypatch.setattr("builtins.input", lambda _: "42")
        params = trigger_workflow.prompt_for_params(schema)
        assert params == {"count": 42}

    def test_blank_optional_field_skipped(self, monkeypatch):
        """Leaving a field blank omits it from the params dict."""
        schema = {"device_id": {"type": "string"}}
        monkeypatch.setattr("builtins.input", lambda _: "")
        params = trigger_workflow.prompt_for_params(schema)
        assert params == {}

    def test_no_schema_parses_manual_json(self, monkeypatch):
        """With no schema the user enters raw JSON."""
        monkeypatch.setattr("builtins.input", lambda _: '{"a": 1}')
        params = trigger_workflow.prompt_for_params(None)
        assert params == {"a": 1}

    def test_boolean_coercion(self, monkeypatch):
        """Boolean fields are truthy for true/1/yes, falsy otherwise."""
        schema = {"flag": {"type": "boolean"}}
        monkeypatch.setattr("builtins.input", lambda _: "yes")
        assert trigger_workflow.prompt_for_params(schema) == {"flag": True}
        monkeypatch.setattr("builtins.input", lambda _: "nope")
        assert trigger_workflow.prompt_for_params(schema) == {"flag": False}

    def test_array_coercion_from_json(self, monkeypatch):
        """An array field entered as JSON is parsed as a list."""
        schema = {"ids": {"type": "array"}}
        monkeypatch.setattr("builtins.input", lambda _: '["a", "b"]')
        assert trigger_workflow.prompt_for_params(schema) == {"ids": ["a", "b"]}

    def test_array_coercion_csv_fallback(self, monkeypatch):
        """A non-JSON array entry falls back to comma-separated splitting."""
        schema = {"ids": {"type": "array"}}
        monkeypatch.setattr("builtins.input", lambda _: "a, b ,c")
        assert trigger_workflow.prompt_for_params(schema) == {"ids": ["a", "b", "c"]}

    def test_object_coercion(self, monkeypatch):
        """An object field is parsed from JSON."""
        schema = {"cfg": {"type": "object"}}
        monkeypatch.setattr("builtins.input", lambda _: '{"k": 1}')
        assert trigger_workflow.prompt_for_params(schema) == {"cfg": {"k": 1}}


class TestPollResults:
    """Test result polling logic (time.sleep mocked for speed)."""

    def test_poll_timeout_returns_none(self, monkeypatch):
        """Verify timeout behavior without hitting real API."""
        call_count = 0
        mock_client = MagicMock()

        def mock_execution_results(**_kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "status_code": 200,
                "body": {"resources": [{"status": "In progress"}]},
                "headers": {},
            }

        mock_client.execution_results = mock_execution_results
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(trigger_workflow.time, "sleep", lambda _s: None)
        result = trigger_workflow.poll_results("fake_id", timeout=1, interval=0.1)
        assert result is None
        assert call_count > 0

    def test_poll_succeeded_returns_result(self, monkeypatch):
        """Verify a terminal Succeeded status is returned (real API casing)."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [{"status": "Succeeded", "output": {"key": "value"}}]},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(trigger_workflow.time, "sleep", lambda _s: None)
        result = trigger_workflow.poll_results("fake_id", timeout=5, interval=0.1)
        assert result is not None
        assert result["status"] == "Succeeded"
        assert result["output"] == {"key": "value"}

    def test_poll_failed_returns_result(self, monkeypatch):
        """Verify capitalized Failed status is terminal (not retried forever)."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 200,
            "body": {"resources": [{"status": "Failed"}]},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(trigger_workflow.time, "sleep", lambda _s: None)
        result = trigger_workflow.poll_results("fake_id", timeout=5, interval=0.1)
        assert result["status"] == "Failed"

    def test_poll_error_branch_then_timeout(self, monkeypatch, capsys):
        """A non-200 from the results API hits the 'Poll error' branch and times out."""
        mock_client = MagicMock()
        mock_client.execution_results.return_value = {
            "status_code": 500,
            "body": {"errors": [{"message": "boom"}]},
            "headers": {},
        }
        monkeypatch.setattr(get_execution_results, "get_client", lambda: mock_client)
        monkeypatch.setattr(trigger_workflow.time, "sleep", lambda _s: None)
        result = trigger_workflow.poll_results("fake_id", timeout=1, interval=0.1)
        assert result is None
        assert "Poll error" in capsys.readouterr().out


class TestMain:
    """Test the CLI entry point wiring and exit codes."""

    def test_main_submitted_without_wait(self, monkeypatch, capsys):
        """A successful execute without --wait prints the execution ID and returns."""
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_workflow.py", "--id", "def_id", "--params", "{}"],
        )
        monkeypatch.setattr(
            trigger_workflow,
            "execute_workflow",
            lambda *_a, **_k: (True, "exec_123", {"resources": ["exec_123"]}),
        )
        trigger_workflow.main()
        out = capsys.readouterr().out
        assert "exec_123" in out
        assert "--wait to poll" in out

    def test_main_execution_failed_exits_1(self, monkeypatch):
        """A failed execution exits non-zero."""
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_workflow.py", "--id", "def_id", "--params", "{}"],
        )
        monkeypatch.setattr(
            trigger_workflow,
            "execute_workflow",
            lambda *_a, **_k: (False, None, "definition not found"),
        )
        with pytest.raises(SystemExit) as exc:
            trigger_workflow.main()
        assert exc.value.code == 1

    def test_main_wait_prints_output(self, monkeypatch, capsys):
        """With --wait, poll_results output is rendered."""
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_workflow.py", "--id", "def_id", "--params", "{}", "--wait"],
        )
        monkeypatch.setattr(
            trigger_workflow,
            "execute_workflow",
            lambda *_a, **_k: (True, "exec_123", {}),
        )
        monkeypatch.setattr(
            trigger_workflow,
            "poll_results",
            lambda *_a, **_k: {"status": "Succeeded", "output": {"done": True}},
        )
        trigger_workflow.main()
        out = capsys.readouterr().out
        assert "Succeeded" in out
        assert "done" in out

    def test_main_interactive_mode_no_params(self, monkeypatch, capsys):
        """Without --params, main() fetches the schema and prompts for params."""
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_workflow.py", "--id", "def_id"],
        )
        monkeypatch.setattr(trigger_workflow, "get_workflow_params_schema",
                            lambda *_a, **_k: {"device_id": {"type": "string"}})
        monkeypatch.setattr(trigger_workflow, "prompt_for_params",
                            lambda *_a, **_k: {"device_id": "abc"})
        monkeypatch.setattr(trigger_workflow, "execute_workflow",
                            lambda *_a, **_k: (True, "exec_9", {}))
        trigger_workflow.main()
        out = capsys.readouterr().out
        assert "device_id" in out
        assert "exec_9" in out

    def test_main_wait_timeout_exits_1(self, monkeypatch):
        """With --wait, no results within timeout exits non-zero."""
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_workflow.py", "--id", "def_id", "--params", "{}", "--wait"],
        )
        monkeypatch.setattr(trigger_workflow, "execute_workflow",
                            lambda *_a, **_k: (True, "exec_123", {}))
        monkeypatch.setattr(trigger_workflow, "poll_results", lambda *_a, **_k: None)
        with pytest.raises(SystemExit) as exc:
            trigger_workflow.main()
        assert exc.value.code == 1

    def test_main_json_output(self, monkeypatch, capsys):
        """--json without --wait prints the raw response body as JSON."""
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_workflow.py", "--id", "def_id", "--params", "{}", "--json"],
        )
        monkeypatch.setattr(
            trigger_workflow,
            "execute_workflow",
            lambda *_a, **_k: (True, "exec_123", {"resources": ["exec_123"]}),
        )
        trigger_workflow.main()
        out = capsys.readouterr().out
        # The trailing JSON block (after the "Execution ID" line) must parse.
        json_block = out[out.rindex("Execution ID:"):]
        json_block = json_block[json_block.index("{"):]
        parsed = json.loads(json_block)
        assert parsed["resources"] == ["exec_123"]

    def test_main_requires_id(self, monkeypatch):
        """argparse enforces the required --id argument."""
        monkeypatch.setattr("sys.argv", ["trigger_workflow.py"])
        with pytest.raises(SystemExit):
            trigger_workflow.main()
