"""Tests for import_workflows.py — input expansion, name extraction,
duplicate checking, single-file import, and the main import flow."""

from unittest.mock import MagicMock

import pytest

import import_workflows


class TestExpandInputs:
    """Test path expansion into a flat, de-duplicated file list."""

    def test_plain_files_pass_through(self, tmp_path):
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("name: A\n")
        b.write_text("name: B\n")
        result = import_workflows.expand_inputs([str(a), str(b)])
        assert result == [str(a), str(b)]

    def test_directory_expands_to_yaml_files(self, tmp_path):
        (tmp_path / "one.yaml").write_text("name: One\n")
        (tmp_path / "two.yml").write_text("name: Two\n")
        (tmp_path / "ignore.txt").write_text("not yaml\n")
        result = import_workflows.expand_inputs([str(tmp_path)])
        basenames = sorted(__import__("os").path.basename(f) for f in result)
        assert basenames == ["one.yaml", "two.yml"]

    def test_deduplicates_preserving_order(self, tmp_path):
        a = tmp_path / "a.yaml"
        a.write_text("name: A\n")
        result = import_workflows.expand_inputs([str(a), str(a)])
        assert result == [str(a)]

    def test_empty_directory_warns_and_yields_nothing(self, tmp_path, capsys):
        result = import_workflows.expand_inputs([str(tmp_path)])
        assert result == []
        assert "No *.yaml/*.yml files found" in capsys.readouterr().err


class TestExtractNameFromYaml:
    """Test YAML name extraction (import_workflows has its own copy)."""

    def test_extracts_name(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("# Header\nname: Import Test\ntrigger:\n  type: On demand\n")
        assert import_workflows.extract_name_from_yaml(str(f)) == "Import Test"

    def test_extracts_quoted_name(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("name: 'Quoted Name'\n")
        assert import_workflows.extract_name_from_yaml(str(f)) == "Quoted Name"

    def test_returns_none_without_name(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("# Header\ntrigger:\n  type: On demand\n")
        assert import_workflows.extract_name_from_yaml(str(f)) is None


class TestCheckDuplicate:
    """Test duplicate name checking."""

    def test_finds_duplicate(self):
        existing = {"my workflow": {"id": "abc123", "name": "My Workflow"}}
        assert import_workflows.check_duplicate("My Workflow", existing) == "abc123"

    def test_case_insensitive(self):
        existing = {"my workflow": {"id": "abc123", "name": "My Workflow"}}
        assert import_workflows.check_duplicate("MY WORKFLOW", existing) == "abc123"

    def test_no_duplicate(self):
        existing = {"other workflow": {"id": "xyz", "name": "Other Workflow"}}
        assert import_workflows.check_duplicate("New Workflow", existing) is None


class TestImportFile:
    """Test single-file import against a mocked FalconPy client."""

    def test_success_returns_id(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "status_code": 200,
            "body": {"resources": [{"id": "def_9999"}], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        ok, msg, wf_id = import_workflows.import_file("wf.yaml")
        assert ok is True
        assert msg == "OK"
        assert wf_id == "def_9999"

    def test_api_errors_reported(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "status_code": 400,
            "body": {"resources": [], "errors": [{"message": "bad definition"}]},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        ok, msg, wf_id = import_workflows.import_file("wf.yaml")
        assert ok is False
        assert msg == "bad definition"
        assert wf_id is None

    def test_empty_resources_is_failure(self, monkeypatch):
        # A 200 with no resources means nothing was created — must be a failure,
        # not a silent success with a null ID.
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "status_code": 200,
            "body": {"resources": [], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        ok, msg, wf_id = import_workflows.import_file("wf.yaml")
        assert ok is False
        assert wf_id is None
        assert "no definition ID" in msg

    def test_server_error_status_is_failure(self, monkeypatch):
        # A 500 with no explicit errors array must still be treated as a failure.
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "status_code": 500,
            "body": {"resources": [], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        ok, msg, wf_id = import_workflows.import_file("wf.yaml")
        assert ok is False
        assert wf_id is None
        assert "500" in msg

    def test_exception_is_caught(self, monkeypatch):
        def boom():
            raise ConnectionError("network down")

        monkeypatch.setattr(import_workflows, "get_client", boom)
        ok, msg, wf_id = import_workflows.import_file("wf.yaml")
        assert ok is False
        assert "network down" in msg
        assert wf_id is None


class TestImportSingleFile:
    """Test the per-file orchestration helper."""

    def test_duplicate_is_skipped(self, tmp_path, monkeypatch):
        f = tmp_path / "wf.yaml"
        f.write_text("name: My Workflow\n")
        existing = {"my workflow": {"id": "abc123", "name": "My Workflow"}}
        # get_client should never be called for a duplicate.
        monkeypatch.setattr(import_workflows, "get_client", lambda: (_ for _ in ()).throw(AssertionError))
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), existing, skip_validate=True
        )
        assert status == "DUPLICATE"
        assert wf_id is None

    def test_imported_when_not_duplicate(self, tmp_path, monkeypatch):
        f = tmp_path / "wf.yaml"
        f.write_text("name: Fresh Workflow\n")
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "body": {"resources": [{"id": "new_1"}], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), {}, skip_validate=True
        )
        assert status == "IMPORTED"
        assert wf_id == "new_1"

    def test_empty_resources_does_not_report_imported(self, tmp_path, monkeypatch):
        # Regression: a 200 with no resources must NOT be reported as IMPORTED
        # with a null ID (the original false-positive bug).
        f = tmp_path / "wf.yaml"
        f.write_text("name: Ghost Workflow\n")
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "status_code": 200,
            "body": {"resources": [], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), {}, skip_validate=True
        )
        assert status == "IMPORT FAILED"
        assert wf_id is None

    def test_server_error_prints_do_not_retry_guidance(self, tmp_path, monkeypatch, capsys):
        # A 500 must surface an explicit "server-side, do not retry" message so
        # the caller stops instead of looping.
        f = tmp_path / "wf.yaml"
        f.write_text("name: Doomed Workflow\n")
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "status_code": 500,
            "body": {"resources": [], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), {}, skip_validate=True
        )
        assert status == "IMPORT FAILED"
        out = capsys.readouterr().out
        assert "server-side" in out
        assert "not retry" in out.lower() or "do not retry" in out.lower()

    def test_validation_failure_skips_import(self, tmp_path, monkeypatch):
        # When validation fails, the API import must never be attempted.
        f = tmp_path / "wf.yaml"
        f.write_text("name: Invalid Workflow\n")
        monkeypatch.setattr(
            import_workflows, "validate_file",
            lambda _fp: (False, ["structural error: missing trigger"])
        )
        monkeypatch.setattr(
            import_workflows, "get_client",
            lambda: (_ for _ in ()).throw(AssertionError("import must not run"))
        )
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), {}, skip_validate=False
        )
        assert status == "VALIDATION FAILED"
        assert wf_id is None

    def test_replace_deletes_then_imports(self, tmp_path, monkeypatch):
        """--replace on an existing name: delete the old definition, then import
        the corrected YAML under the same name (iterate in place)."""
        f = tmp_path / "wf.yaml"
        f.write_text("name: My Workflow\n")
        existing = {"my workflow": {"id": "old_id", "name": "My Workflow"}}
        mock_client = MagicMock()
        mock_client.delete_definitions.return_value = {"body": {"resources": ["old_id"], "errors": []}}
        mock_client.import_definition.return_value = {"body": {"resources": [{"id": "new_id"}], "errors": []}}
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), existing, skip_validate=True, replace=True
        )
        assert status == "IMPORTED"
        assert wf_id == "new_id"
        assert mock_client.delete_definitions.call_args.kwargs["ids"] == ["old_id"]

    def test_replace_aborts_if_delete_fails(self, tmp_path, monkeypatch):
        """If the delete step fails, do not import (would create a duplicate)."""
        f = tmp_path / "wf.yaml"
        f.write_text("name: My Workflow\n")
        existing = {"my workflow": {"id": "old_id", "name": "My Workflow"}}
        mock_client = MagicMock()
        mock_client.delete_definitions.return_value = {"body": {"resources": [], "errors": [{"message": "nope"}]}}
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), existing, skip_validate=True, replace=True
        )
        assert status == "REPLACE FAILED"
        assert wf_id is None
        mock_client.import_definition.assert_not_called()

    def test_no_replace_still_skips_duplicate(self, tmp_path, monkeypatch):
        """Without --replace, an existing name is skipped as before (no delete)."""
        f = tmp_path / "wf.yaml"
        f.write_text("name: My Workflow\n")
        existing = {"my workflow": {"id": "old_id", "name": "My Workflow"}}
        monkeypatch.setattr(import_workflows, "get_client", lambda: (_ for _ in ()).throw(AssertionError))
        basename, status, wf_id = import_workflows._import_single_file(
            str(f), existing, skip_validate=True, replace=False
        )
        assert status == "DUPLICATE"


