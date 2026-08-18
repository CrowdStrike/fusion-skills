"""Tests for trigger_search.py — built-in catalog, API merge, and lookups.

All API calls are mocked; no CrowdStrike credentials are needed.
"""

import json

import pytest
from unittest.mock import MagicMock

import trigger_search


class TestTriggerCatalog:
    """Test the built-in trigger type catalog."""

    def test_catalog_has_required_types(self):
        assert "On demand" in trigger_search.TRIGGER_CATALOG
        assert "Signal" in trigger_search.TRIGGER_CATALOG
        assert "Scheduled" in trigger_search.TRIGGER_CATALOG
        assert "SubModel" in trigger_search.TRIGGER_CATALOG

    def test_each_type_has_description(self):
        for name, info in trigger_search.TRIGGER_CATALOG.items():
            assert "description" in info, f"{name} missing description"
            assert len(info["description"]) > 0

    def test_each_type_has_yaml_example(self):
        for name, info in trigger_search.TRIGGER_CATALOG.items():
            assert "yaml_example" in info, f"{name} missing yaml_example"
            assert "trigger:" in info["yaml_example"]


class TestSearchEventTriggers:
    """Test the API-backed Signal event discovery (search_triggers)."""

    @staticmethod
    def _client_with(resources):
        client = MagicMock()
        # One page then empty, so pagination terminates.
        client.search_triggers.side_effect = [
            {"body": {"resources": resources}},
            {"body": {"resources": []}},
        ]
        return client

    def test_maps_category_to_event(self, monkeypatch):
        resources = [
            {
                "name": "Detection > NG-SIEM Detection",
                "category": "Investigatable/NGSIEM",
                "description": "NG-SIEM detection",
                "version": "v1",
            }
        ]
        monkeypatch.setattr(
            trigger_search, "get_client", lambda: self._client_with(resources)
        )
        result = trigger_search.search_event_triggers()
        assert len(result) == 1
        assert result[0]["name"] == "Detection > NG-SIEM Detection"
        assert result[0]["event"] == "Investigatable/NGSIEM"

    def test_query_filters_by_name_or_event(self, monkeypatch):
        resources = [
            {"name": "Case", "category": "Case"},
            {"name": "Receive email", "category": "MonitoredEmail"},
            {"name": "Detection", "category": "Investigatable"},
        ]
        monkeypatch.setattr(
            trigger_search, "get_client", lambda: self._client_with(resources)
        )
        # Match by category text ('Investigatable') as well as name.
        result = trigger_search.search_event_triggers("investigatable")
        assert [t["name"] for t in result] == ["Detection"]

    def test_api_error_returns_empty(self, monkeypatch):
        client = MagicMock()
        client.search_triggers.side_effect = ConnectionError("boom")
        monkeypatch.setattr(trigger_search, "get_client", lambda: client)
        assert trigger_search.search_event_triggers() == []


class TestListAllTriggers:
    """Test the built-in trigger catalog listing."""

    def test_includes_the_four_builtins(self):
        result = trigger_search.list_all_triggers()
        assert set(result.keys()) == {"On demand", "Signal", "Scheduled", "SubModel"}

    def test_signal_example_requires_event(self):
        # The Signal example must teach the event: field, not a hex id.
        signal = trigger_search.list_all_triggers()["Signal"]["yaml_example"]
        assert "event:" in signal
        assert "id:" not in signal


