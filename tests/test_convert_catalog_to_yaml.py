"""
Tests for bin/convert_catalog_to_yaml.py.

The converter turns a Content Library catalog record (a BPMN-style `model`
graph) into the flat import YAML the Falcon console consumes. These tests
exercise the graph-flattening logic directly on small in-memory models — no
credentials or network. The trigger `event` catalog lookup is monkeypatched.
"""

import json

import pytest
from unittest.mock import MagicMock

import convert_catalog_to_yaml as conv


def _basic_model():
    """A minimal Signal-trigger model: trigger -> A -> end."""
    return {
        "name": "wf",
        "model": {
            "trigger": {
                "id": "trig123",
                "name": "Detection > NG-SIEM Detection",
                "trigger_type": "Signal",
                "outgoing_flow": "f1",
                "version_constraint": "~1",
            },
            "flows": {
                "f1": {"source": "trigger", "target": "A", "condition": {}},
                "f2": {"source": "A", "target": "end", "condition": {}},
            },
            "activities": {
                "A": {
                    "id": "a" * 32,
                    "name": "Act",
                    "flows": {"incoming": "f1", "outgoing": "f2"},
                    "version_constraint": "~1",
                }
            },
        },
    }


class TestTriggerEvent:
    """The Signal event value is resolved from the trigger catalog."""

    def test_event_resolved_from_catalog(self, monkeypatch):
        monkeypatch.setattr(conv, "_load_event_map", lambda: {"trig123": "Investigatable/NGSIEM"})
        out = conv.convert_model(_basic_model())
        assert out["trigger"]["event"] == "Investigatable/NGSIEM"
        assert out["trigger"]["type"] == "Signal"
        # A hex trigger id must NOT be emitted for Signal triggers.
        assert "id" not in out["trigger"]

    def test_event_override_wins(self):
        out = conv.convert_model(_basic_model(), event_override="Custom/Thing")
        assert out["trigger"]["event"] == "Custom/Thing"

    def test_trigger_next_resolves_to_activity(self, monkeypatch):
        monkeypatch.setattr(conv, "_load_event_map", lambda: {"trig123": "X"})
        out = conv.convert_model(_basic_model())
        assert out["trigger"]["next"] == ["A"]
        # The action's outgoing edge to 'end' collapses to no `next`.
        assert "next" not in out["actions"]["A"]