class TestMain:
    """Test the CLI main flow end-to-end with mocks."""

    def test_no_files_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["import_workflows.py", "/nope/*.yaml"])
        # expand_inputs will pass through the literal path (not a dir), so patch it empty.
        monkeypatch.setattr(import_workflows, "expand_inputs", lambda paths: [])
        with pytest.raises(SystemExit) as exc:
            import_workflows.main()
        assert exc.value.code == 1

    def test_successful_import_flow(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "wf.yaml"
        f.write_text("name: Flow WF\n")
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "body": {"resources": [{"id": "flow_1"}], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        monkeypatch.setattr(import_workflows, "fetch_all_definitions", lambda: [])
        monkeypatch.setattr(
            "sys.argv",
            ["import_workflows.py", "--skip-validate", str(f)],
        )
        # No duplicates and no failures -> no SystemExit raised.
        import_workflows.main()
        out = capsys.readouterr().out
        assert "flow_1" in out
        assert "Imported" in out

    def test_skip_validate_warns(self, tmp_path, monkeypatch, capsys):
        # --skip-validate must print a loud warning so it is not used casually
        # to bypass validation (the cause of opaque API 500s).
        f = tmp_path / "wf.yaml"
        f.write_text("name: Warn WF\n")
        mock_client = MagicMock()
        mock_client.import_definition.return_value = {
            "body": {"resources": [{"id": "warn_1"}], "errors": []},
        }
        monkeypatch.setattr(import_workflows, "get_client", lambda: mock_client)
        monkeypatch.setattr(import_workflows, "fetch_all_definitions", lambda: [])
        monkeypatch.setattr(
            "sys.argv", ["import_workflows.py", "--skip-validate", str(f)]
        )
        import_workflows.main()
        err = capsys.readouterr().err
        assert "--skip-validate" in err
        assert "500" in err

    def test_duplicate_causes_exit_1(self, tmp_path, monkeypatch):
        f = tmp_path / "wf.yaml"
        f.write_text("name: Dup WF\n")
        monkeypatch.setattr(
            import_workflows,
            "fetch_all_definitions",
            lambda: [{"name": "Dup WF", "id": "existing_1"}],
        )
        monkeypatch.setattr(
            "sys.argv",
            ["import_workflows.py", "--skip-validate", str(f)],
        )
        with pytest.raises(SystemExit) as exc:
            import_workflows.main()
        assert exc.value.code == 1