class TestMainCli:
    """Test the main() CLI entry point: argument parsing and output."""

    def test_requires_an_argument(self, monkeypatch, capsys):
        # The mutually exclusive group is required; no args => SystemExit(2).
        monkeypatch.setattr("sys.argv", ["trigger_search.py"])
        with pytest.raises(SystemExit) as exc:
            trigger_search.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "one of the arguments" in err or "required" in err

    def test_list_text_output(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "--list"])
        trigger_search.main()
        out = capsys.readouterr().out
        assert "Trigger types (4)" in out
        assert "On demand" in out
        assert "Signal" in out
        # A truncated description line is printed under the name.
        assert "Manually executed" in out

    def test_list_short_flag(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "-l"])
        trigger_search.main()
        out = capsys.readouterr().out
        assert "Trigger types" in out

    def test_list_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "--list", "--json"])
        trigger_search.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert set(data.keys()) == {"On demand", "Signal", "Scheduled", "SubModel"}
        assert data["On demand"]["description"]
        # JSON list mode only exposes descriptions, not YAML examples.
        assert "yaml_example" not in data["On demand"]

    def test_type_text_output(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "--type", "Signal"])
        trigger_search.main()
        out = capsys.readouterr().out
        assert "Trigger type: Signal" in out
        assert "YAML structure:" in out
        assert "trigger:" in out

    def test_type_lookup_is_case_insensitive(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "-t", "on demand"])
        trigger_search.main()
        out = capsys.readouterr().out
        # Canonical casing is restored in the output.
        assert "Trigger type: On demand" in out

    def test_type_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--type", "Scheduled", "--json"]
        )
        trigger_search.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "Scheduled" in data
        assert data["Scheduled"]["yaml_example"] is not None

    def test_unknown_type_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--type", "Nonexistent"]
        )
        with pytest.raises(SystemExit) as exc:
            trigger_search.main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Unknown trigger type 'Nonexistent'" in out
        assert "Available:" in out

    def test_events_text_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            trigger_search,
            "search_event_triggers",
            lambda q=None: [
                {"name": "Detection > NG-SIEM Detection", "event": "Investigatable/NGSIEM"}
            ],
        )
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "--events"])
        trigger_search.main()
        out = capsys.readouterr().out
        assert "Signal event sources (1)" in out
        assert "event: Investigatable/NGSIEM" in out

    def test_events_json_passes_query(self, monkeypatch, capsys):
        seen = {}

        def fake(q=None):
            seen["q"] = q
            return [{"name": "Case", "event": "Case"}]

        monkeypatch.setattr(trigger_search, "search_event_triggers", fake)
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--events", "case", "--json"]
        )
        trigger_search.main()
        out = capsys.readouterr().out
        assert json.loads(out) == [{"name": "Case", "event": "Case"}]
        assert seen["q"] == "case"

    def test_search_alias_passes_query_to_events(self, monkeypatch, capsys):
        """--search is an alias for --events (mirrors action_search.py)."""
        seen = {}

        def fake(q=None):
            seen["q"] = q
            return [{"name": "Case", "event": "Case"}]

        monkeypatch.setattr(trigger_search, "search_event_triggers", fake)
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--search", "case", "--json"]
        )
        trigger_search.main()
        out = capsys.readouterr().out
        assert json.loads(out) == [{"name": "Case", "event": "Case"}]
        assert seen["q"] == "case"

    def test_events_empty_shows_notice(self, monkeypatch, capsys):
        monkeypatch.setattr(trigger_search, "search_event_triggers", lambda q=None: [])
        monkeypatch.setattr("sys.argv", ["trigger_search.py", "--events"])
        trigger_search.main()
        out = capsys.readouterr().out
        assert "No event triggers found" in out

    def test_list_and_type_are_mutually_exclusive(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--list", "--type", "Signal"]
        )
        with pytest.raises(SystemExit) as exc:
            trigger_search.main()
        assert exc.value.code == 2
        assert "not allowed with" in capsys.readouterr().err