class TestGatewayFlattening:
    """Exclusive gateways become one condition each; parallel gateways dissolve."""

    def _exclusive_model(self):
        """trigger -> Q -> exclusive gw (Yes-branch / default No-branch)."""
        return {
            "name": "wf",
            "model": {
                "trigger": {
                    "name": "On demand",
                    "trigger_type": "On demand",
                    "outgoing_flow": "f_start",
                },
                "flows": {
                    "f_start": {"source": "trigger", "target": "Q", "condition": {}},
                    "f_q_g": {"source": "Q", "target": "gw", "condition": {}},
                    "f_yes": {
                        "source": "gw",
                        "target": "Yes",
                        "condition": {"cel_expression": "x==true", "display": ["x is true"]},
                        "name": "If x is true",
                    },
                    "f_default": {"source": "gw", "target": "No", "condition": {}},
                },
                "gateways": {
                    "gw": {
                        "type": "exclusive",
                        "flows": {
                            "incoming": ["f_q_g"],
                            "outgoing": ["f_yes", "f_default"],
                            "default": "f_default",
                        },
                    }
                },
                "activities": {
                    "Q": {"id": "q" * 32, "name": "Q", "flows": {"incoming": "f_start", "outgoing": "f_q_g"}},
                    "Yes": {"id": "y" * 32, "name": "Yes", "flows": {"incoming": "f_yes"}},
                    "No": {"id": "n" * 32, "name": "No", "flows": {"incoming": "f_default"}},
                },
            },
        }

    def _parallel_model(self):
        """trigger -> Q -> parallel gw fanning out to A and B."""
        return {
            "name": "wf",
            "model": {
                "trigger": {
                    "name": "On demand",
                    "trigger_type": "On demand",
                    "outgoing_flow": "f_start",
                },
                "flows": {
                    "f_start": {"source": "trigger", "target": "Q", "condition": {}},
                    "f_q_g": {"source": "Q", "target": "pgw", "condition": {}},
                    "f_a": {"source": "pgw", "target": "A", "condition": {}},
                    "f_b": {"source": "pgw", "target": "B", "condition": {}},
                },
                "gateways": {
                    "pgw": {
                        "type": "parallel",
                        "flows": {"incoming": ["f_q_g"], "outgoing": ["f_a", "f_b"]},
                    }
                },
                "activities": {
                    "Q": {"id": "q" * 32, "name": "Q", "flows": {"incoming": "f_start", "outgoing": "f_q_g"}},
                    "A": {"id": "a" * 32, "name": "A", "flows": {"incoming": "f_a"}},
                    "B": {"id": "b" * 32, "name": "B", "flows": {"incoming": "f_b"}},
                },
            },
        }

    def test_exclusive_gateway_is_one_condition_keyed_by_id(self):
        # An exclusive gateway becomes exactly one condition, keyed by the gateway id.
        out = conv.convert_model(self._exclusive_model())
        assert list(out["conditions"]) == ["gw"]

    def test_exclusive_condition_has_expression_next_else_and_name(self):
        cond = conv.convert_model(self._exclusive_model())["conditions"]["gw"]
        assert cond["cel_expression"] == "x==true"
        assert cond["next"] == ["Yes"]
        # The default (no-condition) flow becomes `else`, never a `default: true` entry.
        assert cond["else"] == ["No"]
        assert "default" not in cond
        assert cond["name"] == "If x is true"

    def test_action_points_at_exclusive_gateway_by_id(self):
        # Q's next is the single condition id, not a per-flow fan-out.
        out = conv.convert_model(self._exclusive_model())
        assert out["actions"]["Q"]["next"] == ["gw"]

    def test_parallel_gateway_dissolves_into_direct_fan_out(self):
        # A parallel gateway emits NO condition; the feeder node lists its
        # downstream targets directly (the console-renderable fan-out shape).
        out = conv.convert_model(self._parallel_model())
        assert out.get("conditions", {}) == {}
        assert out["actions"]["Q"]["next"] == ["A", "B"]
        # The synthetic parallel node never appears as a next target.
        assert "pgw" not in out["actions"]["Q"]["next"]

    def test_no_synthetic_pass_through_nodes(self):
        # Neither model produces default_parallel_*/default_join_* pass-throughs
        # or `default: true` conditions — the shapes that crashed the canvas.
        for model in (self._exclusive_model(), self._parallel_model()):
            text = conv._dump_yaml(conv.convert_model(model))
            assert "default_parallel" not in text
            assert "default_join" not in text
            assert "default: true" not in text

    def test_every_next_reference_resolves(self):
        # No dangling references: each next/else target is a defined node.
        for model in (self._exclusive_model(), self._parallel_model()):
            out = conv.convert_model(model)
            defined = set(out.get("actions", {})) | set(out.get("conditions", {}))
            for group in ("actions", "conditions"):
                for node in out.get(group, {}).values():
                    for ref in node.get("next", []) + node.get("else", []):
                        assert ref in defined
            for ref in out["trigger"].get("next", []):
                assert ref in defined


