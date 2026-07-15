"""Tests for query_workflows.py — pagination, search, name extraction,
formatting, and the check-name / check-yaml command handlers."""

import json
from unittest.mock import MagicMock

import pytest

import query_workflows


class TestFetchAllDefinitions:
    """Test paginated fetch against a mocked client."""

    def test_single_page(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search_definitions.return_value = {
            "body": {
                "resources": [{"id": "1", "name": "A"}],
                "meta": {"pagination": {"total": 1}},
            }
        }
        monkeypatch.setattr(query_workflows, "get_client", lambda: mock_client)
        defs = query_workflows.fetch_all_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "A"

    def test_multiple_pages(self, monkeypatch):
        mock_client = MagicMock()
        pages = [
            {"body": {"resources": [{"id": "1"}, {"id": "2"}], "meta": {"pagination": {"total": 3}}}},
            {"body": {"resources": [{"id": "3"}], "meta": {"pagination": {"total": 3}}}},
        ]
        mock_client.search_definitions.side_effect = pages
        monkeypatch.setattr(query_workflows, "get_client", lambda: mock_client)
        defs = query_workflows.fetch_all_definitions()
        assert [d["id"] for d in defs] == ["1", "2", "3"]

    def test_empty_result(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search_definitions.return_value = {"body": {"resources": []}}
        monkeypatch.setattr(query_workflows, "get_client", lambda: mock_client)
        assert query_workflows.fetch_all_definitions() == []


class TestSearchDefinitions:
    """Test substring search filtering."""

    def test_matches_substring_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(
            query_workflows,
            "fetch_all_definitions",
            lambda: [
                {"name": "Contain Host"},
                {"name": "Notify Team"},
                {"name": "CONTAINment Policy"},
            ],
        )
        results = query_workflows.search_definitions("contain")
        names = [d["name"] for d in results]
        assert "Contain Host" in names
        assert "CONTAINment Policy" in names
        assert "Notify Team" not in names


class TestFindByExactName:
    """Test exact-match lookup."""

    def test_exact_match_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(
            query_workflows,
            "fetch_all_definitions",
            lambda: [{"name": "My Workflow", "id": "x"}, {"name": "My Workflow 2"}],
        )
        results = query_workflows.find_by_exact_name("my workflow")
        assert len(results) == 1
        assert results[0]["id"] == "x"


class TestExtractNameFromYaml:
    """Test workflow name extraction from YAML files."""

    def test_simple_name(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("# Header\nname: My Workflow\ntrigger:\n  type: On demand\n")
        assert query_workflows.extract_name_from_yaml(str(f)) == "My Workflow"

    def test_quoted_name(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("name: 'Quoted Name'\n")
        assert query_workflows.extract_name_from_yaml(str(f)) == "Quoted Name"

    def test_double_quoted_name(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text('name: "Double Quoted"\n')
        assert query_workflows.extract_name_from_yaml(str(f)) == "Double Quoted"

    def test_no_name_key(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("# Header\ntrigger:\n  type: On demand\n")
        assert query_workflows.extract_name_from_yaml(str(f)) is None

    def test_indented_name_ignored(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("# Header\ntrigger:\n  name: Nested Name\n")
        assert query_workflows.extract_name_from_yaml(str(f)) is None

    def test_file_not_found(self):
        assert query_workflows.extract_name_from_yaml("/nonexistent") is None


class TestFormatDefinition:
    """Test human-readable definition formatting."""

    def test_formats_basic_definition(self):
        d = {
            "id": "abc123",
            "name": "Test Workflow",
            "enabled": True,
            "trigger": {"type": "On demand"},
            "last_modified_timestamp": "2026-01-01",
        }
        output = query_workflows.format_definition(d)
        assert "Test Workflow" in output
        assert "abc123" in output
        assert "enabled" in output
        assert "On demand" in output

    def test_formats_disabled_workflow(self):
        d = {"id": "x", "name": "Disabled", "enabled": False, "trigger": {"type": "API"}}
        output = query_workflows.format_definition(d)
        assert "disabled" in output


class TestFormatJson:
    """Test machine-readable JSON formatting."""

    def test_json_shape(self):
        defs = [
            {
                "id": "1",
                "name": "WF",
                "enabled": True,
                "trigger": {"type": "On demand"},
                "last_modified_timestamp": "2026-01-01",
            }
        ]
        out = json.loads(query_workflows.format_json(defs))
        assert out[0]["id"] == "1"
        assert out[0]["name"] == "WF"
        assert out[0]["enabled"] is True
        assert out[0]["trigger_type"] == "On demand"

    def test_empty_list(self):
        assert json.loads(query_workflows.format_json([])) == []


class TestHandleCheckName:
    """Test the --check-name command handler exit codes."""

    def test_exists_exits_0(self, monkeypatch, capsys):
        monkeypatch.setattr(
            query_workflows, "find_by_exact_name", lambda n: [{"id": "1", "name": "WF"}]
        )
        args = MagicMock(check_name="WF", json=False)
        with pytest.raises(SystemExit) as exc:
            query_workflows._handle_check_name(args)
        assert exc.value.code == 0
        assert "DUPLICATE FOUND" in capsys.readouterr().out

    def test_missing_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(query_workflows, "find_by_exact_name", lambda n: [])
        args = MagicMock(check_name="Nope", json=False)
        with pytest.raises(SystemExit) as exc:
            query_workflows._handle_check_name(args)
        assert exc.value.code == 1
        assert "OK" in capsys.readouterr().out

    def test_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            query_workflows, "find_by_exact_name", lambda n: [{"id": "1", "name": "WF"}]
        )
        args = MagicMock(check_name="WF", json=True)
        with pytest.raises(SystemExit):
            query_workflows._handle_check_name(args)
        payload = json.loads(capsys.readouterr().out)
        assert payload["exists"] is True
        assert payload["count"] == 1


class TestHandleCheckYaml:
    """Test the --check-yaml command handler exit codes."""

    def test_duplicate_exits_0(self, tmp_path, monkeypatch, capsys):
        # Mirrors --check-name semantics: a found duplicate exits 0.
        f = tmp_path / "wf.yaml"
        f.write_text("name: Existing WF\n")
        monkeypatch.setattr(
            query_workflows,
            "fetch_all_definitions",
            lambda: [{"name": "Existing WF", "id": "e1"}],
        )
        args = MagicMock(check_yaml=[str(f)], json=False)
        with pytest.raises(SystemExit) as exc:
            query_workflows._handle_check_yaml(args)
        assert exc.value.code == 0
        assert "DUPLICATES FOUND" in capsys.readouterr().out

    def test_clean_exits_1(self, tmp_path, monkeypatch, capsys):
        # No duplicates -> exit 1 (nothing to warn about).
        f = tmp_path / "wf.yaml"
        f.write_text("name: Brand New\n")
        monkeypatch.setattr(query_workflows, "fetch_all_definitions", lambda: [])
        args = MagicMock(check_yaml=[str(f)], json=False)
        with pytest.raises(SystemExit) as exc:
            query_workflows._handle_check_yaml(args)
        assert exc.value.code == 1
        assert "No duplicates" in capsys.readouterr().out


class TestMain:
    """Test the CLI main flow for list and search."""

    def test_list_json(self, monkeypatch, capsys):
        monkeypatch.setattr(
            query_workflows,
            "fetch_all_definitions",
            lambda: [
                {"id": "1", "name": "WF", "enabled": True, "trigger": {"type": "API"}}
            ],
        )
        monkeypatch.setattr("sys.argv", ["query_workflows.py", "--list", "--json"])
        query_workflows.main()
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["id"] == "1"

    def test_search_no_results(self, monkeypatch, capsys):
        monkeypatch.setattr(query_workflows, "fetch_all_definitions", lambda: [])
        monkeypatch.setattr("sys.argv", ["query_workflows.py", "--search", "xyz"])
        query_workflows.main()
        assert "No workflows found" in capsys.readouterr().out

    def test_requires_a_mode(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["query_workflows.py"])
        with pytest.raises(SystemExit) as exc:
            query_workflows.main()
        assert exc.value.code == 2  # argparse error for missing required group