class TestTriggerFields:
    """Tests for the --fields payload-path discovery mode."""

    def test_flatten_uses_full_top_level_names_and_prefixes_nested(self):
        # Top-level field names are already full dotted paths; nested children
        # carry only a relative name and must be joined onto the parent path.
        fields = [
            {"name": "Trigger.Detection.DetectionID", "type": "string"},
            {
                "name": "Trigger.Detection.MitreAttack",
                "fields": [
                    {"name": "Tactic", "type": "string"},
                    {"name": "Technique", "type": "string"},
                ],
            },
        ]
        rows = trigger_search._flatten_trigger_fields(fields)
        paths = [r[0] for r in rows]
        assert "Trigger.Detection.DetectionID" in paths
        assert "Trigger.Detection.MitreAttack.Tactic" in paths
        assert "Trigger.Detection.MitreAttack.Technique" in paths
        # The parent node itself is not emitted as a leaf.
        assert "Trigger.Detection.MitreAttack" not in paths

    def test_search_trigger_fields_sorted_dicts(self, monkeypatch):
        class FakeClient:
            def search_triggers(self, filter=None):  # noqa: A002
                return {
                    "status_code": 200,
                    "body": {
                        "resources": [
                            {
                                "fields": [
                                    {"name": "Trigger.Z", "type": "string"},
                                    {"name": "Trigger.A", "type": "integer",
                                     "display": "First"},
                                ]
                            }
                        ]
                    },
                }

        monkeypatch.setattr(trigger_search, "get_client", lambda: FakeClient())
        rows = trigger_search.search_trigger_fields("Investigatable/EPP")
        assert [r["path"] for r in rows] == ["Trigger.A", "Trigger.Z"]
        assert rows[0]["type"] == "integer" and rows[0]["display"] == "First"

    def test_fields_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            trigger_search,
            "search_trigger_fields",
            lambda cat: [{"path": "Trigger.X", "type": "string", "display": ""}],
        )
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_search.py", "--fields", "Investigatable/EPP", "--json"],
        )
        trigger_search.main()
        out = capsys.readouterr().out
        assert json.loads(out) == [
            {"path": "Trigger.X", "type": "string", "display": ""}
        ]

    def test_fields_flags_release_rejected_mitre_on_ngsiem(self, monkeypatch, capsys):
        monkeypatch.setattr(
            trigger_search,
            "search_trigger_fields",
            lambda cat: [
                {"path": "Trigger.Detection.MitreAttack.Tactic",
                 "type": "string", "display": "MitreAttack tactic"},
            ],
        )
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_search.py", "--fields", "Investigatable/NGSIEM"],
        )
        trigger_search.main()
        out = capsys.readouterr().out
        assert "NOT release-valid" in out

    def test_fields_no_reject_note_off_ngsiem(self, monkeypatch, capsys):
        # Same field path under a different category is not flagged.
        monkeypatch.setattr(
            trigger_search,
            "search_trigger_fields",
            lambda cat: [
                {"path": "Trigger.Detection.MitreAttack.Tactic",
                 "type": "string", "display": "MitreAttack tactic"},
            ],
        )
        monkeypatch.setattr(
            "sys.argv",
            ["trigger_search.py", "--fields", "Investigatable/EPP"],
        )
        trigger_search.main()
        assert "NOT release-valid" not in capsys.readouterr().out

    def test_fields_empty_shows_notice(self, monkeypatch, capsys):
        monkeypatch.setattr(trigger_search, "search_trigger_fields", lambda cat: [])
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--fields", "Bogus/Category"]
        )
        trigger_search.main()
        assert "No fields found" in capsys.readouterr().out

    def test_field_is_array_multiple_flag_authoritative(self):
        """An explicit `multiple` flag wins over the name and is marked certain."""
        # multiple: true on a singular name -> array, certain
        assert trigger_search._field_is_array(
            {"name": "Foo.RiskScore", "multiple": True}
        ) == (True, True)
        # multiple: false on a plural name -> scalar, certain (flag overrides name)
        assert trigger_search._field_is_array(
            {"name": "Foo.SourceIPs", "multiple": False}
        ) == (False, True)

    def test_field_is_array_plural_name_heuristic(self):
        """With no flag, a plural leaf name infers an array (uncertain)."""
        assert trigger_search._field_is_array({"name": "Foo.SourceIPs"}) == (True, False)
        assert trigger_search._field_is_array({"name": "Foo.UserNames"}) == (True, False)
        # Scalar-looking plural endings are not treated as lists.
        assert trigger_search._field_is_array({"name": "Foo.Status"}) == (False, False)
        assert trigger_search._field_is_array({"name": "Foo.RemoteAddress"}) == (
            False,
            False,
        )
        # Singular names are scalars.
        assert trigger_search._field_is_array({"name": "Foo.RiskScore"}) == (
            False,
            False,
        )

    def test_flatten_marks_array_fields(self):
        """_flatten_trigger_fields emits 'list'/'list?'/'' markers per field."""
        fields = [
            {"name": "Trigger.Detection.NGSIEM.SourceIPs", "type": "ip"},
            {"name": "Trigger.Detection.NGSIEM.RiskScore", "type": "int32"},
            {"name": "Trigger.Detection.Tags", "type": "string", "multiple": True},
        ]
        rows = trigger_search._flatten_trigger_fields(fields)
        markers = {r[0]: r[3] for r in rows}
        assert markers["Trigger.Detection.NGSIEM.SourceIPs"] == "list?"
        assert markers["Trigger.Detection.NGSIEM.RiskScore"] == ""
        assert markers["Trigger.Detection.Tags"] == "list"

    def test_fields_output_shows_array_annotation(self, monkeypatch, capsys):
        """--fields prints (list?) next to inferred-array fields."""
        monkeypatch.setattr(
            trigger_search,
            "search_trigger_fields",
            lambda cat: [
                {"path": "Trigger.X.SourceIPs", "type": "ip", "display": "",
                 "array": "list?"},
                {"path": "Trigger.X.RiskScore", "type": "int32", "display": "",
                 "array": ""},
            ],
        )
        monkeypatch.setattr(
            "sys.argv", ["trigger_search.py", "--fields", "Investigatable/NGSIEM"]
        )
        trigger_search.main()
        out = capsys.readouterr().out
        assert "(list?)" in out
        # The scalar field line must not carry a list marker.
        risk_line = [ln for ln in out.splitlines() if "int32" in ln][0]
        assert "list" not in risk_line