class TestConditionEdgeCases:
    """Malformed / edge-case gateway models must degrade cleanly."""

    def _model_with_gateway(self, gateway, flows, activities=None):
        return {
            "name": "wf",
            "model": {
                "trigger": {"trigger_type": "On demand", "outgoing_flow": "f_start"},
                "flows": {"f_start": {"source": "trigger", "target": "gw", "condition": {}}, **flows},
                "gateways": {"gw": gateway},
                "activities": activities or {},
            },
        }

    def test_scalar_outgoing_is_not_char_iterated(self):
        # A gateway whose `outgoing` is a scalar string (not a list) must be
        # normalized, not iterated character by character.
        model = self._model_with_gateway(
            {"type": "exclusive", "flows": {"outgoing": "f_yes", "default": "f_no"}},
            {
                "f_yes": {"source": "gw", "target": "Yes",
                          "condition": {"cel_expression": "x==1"}, "name": "If x"},
                "f_no": {"source": "gw", "target": "No", "condition": {}},
            },
            {"Yes": {"id": "y" * 32, "name": "Yes"}, "No": {"id": "n" * 32, "name": "No"}},
        )
        cond = conv.convert_model(model)["conditions"]["gw"]
        assert cond["next"] == ["Yes"]
        assert cond["else"] == ["No"]
        assert cond["cel_expression"] == "x==1"

    def test_dangling_default_flow_emits_no_else(self):
        # A default flow id absent from the flows map must not produce `else: []`.
        model = self._model_with_gateway(
            {"type": "exclusive", "flows": {"outgoing": ["f_yes"], "default": "f_missing"}},
            {"f_yes": {"source": "gw", "target": "Yes",
                       "condition": {"cel_expression": "x==1"}}},
            {"Yes": {"id": "y" * 32, "name": "Yes"}},
        )
        cond = conv.convert_model(model)["conditions"]["gw"]
        assert "else" not in cond

    def test_gateway_with_no_outgoing_emits_no_condition(self):
        # An exclusive gateway with no outgoing flows must not emit an empty
        # condition entry (invalid at import).
        model = self._model_with_gateway(
            {"type": "exclusive", "flows": {}}, {},
        )
        assert "gw" not in conv.convert_model(model).get("conditions", {})

    def _multi_branch_no_default_model(self):
        """An if/else-if gateway with NO default flow (two conditional branches).

        Mirrors the shape the console exports for a `use_llm == true` /
        `use_llm == false` split: two conditional flows, no default. The flow ids
        follow the console's `FROM_<conditionName>_TO_<target>` convention so the
        else-if branch key can be recovered from the id.
        """
        return self._model_with_gateway(
            {
                "type": "exclusive",
                "flows": {"outgoing": ["FROM_gw_TO_Yes", "FROM_second_TO_No"]},
            },
            {
                "FROM_gw_TO_Yes": {
                    "source": "gw", "target": "Yes",
                    "condition": {"cel_expression": "x==1"}, "name": "x is 1",
                },
                "FROM_second_TO_No": {
                    "source": "gw", "target": "No",
                    "condition": {"cel_expression": "x==2"}, "name": "x is 2",
                },
            },
            {"Yes": {"id": "y" * 32, "name": "Yes"}, "No": {"id": "n" * 32, "name": "No"}},
        )

    def test_multi_branch_else_if_is_string_ref_not_inline_list(self):
        # The import API rejects an inline-list `else_if`. A multi-branch gateway
        # must chain via a STRING `else_if` naming a separate top-level condition.
        conditions = conv.convert_model(self._multi_branch_no_default_model())["conditions"]
        assert conditions["gw"]["next"] == ["Yes"]
        assert conditions["gw"]["cel_expression"] == "x==1"
        assert conditions["gw"]["else_if"] == "second"     # string, not a list
        assert isinstance(conditions["gw"]["else_if"], str)
        # The second branch is its own standalone condition, keyed by the name
        # recovered from its flow id.
        assert conditions["second"]["next"] == ["No"]
        assert conditions["second"]["cel_expression"] == "x==2"
        assert "else_if" not in conditions["second"]

    def test_multi_branch_next_references_all_resolve(self):
        # Every next/else_if target must be a defined node or condition.
        out = conv.convert_model(self._multi_branch_no_default_model())
        defined = set(out.get("actions", {})) | set(out.get("conditions", {}))
        for cond in out["conditions"].values():
            for ref in cond.get("next", []):
                assert ref in defined
            if isinstance(cond.get("else_if"), str):
                assert cond["else_if"] in defined



class TestSubModelLoops:
    """sub_models convert to loops, including nested loops."""

    def test_submodel_becomes_loop(self):
        model = {
            "name": "wf",
            "model": {
                "trigger": {"name": "On demand", "trigger_type": "On demand", "outgoing_flow": "f1"},
                "flows": {"f1": {"source": "trigger", "target": "L", "condition": {}}},
                "activities": {},
                "sub_models": {
                    "L": {
                        "name": "For each item",
                        "multi": {"array_field": "Var.items", "sequential": True},
                        "flows": {"incoming": "f1"},
                        "model": {
                            "trigger": {"outgoing_flow": "if1"},
                            "flows": {"if1": {"source": "trigger", "target": "Inner", "condition": {}}},
                            "activities": {"Inner": {"id": "i" * 32, "name": "Inner", "flows": {"incoming": "if1"}}},
                        },
                    }
                },
            },
        }
        out = conv.convert_model(model)
        assert "L" in out["loops"]
        assert out["loops"]["L"]["for"]["input"] == "Var.items"
        assert "Inner" in out["loops"]["L"]["actions"]


def _write_basic(tmp_path):
    """Write the basic model to a JSON file, return its path."""
    p = tmp_path / "wf.json"
    p.write_text(json.dumps(_basic_model()))
    return str(p)


class TestLoadEventMap:
    """The event catalog lookup paginates and degrades gracefully."""

    def test_paginates_and_maps_id_to_category(self, monkeypatch):
        conv._EVENT_CACHE.clear()
        client = MagicMock()
        client.search_triggers.side_effect = [
            {"body": {"resources": [{"id": "t1", "category": "Cat/One"}]}},
            {"body": {"resources": []}},
        ]
        monkeypatch.setattr(conv, "get_client", lambda: client)
        result = conv._load_event_map()
        assert result["t1"] == "Cat/One"
        conv._EVENT_CACHE.clear()

    def test_returns_empty_when_offline(self, monkeypatch):
        conv._EVENT_CACHE.clear()
        monkeypatch.setattr(conv, "get_client", None)
        assert conv._load_event_map() == {}

    def test_returns_empty_on_api_error(self, monkeypatch):
        conv._EVENT_CACHE.clear()
        client = MagicMock()
        client.search_triggers.side_effect = ConnectionError("boom")
        monkeypatch.setattr(conv, "get_client", lambda: client)
        assert conv._load_event_map() == {}
        conv._EVENT_CACHE.clear()


class TestConvertFile:
    """convert_file loads JSON and prepends the provenance header."""

    def test_header_and_body(self, tmp_path):
        path = _write_basic(tmp_path)
        out = conv.convert_file(path, event_override="X/Y")
        assert out.startswith("# Converted from a CrowdStrike Content Library")
        assert "name: wf" in out
        assert "event: X/Y" in out


class TestMainCli:
    """The main() CLI: stdout, -o, --dir, and error paths."""

    def test_stdout(self, tmp_path, monkeypatch, capsys):
        path = _write_basic(tmp_path)
        monkeypatch.setattr("sys.argv", ["c", path, "--event", "X/Y"])
        conv.main()
        assert "name: wf" in capsys.readouterr().out

    def test_output_file(self, tmp_path, monkeypatch, capsys):
        path = _write_basic(tmp_path)
        dst = tmp_path / "out.yaml"
        monkeypatch.setattr("sys.argv", ["c", path, "-o", str(dst), "--event", "X/Y"])
        conv.main()
        assert dst.exists()
        assert "name: wf" in dst.read_text()
        assert "Wrote" in capsys.readouterr().out

    def test_dir_mode(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        (src / "wf.json").write_text(json.dumps(_basic_model()))
        out = tmp_path / "out"
        monkeypatch.setattr("sys.argv", ["c", "--dir", str(src), "--out-dir", str(out), "--event", "X/Y"])
        conv.main()
        assert (out / "wf.yaml").exists()

    def test_dir_requires_out_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["c", "--dir", str(tmp_path)])
        with pytest.raises(SystemExit):
            conv.main()

    def test_no_input_errors(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["c"])
        with pytest.raises(SystemExit):
            conv.main()
