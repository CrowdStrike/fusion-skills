"""Tests for validate.py — preflight and structural checks (no API calls needed)."""

from unittest.mock import MagicMock

import sys

import pytest

import validate


VALID_WORKFLOW = """\
# Created by the CrowdStrike Falcon Fusion authoring skill
name: Test Workflow
trigger:
  type: On demand
  name: On demand
  next:
    - ContainHost
  parameters:
    $schema: https://json-schema.org/draft-07/schema
    properties:
      device_id:
        type: string
    required:
      - device_id
    type: object
actions:
  ContainHost:
    id: aabbccdd11223344aabbccdd11223344
    name: Contain device
    properties:
      device_id: ${data['device_id']}
output_fields: []
"""


class TestPreflightCheck:
    """Test local YAML pre-flight validation checks."""

    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "good.yaml"
        f.write_text("# Header comment\nname: Test Workflow\ntrigger:\n  type: On demand\n")
        issues = validate.preflight_check(str(f))
        assert issues == []

    def test_missing_header_comment(self, tmp_path):
        f = tmp_path / "no_header.yaml"
        f.write_text("name: Test\ntrigger:\n  type: On demand\n")
        issues = validate.preflight_check(str(f))
        assert any("header comment" in i for i in issues)

    def test_missing_name_key(self, tmp_path):
        f = tmp_path / "no_name.yaml"
        f.write_text("# Header\ntrigger:\n  type: On demand\n")
        issues = validate.preflight_check(str(f))
        assert any("'name'" in i for i in issues)

    def test_missing_trigger_key(self, tmp_path):
        f = tmp_path / "no_trigger.yaml"
        f.write_text("# Header\nname: Test\n")
        issues = validate.preflight_check(str(f))
        assert any("'trigger'" in i for i in issues)

    def test_placeholder_markers_detected(self, tmp_path):
        f = tmp_path / "placeholders.yaml"
        f.write_text(
            "# Header\nname: Test\ntrigger:\n  type: On demand\n"
            "actions:\n  MyAction:\n    id: PLACEHOLDER_ACTION_ID\n"
        )
        issues = validate.preflight_check(str(f))
        assert any("PLACEHOLDER" in i for i in issues)

    def test_file_not_found(self):
        issues = validate.preflight_check("/nonexistent/file.yaml")
        assert any("not found" in i.lower() for i in issues)

    def test_multiple_placeholders_listed_once(self, tmp_path):
        f = tmp_path / "multi.yaml"
        f.write_text(
            "# Header\nname: Test\ntrigger:\n  type: On demand\n"
            "actions:\n  A:\n    id: PLACEHOLDER_ACTION_ID\n"
            "  B:\n    id: PLACEHOLDER_TRIGGER_ID\n"
        )
        issues = validate.preflight_check(str(f))
        placeholder_issues = [i for i in issues if "PLACEHOLDER" in i]
        assert len(placeholder_issues) == 1  # single message listing all
        assert "PLACEHOLDER_ACTION_ID" in placeholder_issues[0]
        assert "PLACEHOLDER_TRIGGER_ID" in placeholder_issues[0]


class TestStructuralCheck:
    """Test YAML structural validation rules."""

    def test_valid_workflow_passes(self, tmp_path):
        f = tmp_path / "good.yaml"
        f.write_text(VALID_WORKFLOW)
        issues = validate.structural_check(str(f))
        assert issues == []

    def test_cel_expression_with_else_is_allowed(self, tmp_path):
        """cel_expression supports else/else_if (CEL semantics). The validator
        must NOT flag a condition that has both a cel_expression and an else."""
        content = """\
# Header
name: CEL else test
trigger:
  type: On demand
  next:
    - is_bar
actions:
  PrintBar:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: bar
  PrintDefault:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: nope
conditions:
  is_bar:
    cel_expression: data['foo'] == "bar"
    next:
      - PrintBar
    else:
      - PrintDefault
"""
        f = tmp_path / "cel_else.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any(
            "cel_expression" in i.lower() and "else" in i.lower() for i in issues
        )
        # And the else target must not be reported as an unresolved reference.
        assert not any("PrintDefault" in i for i in issues)

    def test_invalid_action_id_not_hex(self, tmp_path):
        f = tmp_path / "bad_id.yaml"
        f.write_text(
            VALID_WORKFLOW.replace(
                "aabbccdd11223344aabbccdd11223344", "not-a-valid-hex-id-at-all!!"
            )
        )
        issues = validate.structural_check(str(f))
        assert any("invalid id" in i.lower() for i in issues)

    def test_invalid_action_id_wrong_length(self, tmp_path):
        f = tmp_path / "short_id.yaml"
        f.write_text(
            VALID_WORKFLOW.replace(
                "aabbccdd11223344aabbccdd11223344", "aabbccdd1122"
            )
        )
        issues = validate.structural_check(str(f))
        assert any("invalid id" in i.lower() for i in issues)

    def test_fake_all_same_character_id(self, tmp_path):
        f = tmp_path / "fake_id.yaml"
        f.write_text(
            VALID_WORKFLOW.replace(
                "aabbccdd11223344aabbccdd11223344", "a" * 32
            )
        )
        issues = validate.structural_check(str(f))
        assert any("fake id" in i.lower() for i in issues)

    def test_compound_plugin_id_underscore_passes(self, tmp_path):
        """A compound plugin id '<hex>_<hex>' (e.g. Charlotte AI) is valid."""
        f = tmp_path / "compound_underscore.yaml"
        f.write_text(
            VALID_WORKFLOW.replace(
                "aabbccdd11223344aabbccdd11223344",
                "bdfecafafdb44919a458fcf51d6b93a7_98dec86072334d24b37dd798098cfd63",
            )
        )
        issues = validate.structural_check(str(f))
        assert not any("invalid id" in i.lower() for i in issues)

    def test_compound_plugin_id_tilde_passes(self, tmp_path):
        """A compound plugin id '<hex>~<hex>' (e.g. VirusTotal) is valid."""
        f = tmp_path / "compound_tilde.yaml"
        f.write_text(
            VALID_WORKFLOW.replace(
                "aabbccdd11223344aabbccdd11223344",
                "4e173250822e4806b11d8b91fe57b16f~bc2df090c5f5e74635ee1e00aa9b7322",
            )
        )
        issues = validate.structural_check(str(f))
        assert not any("invalid id" in i.lower() for i in issues)

    def test_malformed_compound_id_fails(self, tmp_path):
        """A compound id with a wrong-length half is still rejected."""
        f = tmp_path / "bad_compound.yaml"
        f.write_text(
            VALID_WORKFLOW.replace(
                "aabbccdd11223344aabbccdd11223344",
                "aabbccdd11223344aabbccdd11223344_deadbeef",
            )
        )
        issues = validate.structural_check(str(f))
        assert any("invalid id" in i.lower() for i in issues)

    def test_missing_action_id(self, tmp_path):
        f = tmp_path / "no_id.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - MyAction
actions:
  MyAction:
    name: Some action
    properties:
      key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("missing required 'id'" in i.lower() for i in issues)

    def test_missing_action_name(self, tmp_path):
        f = tmp_path / "no_name.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - MyAction
actions:
  MyAction:
    id: aabbccdd11223344aabbccdd11223344
    properties:
      key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("missing required 'name'" in i.lower() for i in issues)

    def test_missing_version_constraint_for_class_action(self, tmp_path):
        f = tmp_path / "no_vc.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - CreateVariable
actions:
  CreateVariable:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    name: Create variable
    properties:
      variable_schema:
        properties:
          item:
            type: string
        type: object
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("version_constraint" in i for i in issues)

    def test_class_action_with_version_constraint_passes(self, tmp_path):
        f = tmp_path / "vc_ok.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - CreateVariable
actions:
  CreateVariable:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    version_constraint: ~1
    name: Create variable
    properties:
      variable_schema:
        properties:
          item:
            type: string
        type: object
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("version_constraint" in i for i in issues)

    def test_invalid_trigger_type(self, tmp_path):
        f = tmp_path / "bad_trigger.yaml"
        f.write_text(VALID_WORKFLOW.replace("type: On demand", "type: Invalid"))
        issues = validate.structural_check(str(f))
        assert any("invalid trigger type" in i.lower() for i in issues)

    def test_missing_trigger_type(self, tmp_path):
        # A trigger with no 'type' at all must fail — this is what let an
        # invalid workflow through and then 500 on real import.
        f = tmp_path / "no_type.yaml"
        f.write_text(
            "# Header\nname: No Type WF\n"
            "trigger:\n  event: Investigatable/EPP\n  next:\n    - Act\n"
            "actions:\n  Act:\n    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66\n"
        )
        issues = validate.structural_check(str(f))
        assert any("missing a 'type'" in i for i in issues)

    def test_valid_trigger_types_accepted(self, tmp_path):
        for trigger_type in ("Signal", "Scheduled", "SubModel"):
            f = tmp_path / f"trigger_{trigger_type}.yaml"
            content = VALID_WORKFLOW.replace("type: On demand", f"type: {trigger_type}")
            # Signal and Scheduled triggers must also carry an 'event' field, so
            # add one when exercising those types or the (correct) missing-event
            # rule fires.
            if trigger_type == "Signal":
                content = content.replace(
                    "type: Signal", "type: Signal\n  event: Investigatable/NGSIEM"
                )
            elif trigger_type == "Scheduled":
                content = content.replace(
                    "type: Scheduled", "type: Scheduled\n  event: Schedule"
                )
            f.write_text(content)
            issues = validate.structural_check(str(f))
            assert not any("invalid trigger type" in i.lower() for i in issues)
            assert not any("missing an 'event'" in i for i in issues)

    def test_signal_trigger_missing_event(self, tmp_path):
        # A Signal trigger with no 'event' field must fail — without it the
        # import API returns code 2003 "unknown trigger event named ".
        f = tmp_path / "signal_no_event.yaml"
        f.write_text(VALID_WORKFLOW.replace("type: On demand", "type: Signal"))
        issues = validate.structural_check(str(f))
        assert any("missing an 'event'" in i for i in issues)

    def test_signal_trigger_with_event_ok(self, tmp_path):
        # A Signal trigger that names its event source passes the event check.
        f = tmp_path / "signal_with_event.yaml"
        content = VALID_WORKFLOW.replace(
            "type: On demand", "type: Signal\n  event: Investigatable/NGSIEM"
        )
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("missing an 'event'" in i for i in issues)

    def test_non_signal_trigger_needs_no_event(self, tmp_path):
        # On demand / SubModel triggers do not require an event field.
        # (Signal and Scheduled DO — covered by their own tests.)
        for trigger_type in ("On demand", "SubModel"):
            f = tmp_path / f"noevent_{trigger_type.replace(' ', '_')}.yaml"
            f.write_text(VALID_WORKFLOW.replace("type: On demand", f"type: {trigger_type}"))
            issues = validate.structural_check(str(f))
            assert not any("missing an 'event'" in i for i in issues)

    def test_scheduled_trigger_missing_event(self, tmp_path):
        # A Scheduled trigger with no 'event' field must fail — the import API
        # returns code 2003 "unknown trigger event named " even with a valid
        # schedule block. The category is 'Schedule'.
        f = tmp_path / "scheduled_no_event.yaml"
        f.write_text(VALID_WORKFLOW.replace("type: On demand", "type: Scheduled"))
        issues = validate.structural_check(str(f))
        assert any("Scheduled trigger is missing an 'event'" in i for i in issues)
        assert any("event: Schedule" in i for i in issues)

    def test_scheduled_trigger_with_event_ok(self, tmp_path):
        # A Scheduled trigger that carries 'event: Schedule' passes the event check.
        f = tmp_path / "scheduled_with_event.yaml"
        content = VALID_WORKFLOW.replace(
            "type: On demand", "type: Scheduled\n  event: Schedule"
        )
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("missing an 'event'" in i for i in issues)

    def test_scheduled_schedule_cron_field_flagged(self, tmp_path):
        # A schedule block using 'cron:'/'timezone:' (instead of time_cycle/tz)
        # imports but fails release; flag the wrong field names at authoring time.
        f = tmp_path / "scheduled_wrong_fields.yaml"
        content = VALID_WORKFLOW.replace(
            "  type: On demand",
            "  type: Scheduled\n  event: Schedule\n"
            "  schedule:\n    cron: '0 8 * * *'\n    timezone: UTC",
        )
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("'cron:'" in i and "time_cycle" in i for i in issues)
        assert any("'timezone:'" in i and "tz" in i for i in issues)

    def test_scheduled_schedule_correct_fields_ok(self, tmp_path):
        # A schedule block with the release-valid field names passes.
        f = tmp_path / "scheduled_right_fields.yaml"
        content = VALID_WORKFLOW.replace(
            "  type: On demand",
            "  type: Scheduled\n  event: Schedule\n"
            "  schedule:\n    time_cycle: '0 8 * * *'\n    tz: Etc/UTC\n"
            "    start_date: ''\n    end_date: ''\n    skip_concurrent: true",
        )
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("time_cycle" in i for i in issues)
        assert not any("schedule uses" in i for i in issues)

    def test_scheduled_no_schedule_block_is_valid_template(self, tmp_path):
        # 'event: Schedule' with NO schedule block is a valid caller-scheduled job
        # template (several Foundry samples ship this) — must not be flagged for a
        # missing time_cycle.
        f = tmp_path / "scheduled_template.yaml"
        content = VALID_WORKFLOW.replace(
            "type: On demand", "type: Scheduled\n  event: Schedule"
        )
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("time_cycle" in i for i in issues)

    def test_condition_without_default_or_expression_fails(self, tmp_path):
        # A condition with a bare 'next:' and neither 'default: true' nor a
        # cel_expression/expression is what the release-time validator rejects
        # ("exclusive gateway ... has no condition set and is not marked as
        # default"). Import and API validation miss it, so structural_check must
        # catch it.
        content = """\
# Header
name: Bare condition
trigger:
  type: On demand
  next:
    - default_parallel_start
actions:
  Start:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
conditions:
  default_parallel_start:
    next:
      - Start
"""
        f = tmp_path / "bare_cond.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "default_parallel_start" in i and "neither a match expression" in i
            for i in issues
        )

    def test_bare_default_true_passthrough_fails(self, tmp_path):
        # A bare 'default: true' pass-through condition (no expression, no else)
        # is NOT release-valid. Verified live against the tenant: the release
        # (enable) API does not honor a node-level 'default: true' and rejects the
        # flow with "exclusive gateway ... has no condition set and is not marked
        # as default" — even for the console-exported 'default_gateway_decision_*'
        # shape. structural_check must flag it; the valid default mechanism is an
        # 'else:' branch on the expression-bearing condition.
        content = """\
# Header
name: Bare default passthrough
trigger:
  type: On demand
  next:
    - default_parallel_start
actions:
  Start:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
conditions:
  default_parallel_start:
    next:
      - Start
    default: true
"""
        f = tmp_path / "bare_default.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "default_parallel_start" in i and "neither a match expression" in i
            for i in issues
        )

    def test_console_default_gateway_decision_node_fails(self, tmp_path):
        # The exact shape real console exports produce: an expression-bearing
        # condition plus a sibling 'default_gateway_decision_<hex>' node marked
        # 'default: true'. Live probe proved the release API rejects the default
        # node's flow. The default node (not the expression sibling) must be the
        # one flagged.
        content = """\
# Header
name: Console default gateway
trigger:
  type: On demand
  next:
    - is_csv
    - default_gateway_decision_ae829b82_ef89_45bf_85e8_8340bd76251
actions:
  MatchAction:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
  DefaultAction:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: bye
conditions:
  is_csv:
    next:
      - MatchAction
    cel_expression: data['filename'].endsWith('.csv')
  default_gateway_decision_ae829b82_ef89_45bf_85e8_8340bd76251:
    next:
      - DefaultAction
    default: true
"""
        f = tmp_path / "console_default.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        # The default node is flagged...
        assert any(
            "default_gateway_decision_ae829b82" in i
            and "neither a match expression" in i
            for i in issues
        )
        # ...and the expression-bearing sibling is NOT.
        assert not any("'is_csv' has neither" in i for i in issues)

    def test_condition_with_expression_and_else_is_the_valid_default(self, tmp_path):
        # The release-valid replacement for a 'default: true' node: fold the
        # default target into the expression-bearing condition's 'else:' branch.
        # Verified live — converting the console 'default: true' shape to this
        # form releases OK. Must NOT be flagged.
        content = """\
# Header
name: If/else default
trigger:
  type: On demand
  next:
    - is_csv
actions:
  MatchAction:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
  DefaultAction:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: bye
conditions:
  is_csv:
    next:
      - MatchAction
    else:
      - DefaultAction
    cel_expression: data['filename'].endsWith('.csv')
"""
        f = tmp_path / "ifelse_default.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("neither a match expression" in i for i in issues)

    def test_gated_condition_with_expression_passes(self, tmp_path):
        # A gated branch with a cel_expression (and no default) is valid.
        content = """\
# Header
name: Gated branch
trigger:
  type: On demand
  next:
    - ip_present
actions:
  Start:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
conditions:
  ip_present:
    next:
      - Start
    cel_expression: data['ip'] != null
"""
        f = tmp_path / "gated.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("neither a match expression" in i for i in issues)

    def test_loop_condition_without_default_or_expression_fails(self, tmp_path):
        # The same rule applies to conditions nested inside a loop.
        content = """\
# Header
name: Loop bare condition
trigger:
  type: On demand
  next:
    - MyLoop
actions:
  Seed:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
loops:
  MyLoop:
    for:
      input: items
    actions:
      Seed:
        id: aabbccdd11223344aabbccdd11223344
        name: Print data
        properties:
          text_data: hi
    conditions:
      default_parallel_loop:
        next:
          - Seed
"""
        f = tmp_path / "loop_bare_cond.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "default_parallel_loop" in i and "neither a match expression" in i
            for i in issues
        )

    def test_nested_loop_condition_without_default_or_expression_fails(self, tmp_path):
        # A bad condition two levels deep (a loop inside a loop) must still be
        # caught. Real Content Library playbooks nest sub-models several levels;
        # a one-level scan would let this pass validation, then fail at release
        # with "exclusive gateway ... has no condition set".
        content = """\
# Header
name: Nested loop bare condition
trigger:
  type: On demand
  next:
    - OuterLoop
actions:
  Seed:
    id: aabbccdd11223344aabbccdd11223344
    name: Print data
    properties:
      text_data: hi
loops:
  OuterLoop:
    for:
      input: items
    actions:
      Seed:
        id: aabbccdd11223344aabbccdd11223344
        name: Print data
        properties:
          text_data: hi
    loops:
      InnerLoop:
        for:
          input: inner_items
        actions:
          Seed:
            id: aabbccdd11223344aabbccdd11223344
            name: Print data
            properties:
              text_data: hi
        conditions:
          deep_bare_cond:
            next:
              - Seed
"""
        f = tmp_path / "nested_loop_bare_cond.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "deep_bare_cond" in i and "neither a match expression" in i
            for i in issues
        )

    def test_nested_loop_for_input_not_checked_against_trigger_params(self, tmp_path):
        # A nested loop iterates over its parent loop's item/output, not a
        # trigger parameter, so its for.input must NOT be flagged against
        # trigger.parameters.properties (that check is top-level only).
        content = """\
# Header
name: Nested loop valid for-input
trigger:
  type: On demand
  next:
    - OuterLoop
  parameters:
    properties:
      hosts:
        type: array
actions: {}
loops:
  OuterLoop:
    for:
      input: hosts
    actions:
      Seed:
        id: aabbccdd11223344aabbccdd11223344
        name: Print data
        properties:
          text_data: hi
    loops:
      InnerLoop:
        for:
          input: host_ips
        actions:
          Seed:
            id: aabbccdd11223344aabbccdd11223344
            name: Print data
            properties:
              text_data: hi
"""
        f = tmp_path / "nested_loop_valid_input.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        # host_ips comes from the outer loop, not the trigger — no warning.
        assert not any("host_ips" in i for i in issues)
        f = tmp_path / "bad_next.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - ContainHost
actions:
  ContainHost:
    id: aabbccdd11223344aabbccdd11223344
    name: Contain device
    next:
      - NonExistentAction
    properties:
      device_id: ${data['device_id']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("NonExistentAction" in i for i in issues)

    def test_loop_input_references_undefined_param(self, tmp_path):
        f = tmp_path / "bad_loop.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Loop
  parameters:
    $schema: https://json-schema.org/draft-07/schema
    properties:
      device_id:
        type: string
    required:
      - device_id
    type: object
loops:
  Loop:
    name: For each item
    for:
      input: nonexistent_param
      sequential: true
    trigger:
      next:
        - DoStuff
    actions:
      DoStuff:
        id: aabbccdd11223344aabbccdd11223344
        name: Do stuff
        properties:
          key: value
output_fields: []
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("nonexistent_param" in i for i in issues)

    def test_loop_input_signal_trigger_namespace_not_flagged(self, tmp_path):
        # A Signal trigger exposes event fields under Trigger.Category.* /
        # Trigger.Detection.* rather than in parameters.properties. A loop that
        # iterates over such a field (e.g. a phishing playbook looping over
        # Trigger.Category.PhishingEmail.ToEmails) is valid at release, so it must
        # NOT be flagged as a missing trigger parameter.
        f = tmp_path / "signal_loop.yaml"
        content = """\
# Header
name: Signal trigger loop over event field
trigger:
  type: Signal
  event: PhishingEmail/MicrosoftO365
  version_constraint: ~1
  next:
    - Loop
actions: {}
loops:
  Loop:
    name: For each recipient
    for:
      input: Trigger.Category.PhishingEmail.ToEmails
      sequential: true
    actions:
      Seed:
        id: aabbccdd11223344aabbccdd11223344
        name: Print data
        properties:
          text_data: hi
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Trigger.Category.PhishingEmail" in i for i in issues)
        assert not any("for.input" in i for i in issues)

    def test_unclosed_data_reference(self, tmp_path):
        f = tmp_path / "unclosed.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - ContainHost
actions:
  ContainHost:
    id: aabbccdd11223344aabbccdd11223344
    name: Contain device
    properties:
      device_id: "${data['device_id']"
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("unclosed data reference" in i.lower() for i in issues)

    def test_yaml_parse_error(self, tmp_path):
        f = tmp_path / "invalid.yaml"
        f.write_text(":\n  - [\ninvalid yaml content {{{\n")
        issues = validate.structural_check(str(f))
        assert any("parse error" in i.lower() for i in issues)

    def test_non_dict_yaml_reported(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("# Header\n- just\n- a\n- list\n")
        issues = validate.structural_check(str(f))
        assert any("dictionary" in i.lower() for i in issues)


class TestValidateFile:
    """Test the combined validation flow."""

    def test_preflight_only_passes(self, tmp_path):
        f = tmp_path / "good.yaml"
        f.write_text(VALID_WORKFLOW)
        passed, messages = validate.validate_file(str(f), preflight_only=True)
        assert passed is True
        assert any("passed" in m.lower() for m in messages)

    def test_preflight_only_fails_on_errors(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("# Header\ntrigger:\n  type: On demand\n")
        passed, _ = validate.validate_file(str(f), preflight_only=True)
        assert passed is False

    def test_preflight_errors_block_further_validation(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("# Header\nname: PLACEHOLDER_NAME\ntrigger:\n  type: On demand\n")
        passed, messages = validate.validate_file(str(f), preflight_only=False)
        assert passed is False
        assert any("fix errors" in m.lower() for m in messages)

    def test_structural_errors_block_api(self, tmp_path):
        f = tmp_path / "struct_bad.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - MyAction
actions:
  MyAction:
    name: Missing ID action
    properties:
      key: value
"""
        f.write_text(content)
        passed, messages = validate.validate_file(str(f), preflight_only=False)
        assert passed is False
        assert any("structural validation failed" in m.lower() for m in messages)

    def test_warnings_do_not_fail_preflight(self, tmp_path):
        """A missing header is a WARNING, not an ERROR, so pre-flight still passes."""
        f = tmp_path / "warn.yaml"
        f.write_text("name: Test Workflow\ntrigger:\n  type: On demand\n")
        passed, messages = validate.validate_file(str(f), preflight_only=True)
        assert passed is True
        assert any("header comment" in m for m in messages)


class TestStructuralEdgeCases:
    """Cover defensive branches for malformed / non-dict structures."""

    def test_non_dict_loops_does_not_crash(self, tmp_path):
        """A scalar `loops:` value must not raise AttributeError (regression)."""
        f = tmp_path / "scalar_loops.yaml"
        f.write_text(
            "# Header\nname: Test\ntrigger:\n  type: On demand\n"
            "loops: just_a_string\n"
        )
        issues = validate.structural_check(str(f))
        # No crash, and nothing to complain about structurally.
        assert issues == []

    def test_non_dict_loop_def_skipped(self, tmp_path):
        """A loop entry whose value is not a dict is skipped, not crashed on."""
        f = tmp_path / "scalar_loop_def.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Loop
loops:
  Loop: not_a_dict
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert issues == []

    def test_scalar_next_flagged_not_iterated_as_refs(self, tmp_path):
        """A scalar `next` is flagged as a scalar-form error (the import API
        requires list form), not iterated as unresolved references."""
        f = tmp_path / "scalar_next.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - ContainHost
actions:
  ContainHost:
    id: aabbccdd11223344aabbccdd11223344
    name: Contain device
    next: SomeString
    properties:
      key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        # Not treated as a ref list (no "in 'next'" warning)...
        assert not any("in 'next'" in i for i in issues)
        # ...but flagged as a scalar-form error instead.
        assert any("scalar 'next: SomeString'" in i for i in issues)

    def test_non_dict_action_value_skipped(self, tmp_path):
        """An action whose value is a scalar is skipped by _validate_action."""
        f = tmp_path / "scalar_action.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
actions:
  ContainHost: just_a_string
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        # No 'missing id'/'missing name' errors because the action was skipped.
        assert not any("ContainHost" in i for i in issues)

    def test_loop_with_non_dict_for_skipped(self, tmp_path):
        """A loop whose `for` is not a dict skips input validation without crashing."""
        f = tmp_path / "scalar_for.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
loops:
  Loop:
    name: For each item
    for: not_a_dict
    actions:
      DoStuff:
        id: aabbccdd11223344aabbccdd11223344
        name: Do stuff
        properties:
          key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("for.input" in i for i in issues)

    def test_loop_for_without_input_skipped(self, tmp_path):
        """A loop `for` block with no `input` key skips the property check."""
        f = tmp_path / "no_input.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
loops:
  Loop:
    name: For each item
    for:
      sequential: true
    actions:
      DoStuff:
        id: aabbccdd11223344aabbccdd11223344
        name: Do stuff
        properties:
          key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("for.input" in i for i in issues)

    def test_loop_input_with_non_dict_trigger_params(self, tmp_path):
        """When trigger.parameters is not a dict, the loop input check is skipped."""
        f = tmp_path / "scalar_params.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  parameters: not_a_dict
loops:
  Loop:
    name: For each item
    for:
      input: some_param
    actions:
      DoStuff:
        id: aabbccdd11223344aabbccdd11223344
        name: Do stuff
        properties:
          key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("for.input" in i for i in issues)

    def test_loop_input_valid_when_param_defined(self, tmp_path):
        """A loop input that matches a defined trigger property produces no warning."""
        f = tmp_path / "good_loop.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  parameters:
    properties:
      device_ids:
        type: array
loops:
  Loop:
    name: For each item
    for:
      input: device_ids
    actions:
      DoStuff:
        id: aabbccdd11223344aabbccdd11223344
        name: Do stuff
        properties:
          key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("for.input" in i for i in issues)

    def test_loop_input_from_action_output_is_valid(self, tmp_path):
        """A loop that iterates over a prior action's output (a dotted reference
        whose head is a defined node) must NOT warn — it isn't a trigger param."""
        f = tmp_path / "loop_action_output.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - QueryDevices
actions:
  QueryDevices:
    id: aabbccdd11223344aabbccdd11223344
    name: Query devices
    next:
      - Loop
loops:
  Loop:
    name: For each device
    for:
      input: QueryDevices.Device.query.devices
    actions:
      DoStuff:
        id: bbccddee22334455bbccddee22334455
        name: Do stuff
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("for.input" in i for i in issues)

    def test_loop_input_from_custom_variable_is_valid(self, tmp_path):
        """A loop that iterates over WorkflowCustomVariable.<name> must NOT warn."""
        f = tmp_path / "loop_custom_var.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Loop
loops:
  Loop:
    name: For each item
    for:
      input: WorkflowCustomVariable.my_items
    actions:
      DoStuff:
        id: aabbccdd11223344aabbccdd11223344
        name: Do stuff
        properties:
          WorkflowCustomVariable:
            my_items: []
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("for.input" in i for i in issues)
        """Actions nested inside a loop are validated too."""
        f = tmp_path / "loop_bad_action.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
loops:
  Loop:
    name: For each item
    for:
      sequential: true
    actions:
      DoStuff:
        name: Do stuff
        properties:
          key: value
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("missing required 'id'" in i.lower() for i in issues)

    def test_data_ref_without_quote_is_ignored(self, tmp_path):
        """A `${data[` that is not the quoted `${data['` form is skipped (line 168)."""
        f = tmp_path / "bare_dataref.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
actions:
  ContainHost:
    id: aabbccdd11223344aabbccdd11223344
    name: Contain device
    properties:
      device_id: "${data[0]}"
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("unclosed data reference" in i.lower() for i in issues)


class TestReachability:
    """Disjoint-node detection: every node must be reachable from the trigger.

    Mirrors the release-time validator, which rejects a workflow with a
    "disjoint node" or "At least one action ... should be defined after the
    trigger". These fail server-side release but pass import — validate.py must
    catch them locally so the model doesn't churn through failed releases.
    """

    def test_trigger_without_next_is_flagged(self, tmp_path):
        """A trigger with a type/event but no 'next:' severs the whole graph."""
        content = """\
# Header
name: No trigger edge
trigger:
  type: Signal
  event: Investigatable/NGSIEM
actions:
  hydrate:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    name: Hydrate
    next:
      - notify
  notify:
    id: aabbccdd11223344aabbccdd11223344
    name: Notify
"""
        f = tmp_path / "no_trigger_next.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("no 'next:' edge" in i for i in issues)
        assert any(i.startswith("ERROR") for i in issues)

    def test_orphan_action_is_flagged(self, tmp_path):
        """An action nobody points at is unreachable and must be flagged."""
        content = """\
# Header
name: Orphan action
trigger:
  type: On demand
  next:
    - first
actions:
  first:
    id: aabbccdd11223344aabbccdd11223344
    name: First
  orphan:
    id: bbccddee22334455bbccddee22334455
    name: Orphan
"""
        f = tmp_path / "orphan.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("not reachable from the trigger" in i for i in issues)
        assert any("orphan" in i for i in issues)

    def test_fully_connected_workflow_passes(self, tmp_path):
        """A linear trigger -> A -> B chain has no disjoint nodes."""
        content = """\
# Header
name: Connected
trigger:
  type: On demand
  next:
    - a
actions:
  a:
    id: aabbccdd11223344aabbccdd11223344
    name: A
    next:
      - b
  b:
    id: bbccddee22334455bbccddee22334455
    name: B
"""
        f = tmp_path / "connected.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("reachable" in i.lower() for i in issues)

    def test_reachable_via_condition_else_branch(self, tmp_path):
        """Nodes reached only through a condition's 'else:' are not disjoint."""
        content = """\
# Header
name: Else reachable
trigger:
  type: On demand
  next:
    - gate
conditions:
  gate:
    cel_expression: data['x'] == 1
    next:
      - on_match
    else:
      - on_else
actions:
  on_match:
    id: aabbccdd11223344aabbccdd11223344
    name: On match
  on_else:
    id: bbccddee22334455bbccddee22334455
    name: On else
"""
        f = tmp_path / "else_reach.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("reachable" in i.lower() for i in issues)

    def test_reachable_via_else_if_chain(self, tmp_path):
        """Nodes reached only through a condition's string 'else_if:' chain are
        not disjoint. else_if is a STRING naming the next condition (the
        documented form, used by real console exports)."""
        content = """\
# Header
name: Else-if chain
trigger:
  type: On demand
  next:
    - is_200
conditions:
  is_200:
    expression: HTTP.response_status_code:200
    next:
      - on_ok
    else_if: is_404
  is_404:
    expression: HTTP.response_status_code:404
    next:
      - on_missing
actions:
  on_ok:
    id: aabbccdd11223344aabbccdd11223344
    name: On ok
  on_missing:
    id: bbccddee22334455bbccddee22334455
    name: On missing
"""
        f = tmp_path / "elseif_chain.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("reachable" in i.lower() for i in issues)

    def test_scalar_trigger_next_is_rejected(self, tmp_path):
        """A scalar trigger.next is rejected: the import API requires list form
        (a scalar parses locally but fails import as 'invalid YAML file').
        Confirmed live against the API — identical workflows differ only by
        next shape; scalar fails, list passes."""
        content = """\
# Header
name: Scalar next
trigger:
  type: On demand
  next: only
actions:
  only:
    id: aabbccdd11223344aabbccdd11223344
    name: Only
"""
        f = tmp_path / "scalar_next.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("scalar 'next: only'" in i for i in issues)
        assert any(i.startswith("ERROR") for i in issues)

    def test_scalar_action_next_is_rejected(self, tmp_path):
        """A scalar next on an action is likewise rejected."""
        content = """\
# Header
name: Scalar action next
trigger:
  type: On demand
  next:
    - a
actions:
  a:
    id: aabbccdd11223344aabbccdd11223344
    name: A
    next: b
  b:
    id: bbccddee22334455bbccddee22334455
    name: B
"""
        f = tmp_path / "scalar_action_next.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("scalar 'next: b'" in i for i in issues)

    def test_list_next_forms_pass(self, tmp_path):
        """List-form next on both trigger and actions is accepted."""
        content = """\
# Header
name: List next
trigger:
  type: On demand
  next:
    - a
actions:
  a:
    id: aabbccdd11223344aabbccdd11223344
    name: A
    next:
      - b
  b:
    id: bbccddee22334455bbccddee22334455
    name: B
"""
        f = tmp_path / "list_next.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("scalar 'next" in i for i in issues)

    def test_loop_internal_actions_are_not_disjoint(self, tmp_path):
        """A loop's internal actions have their own entry edge and must not be
        counted as top-level disjoint nodes."""
        content = """\
# Header
name: Loop reachable
trigger:
  type: On demand
  next:
    - Loop
loops:
  Loop:
    name: For each item
    next:
      - after
    for:
      input: WorkflowCustomVariable.items
      sequential: true
    trigger:
      next:
        - InnerAction
    actions:
      InnerAction:
        id: aabbccdd11223344aabbccdd11223344
        name: Inner
actions:
  after:
    id: bbccddee22334455bbccddee22334455
    name: After loop
    properties:
      WorkflowCustomVariable:
        items: []
"""
        f = tmp_path / "loop_reach.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("reachable" in i.lower() for i in issues)


class TestTopLevelShape:
    """Off-schema top-level shapes must be rejected, not silently passed.

    A weaker model sometimes invents a graph shape the Fusion engine doesn't
    understand (`nodes`/`edges`, `steps`/`outputs`, or action labels dumped at
    the top level with no `actions:` wrapper). These import as empty and fail at
    release, but every action-keyed check passes trivially — so validate.py must
    flag the shape itself.
    """

    def test_nodes_edges_shape_flagged(self, tmp_path):
        """A `trigger`/`nodes`/`edges` graph is off-schema."""
        content = """\
# Header
name: Node graph
trigger:
  type: On demand
nodes:
  a: {}
edges:
  - [a, b]
"""
        f = tmp_path / "nodes.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Unknown top-level key" in i for i in issues)
        assert any("nodes" in i and "edges" in i for i in issues)

    def test_steps_outputs_shape_flagged(self, tmp_path):
        """A `trigger`/`steps`/`outputs` shape is off-schema."""
        content = """\
# Header
name: Steps graph
trigger:
  type: Signal
  event: Investigatable/NGSIEM
steps:
  first: {}
outputs:
  result: x
"""
        f = tmp_path / "steps.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Unknown top-level key" in i for i in issues)

    def test_actions_at_top_level_flagged(self, tmp_path):
        """Action labels placed at the root (no `actions:` wrapper) are flagged
        both as unknown keys and as a missing actions section."""
        content = """\
# Header
name: Flat actions
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - hydrate
hydrate:
  id: aabbccdd11223344aabbccdd11223344
  name: Hydrate
"""
        f = tmp_path / "flat.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Unknown top-level key" in i for i in issues)
        assert any("defines no 'actions:'" in i for i in issues)

    def test_missing_actions_when_trigger_fans_out(self, tmp_path):
        """A trigger with `next:` but no `actions:` dict is flagged even when no
        stray top-level keys are present."""
        content = """\
# Header
name: No actions
trigger:
  type: On demand
  next:
    - somewhere
"""
        f = tmp_path / "no_actions.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("defines no 'actions:'" in i for i in issues)

    def test_valid_schema_keys_pass(self, tmp_path):
        """The documented top-level keys (incl. output_fields) are accepted."""
        content = """\
# Header
name: Valid
description: ok
trigger:
  type: On demand
  next:
    - a
actions:
  a:
    id: aabbccdd11223344aabbccdd11223344
    name: A
conditions: {}
loops: {}
output_fields: []
"""
        f = tmp_path / "valid.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Unknown top-level key" in i for i in issues)
        assert not any("defines no 'actions:'" in i for i in issues)

    def test_disconnected_nodes_key_allowed(self, tmp_path):
        """`disconnected_nodes` appears in real console exports and is allowed."""
        content = """\
# Header
name: Export shape
trigger:
  type: On demand
  next:
    - a
actions:
  a:
    id: aabbccdd11223344aabbccdd11223344
    name: A
disconnected_nodes: []
"""
        f = tmp_path / "export.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Unknown top-level key" in i for i in issues)


class TestApiValidate:
    """Test the API dry-run validation boundary with a mocked client."""

    def test_api_validate_success(self, monkeypatch, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 200,
            "body": {"errors": []},
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is True
        assert msg == "OK"
        client.import_definition.assert_called_once_with(
            data_file=str(f), validate_only=True
        )

    def test_api_validate_reports_errors(self, monkeypatch, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 400,
            "body": {"errors": [{"message": "bad action id"}]},
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is False
        assert "bad action id" in msg

    def test_api_validate_error_without_message_field(self, monkeypatch, tmp_path):
        """An error dict lacking a 'message' key falls back to str(e)."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 400,
            "body": {"errors": [{"code": 42}]},
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is False
        assert "42" in msg

    def test_api_validate_non_2xx_status(self, monkeypatch, tmp_path):
        """No errors list, but a non-200/201 status is still a failure."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 500,
            "body": {"errors": []},
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is False
        assert "500" in msg

    def test_api_validate_handles_exception(self, monkeypatch, tmp_path):
        """A connection/runtime error is caught and returned as a failure."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)

        def boom():
            raise ConnectionError("network down")

        monkeypatch.setattr(validate, "get_client", boom)
        ok, msg = validate.api_validate(str(f))
        assert ok is False
        assert "network down" in msg

    def test_api_validate_reports_resource_validation_errors(self, monkeypatch, tmp_path):
        """A 200 with empty top-level errors but a per-resource validation_errors
        list is still a failure. This is the shape the import API returns for a
        Scheduled trigger missing 'event:' (code 2003), which release then rejects.
        """
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 200,
            "body": {
                "errors": [],
                "resources": [
                    {
                        "parameters": None,
                        "validation_errors": [
                            {
                                "code": 2003,
                                "message": "unknown trigger event named ",
                                "node_id": "trigger",
                            }
                        ],
                    }
                ],
            },
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is False
        assert "unknown trigger event named" in msg
        assert "trigger" in msg

    def test_api_validate_resource_error_without_message_uses_code(self, monkeypatch, tmp_path):
        """A resource validation_error lacking a message falls back to its code."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 200,
            "body": {
                "errors": [],
                "resources": [{"validation_errors": [{"code": 2003}]}],
            },
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is False
        assert "2003" in msg

    def test_api_validate_clean_resources_pass(self, monkeypatch, tmp_path):
        """A 200 whose resources carry no validation_errors passes."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        client = MagicMock()
        client.import_definition.return_value = {
            "status_code": 200,
            "body": {"errors": [], "resources": [{"parameters": None}]},
        }
        monkeypatch.setattr(validate, "get_client", lambda: client)
        ok, msg = validate.api_validate(str(f))
        assert ok is True
        assert msg == "OK"

    def test_validate_file_runs_api_path_on_success(self, monkeypatch, tmp_path):
        """validate_file with preflight_only=False reaches api_validate."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        monkeypatch.setattr(validate, "api_validate", lambda fp: (True, "OK"))
        passed, messages = validate.validate_file(str(f), preflight_only=False)
        assert passed is True
        assert any("API validation passed" in m for m in messages)

    def test_validate_file_reports_api_failure(self, monkeypatch, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        monkeypatch.setattr(validate, "api_validate", lambda fp: (False, "boom"))
        passed, messages = validate.validate_file(str(f), preflight_only=False)
        assert passed is False
        assert any("API validation FAILED: boom" in m for m in messages)


class TestMain:
    """Test the main() CLI entry point."""

    def test_main_all_passed(self, monkeypatch, capsys, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        monkeypatch.setattr(sys, "argv", ["validate.py", "--preflight-only", str(f)])
        # preflight-only path avoids API; should exit cleanly (no SystemExit).
        validate.main()
        out = capsys.readouterr().out
        assert "All files passed validation." in out

    def test_main_reports_failure_and_exits(self, monkeypatch, capsys, tmp_path):
        f = tmp_path / "bad.yaml"
        # Missing 'name' key -> preflight ERROR -> failure.
        f.write_text("# Header\ntrigger:\n  type: On demand\n")
        monkeypatch.setattr(sys, "argv", ["validate.py", "--preflight-only", str(f)])
        with pytest.raises(SystemExit) as exc:
            validate.main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Some files failed validation." in out

    def test_main_success_glyph_and_failure_glyph(self, monkeypatch, capsys, tmp_path):
        """Passing messages get the check glyph; ERROR/WARNING lines get the cross."""
        f = tmp_path / "warn.yaml"
        # Missing header -> WARNING (not a failure under preflight-only).
        f.write_text("name: Test\ntrigger:\n  type: On demand\n")
        monkeypatch.setattr(sys, "argv", ["validate.py", "--preflight-only", str(f)])
        validate.main()
        out = capsys.readouterr().out
        assert "✓" in out  # a passing line rendered with a check
        assert "✗" in out  # the warning line rendered with a cross

    def test_main_invokes_api_path(self, monkeypatch, capsys, tmp_path):
        """Without --preflight-only, main() runs the (mocked) API validation."""
        f = tmp_path / "wf.yaml"
        f.write_text(VALID_WORKFLOW)
        monkeypatch.setattr(validate, "api_validate", lambda fp: (True, "OK"))
        monkeypatch.setattr(sys, "argv", ["validate.py", str(f)])
        validate.main()
        out = capsys.readouterr().out
        assert "All files passed validation." in out
        assert "API validation passed" in out


class TestActionRequiredProperties:
    """Per-class required-property checks that mirror the release validator.

    These properties import cleanly but fail at release, forcing an
    edit/re-import cycle. Catching them pre-deploy is what stops that churn.
    """

    _CHARLOTTE_ID = "bdfecafafdb44919a458fcf51d6b93a7_98dec86072334d24b37dd798098cfd63"

    def test_http_action_missing_method_and_content_type(self, tmp_path):
        f = tmp_path / "http_missing.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - EnrichIP
actions:
  EnrichIP:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich IP
    properties:
      http_transaction:
        request_url: https://example.com
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("request_http_method" in i for i in issues)
        assert any("request_content_type" in i for i in issues)

    def test_http_action_missing_transaction_block(self, tmp_path):
        f = tmp_path / "http_no_txn.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - EnrichIP
actions:
  EnrichIP:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich IP
    properties:
      timeout: 30
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("http_transaction" in i for i in issues)

    def test_http_action_complete_passes(self, tmp_path):
        f = tmp_path / "http_ok.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - EnrichIP
actions:
  EnrichIP:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich IP
    properties:
      http_transaction:
        request_http_method: GET
        request_url: https://example.com
        request_content_type: NONE
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("http_transaction" in i or "request_" in i for i in issues)

    def test_http_action_placeholder_definition_id_flagged(self, tmp_path):
        f = tmp_path / "http_placeholder_defid.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - EnrichIP
actions:
  EnrichIP:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich IP
    properties:
      definition_id: VIRUSTOTAL_CREDENTIAL_CONFIG_ID
      http_transaction:
        request_http_method: GET
        request_url: https://example.com
        request_content_type: NONE
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("placeholder definition_id" in i for i in issues)

    def test_http_action_no_definition_id_passes(self, tmp_path):
        f = tmp_path / "http_no_defid.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - EnrichIP
actions:
  EnrichIP:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich IP
    properties:
      http_transaction:
        request_http_method: GET
        request_url: https://example.com
        request_content_type: NONE
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("definition_id" in i for i in issues)

    def test_http_action_real_definition_id_passes(self, tmp_path):
        f = tmp_path / "http_real_defid.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - EnrichIP
actions:
  EnrichIP:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich IP
    properties:
      definition_id: 7227ab386bd646c18b27716e8fff8d26
      http_transaction:
        request_http_method: GET
        request_url: https://example.com
        request_content_type: NONE
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("definition_id" in i for i in issues)
        f = tmp_path / "llm_missing.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Summarize
actions:
  Summarize:
    id: {self._CHARLOTTE_ID}
    version_constraint: ~0
    name: Summarize
    properties:
      temperature: 0.2
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("user_prompt" in i for i in issues)
        assert any("model_name" in i for i in issues)

    def test_charlotte_llm_complete_passes(self, tmp_path):
        f = tmp_path / "llm_ok.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Summarize
actions:
  Summarize:
    id: {self._CHARLOTTE_ID}
    version_constraint: ~0
    name: Summarize
    properties:
      user_prompt: Summarize this detection.
      model_name: Claude Latest
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("user_prompt" in i or "model_name" in i for i in issues)

    _SEND_EMAIL_ID = "07413ef9ba7c47bf5a242799f59902cc"
    _REQUEST_HUMAN_INPUT_EMAIL_ID = "d6731c10b24834e2e0f4bd9d390a29c8"

    def test_send_email_empty_to_flagged(self, tmp_path):
        f = tmp_path / "email_empty.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - SendEmail
actions:
  SendEmail:
    id: {self._SEND_EMAIL_ID}
    version_constraint: ~1
    name: Send email
    properties:
      subject: Alert
      msg: Body
      to: []
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Send email" in i and "to:" in i for i in issues)

    def test_send_email_wrong_recipients_key_flagged(self, tmp_path):
        f = tmp_path / "email_wrongkey.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - SendEmail
actions:
  SendEmail:
    id: {self._SEND_EMAIL_ID}
    version_constraint: ~1
    name: Send email
    properties:
      subject: Alert
      msg: Body
      recipients:
        - user@crowdstrike.com
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Send email" in i and "recipients" in i for i in issues)

    def test_send_email_valid_to_passes(self, tmp_path):
        f = tmp_path / "email_ok.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - SendEmail
actions:
  SendEmail:
    id: {self._SEND_EMAIL_ID}
    version_constraint: ~1
    name: Send email
    properties:
      subject: Alert
      msg: Body
      to:
        - user@crowdstrike.com
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Send email" in i for i in issues)

    def test_request_human_input_missing_to_flagged(self, tmp_path):
        # The Request human input "send email" action requires a `to` recipient
        # at release ('A value is required for the property "to"'), but
        # validate_only misses it. structural_check must flag a missing `to`.
        f = tmp_path / "rhi_missing.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - AskApproval
actions:
  AskApproval:
    id: {self._REQUEST_HUMAN_INPUT_EMAIL_ID}
    version_constraint: ~1
    name: Request human input - send email
    properties:
      subject: Approve?
      msg: Please approve
      allowed_responses:
        - Approve
        - Decline
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Request human input" in i and "to:" in i for i in issues)

    def test_request_human_input_fake_domain_flagged(self, tmp_path):
        # A hardcoded fake domain dead-ends at runtime (no CID approves
        # example.com), so it must be flagged for the RHI email action too.
        f = tmp_path / "rhi_fake.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - AskApproval
actions:
  AskApproval:
    id: {self._REQUEST_HUMAN_INPUT_EMAIL_ID}
    version_constraint: ~1
    name: Request human input - send email
    properties:
      subject: Approve?
      msg: Please approve
      allowed_responses:
        - Approve
      to: security-team@example.com
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("placeholder recipient" in i for i in issues)

    def test_request_human_input_variable_recipient_passes(self, tmp_path):
        # A configurable variable recipient (the correct placeholder) is valid —
        # non-empty and not a hardcoded fake domain. This is the shape the
        # network-contain example uses.
        f = tmp_path / "rhi_var.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - InitApprover
actions:
  InitApprover:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    name: Create variable
    version_constraint: ~1
    next:
      - AskApproval
    properties:
      variable_schema:
        properties:
          approver_email:
            type: string
        type: object
  AskApproval:
    id: {self._REQUEST_HUMAN_INPUT_EMAIL_ID}
    version_constraint: ~1
    name: Request human input - send email
    properties:
      subject: Approve?
      msg: Please approve
      allowed_responses:
        - Approve
      to: ${{WorkflowCustomVariable.approver_email}}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Request human input" in i for i in issues)

    def test_send_email_fake_domain_flagged(self, tmp_path):
        f = tmp_path / "email_fake.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - SendEmail
actions:
  SendEmail:
    id: {self._SEND_EMAIL_ID}
    version_constraint: ~1
    name: Send email
    properties:
      subject: Alert
      msg: Body
      to:
        - soc-team@yourcompany.com
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("placeholder recipient" in i for i in issues)

    def test_send_email_data_ref_recipient_passes(self, tmp_path):
        f = tmp_path / "email_dataref.yaml"
        content = f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - SendEmail
actions:
  SendEmail:
    id: {self._SEND_EMAIL_ID}
    version_constraint: ~1
    name: Send email
    properties:
      subject: Alert
      msg: Body
      to:
        - ${{data['recipient']}}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Send email" in i for i in issues)

    def test_release_ref_http_body_prefix_flagged(self, tmp_path):
        f = tmp_path / "httpbody.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['Enrich.HTTP.body.data.attributes.malicious']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("HTTP.body" in i and "ERROR" in i for i in issues)

    def test_release_ref_events_index_flagged(self, tmp_path):
        f = tmp_path / "events.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['Query.events.0.RemoteIP']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(".events" in i and "results" in i for i in issues)

    def test_release_ref_dotted_array_index_flagged(self, tmp_path):
        f = tmp_path / "dottedidx.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['EnrichHostDT.response.results.0.domain_risk.risk_score']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("numeric array index" in i for i in issues)

    def test_bracket_array_index_passes(self, tmp_path):
        f = tmp_path / "bracketidx.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['Query.results'][0].RemoteIP}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("numeric array index" in i for i in issues)

    def test_release_ref_lowercase_faas_flagged(self, tmp_path):
        f = tmp_path / "faas.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['S.faas.nlpassistantapi.llminvocator_handler.completion']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("case-sensitive" in i for i in issues)

    def test_release_ref_cs_json_decode_stdout_flagged(self, tmp_path):
        f = tmp_path / "jsondecode.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Store
actions:
  Store:
    id: 6c6eab39063fa3b72d98c82af60deb8a
    class: UpdateVariable
    name: Update variable
    properties:
      WorkflowCustomVariable:
        ip: ${cs.json.decode(data['ExtractIndicators.output_stdout']).remote_ip}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("cs.json.decode" in i and "output_stdout" in i for i in issues)

    def test_pinned_long_namespace_path_flagged(self, tmp_path):
        # A pinned action referenced with its output namespace left in the path
        # (device.query) imports fine but release rejects it as an unknown
        # variable. Confirmed live (PR #34).
        f = tmp_path / "pinnedlong.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - DeviceQuery
actions:
  DeviceQuery:
    id: 68ffa99af40c84b36462daa076f535d0
    version_constraint: ~1
    name: Device Query
    next:
      - Log
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['DeviceQuery.Device.query.devices']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "DeviceQuery" in i and "version_constraint" in i and "ERROR" in i
            for i in issues
        )

    def test_pinned_event_query_namespace_path_flagged(self, tmp_path):
        f = tmp_path / "pinnedeq.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Query
actions:
  Query:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Workflow-specific event query
    next:
      - Log
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['Query.logscale.query_event.results']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "Query" in i and "results" in i and "ERROR" in i for i in issues
        )

    def test_pinned_collapsed_path_passes(self, tmp_path):
        # Correct collapsed form under a pin — no namespace segment.
        f = tmp_path / "pinnedok.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - DeviceQuery
actions:
  DeviceQuery:
    id: 68ffa99af40c84b36462daa076f535d0
    version_constraint: ~1
    name: Device Query
    next:
      - Log
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['DeviceQuery.devices']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("long namespaced path" in i for i in issues)

    def test_unpinned_long_namespace_path_passes(self, tmp_path):
        # Without version_constraint the long namespaced path is valid and
        # releases fine (PR #34, Probe C), so the guard must not fire.
        f = tmp_path / "unpinnedlong.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - DeviceQuery
actions:
  DeviceQuery:
    id: 68ffa99af40c84b36462daa076f535d0
    name: Device Query
    next:
      - Log
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['DeviceQuery.Device.query.devices']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("long namespaced path" in i for i in issues)

    def test_plain_output_stdout_ref_passes(self, tmp_path):
        f = tmp_path / "plainstdout.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Store
actions:
  Store:
    id: 6c6eab39063fa3b72d98c82af60deb8a
    class: UpdateVariable
    name: Update variable
    properties:
      WorkflowCustomVariable:
        raw: ${data['ExtractIndicators.output_stdout']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("cs.json.decode" in i for i in issues)

    def test_http_action_referenced_without_output_schema_flagged(self, tmp_path):
        f = tmp_path / "noschema.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Enrich
actions:
  Enrich:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Cloud HTTP Request
    next:
      - Log
    properties:
      http_transaction:
        request_http_method: GET
        request_content_type: JSON
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['Enrich.data.attributes.malicious']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("_cs_inline_output_schema" in i for i in issues)

    def test_http_action_with_output_schema_passes(self, tmp_path):
        f = tmp_path / "withschema.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Enrich
actions:
  Enrich:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Cloud HTTP Request
    next:
      - Log
    properties:
      http_transaction:
        request_http_method: GET
        request_content_type: JSON
        _cs_inline_output_schema: '{"type":"object"}'
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        v: ${data['Enrich.data.attributes.malicious']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("_cs_inline_output_schema" in i for i in issues)

    def test_event_query_minimal_shape_flagged(self, tmp_path):
        f = tmp_path / "eq_minimal.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      query: "#event_simpleName=* Foo=?x"
      time_range: 24h
      repo: search-all
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Event Query" in i and "minimal" in i for i in issues)

    def test_event_query_config_missing_start_flagged(self, tmp_path):
        f = tmp_path / "eq_nostart.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 7 days
    inline_configuration:
      config:
        search_query: "#repo=xdr_indicatorsrepo Ngsiem.alert.id=?x"
        repo_or_view: search-all
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("'start:'" in i and "Missing search start" in i for i in issues)

    def test_event_query_config_complete_passes(self, tmp_path):
        f = tmp_path / "eq_complete.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 7 days
    inline_configuration:
      config:
        search_query: "#repo=xdr_indicatorsrepo Ngsiem.alert.id=?x"
        repo_or_view: search-all
        start: 7d
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Missing search start" in i for i in issues)

    def test_event_query_detection_id_join_flagged(self, tmp_path):
        """A hydration query joining on Ngsiem.detection.id = ?arg is flagged."""
        f = tmp_path / "eq_wrong_join.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      detectID: ${Trigger.Detection.DetectionID}
    inline_configuration:
      config:
        search_query: "Ngsiem.detection.id = ?detectID | select([RemoteIP])"
        repo_or_view: search-all
        start: 24h
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("Ngsiem.alert.id" in i and "zero rows" in i for i in issues)

    def test_event_query_alert_id_join_passes(self, tmp_path):
        """The correct Ngsiem.alert.id join form is not flagged."""
        f = tmp_path / "eq_right_join.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      detectID: ${Trigger.Detection.DetectionID}
    inline_configuration:
      config:
        search_query: "Ngsiem.alert.id = ?detectID | select([RemoteIP])"
        repo_or_view: search-all
        start: 24h
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Ngsiem.alert.id" in i and "zero rows" in i for i in issues)

    def test_ngsiem_mitre_trigger_field_flagged(self, tmp_path):
        """MitreAttack.Tactic/Technique are advertised by trigger discovery but
        release rejects them on the NG-SIEM trigger (confirmed live)."""
        f = tmp_path / "mitre_ngsiem.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        tactic: ${data['Trigger.Detection.MitreAttack.Tactic']}
        technique: ${data['Trigger.Detection.MitreAttack.Technique']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("MitreAttack.Tactic" in i and "ERROR" in i for i in issues)
        assert any("MitreAttack.Technique" in i and "ERROR" in i for i in issues)

    def test_mitre_trigger_field_non_ngsiem_passes(self, tmp_path):
        """The guard is scoped to the NG-SIEM trigger; the base Investigatable
        (EPP) trigger is left alone."""
        f = tmp_path / "mitre_epp.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/EPP
  next:
    - Log
actions:
  Log:
    id: 04c59ceb6dff9e6cd89e5f5cf13121ab
    name: Write to log repo
    properties:
      custom_json:
        tactic: ${data['Trigger.Detection.MitreAttack.Tactic']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("MitreAttack" in i for i in issues)

    def test_event_query_detection_id_literal_not_flagged(self, tmp_path):
        """A non-?arg reference to detection.id (no hydration join) is not flagged."""
        f = tmp_path / "eq_literal.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 7 days
    inline_configuration:
      config:
        search_query: "#repo=xdr | groupBy([Ngsiem.detection.id])"
        repo_or_view: search-all
        start: 7d
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("zero rows" in i for i in issues)

    def test_event_query_alert_population_flagged(self, tmp_path):
        """An Event Query fetching an alert population by severity is flagged."""
        f = tmp_path / "eq_population.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Scheduled
  event: Schedule
  next:
    - Q
  schedule:
    time_cycle: "0 8 * * *"
    tz: Etc/UTC
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 24 hours
    inline_configuration:
      config:
        search_query: "#repo=xdr_indicatorsrepo | Severity>=4 | groupBy([DetectName])"
        repo_or_view: search-all
        start: 24h
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("population" in i and "/alerts/queries/alerts/v2" in i for i in issues)

    def test_event_query_detection_summary_population_flagged(self, tmp_path):
        """DetectionSummaryEvent + severity filter is flagged as a population fetch."""
        f = tmp_path / "eq_dse.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Scheduled
  event: Schedule
  next:
    - Q
  schedule:
    time_cycle: "0 8 * * *"
    tz: Etc/UTC
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 24 hours
    inline_configuration:
      config:
        search_query: "#event_simpleName=DetectionSummaryEvent | SeverityName=/High/"
        repo_or_view: search-all
        start: 24h
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("population" in i for i in issues)

    def test_event_query_alerts_repo_population_flagged(self, tmp_path):
        """An Event Query against the `alerts` repo + severity is a population fetch."""
        f = tmp_path / "eq_alerts.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Scheduled
  event: Schedule
  next:
    - Q
  schedule:
    time_cycle: "0 8 * * *"
    tz: Etc/UTC
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 24 hours
    inline_configuration:
      config:
        search_query: "#repo=alerts severity=High OR severity=high | groupBy([alert_id, name, severity])"
        repo_or_view: search-all
        start: 24h
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("population" in i and "/alerts/queries/alerts/v2" in i for i in issues)

    def test_event_query_held_detection_enrichment_not_flagged(self, tmp_path):
        """Enriching a held detection (alert.id=?arg) in an alert repo is NOT a population."""
        f = tmp_path / "eq_held.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Q
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      detection_id: ${Trigger.Detection.DetectionID}
    inline_configuration:
      config:
        search_query: "#repo=xdr_indicatorsrepo Ngsiem.alert.id=?detection_id | groupBy([Ngsiem.alert.id])"
        repo_or_view: search-all
        start: 24h
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("population" in i for i in issues)

    def test_event_query_ngsiem_telemetry_not_flagged(self, tmp_path):
        """A legit NG-SIEM telemetry query (no alert repo) is NOT flagged as a population."""
        f = tmp_path / "eq_telemetry.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Scheduled
  event: Schedule
  next:
    - Q
  schedule:
    time_cycle: "0 8 * * *"
    tz: Etc/UTC
actions:
  Q:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    class: Inline.QueryEvent
    version_constraint: ~1
    name: Query
    properties:
      logscale_search_start_time: 1 day
    inline_configuration:
      config:
        search_query: "#event_simpleName=UserLogonFailed | stats([UserName, ComputerName])"
        repo_or_view: search-all
        start: 1d
        end: now
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("population" in i for i in issues)

        f = tmp_path / "ngsiem_product.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Notify
actions:
  Notify:
    id: 07413ef9ba7c47bf5a242799f59902cc
    version_constraint: ~1
    name: Send email
    properties:
      to:
        - analyst@crowdstrike.com
      subject: Detection
      body: Product is ${data['Trigger.Detection.Product']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "Trigger.Detection.Product" in i and "Investigatable/NGSIEM" in i
            for i in issues
        )

    def test_base_investigatable_trigger_product_field_passes(self, tmp_path):
        f = tmp_path / "base_product.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable
  next:
    - Notify
actions:
  Notify:
    id: 07413ef9ba7c47bf5a242799f59902cc
    version_constraint: ~1
    name: Send email
    properties:
      to:
        - analyst@crowdstrike.com
      subject: Detection
      body: Product is ${data['Trigger.Detection.Product']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Trigger.Detection.Product" in i for i in issues)

    def test_ngsiem_trigger_investigatable_namespace_flagged(self, tmp_path):
        f = tmp_path / "ngsiem_investigatable.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Notify
actions:
  Notify:
    id: 07413ef9ba7c47bf5a242799f59902cc
    version_constraint: ~1
    name: Send email
    properties:
      to:
        - analyst@crowdstrike.com
      subject: Detection ${data['Trigger.Category.Investigatable.InvestigatableID']}
      body: Severity ${data['Trigger.Category.Investigatable.Severity']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "Trigger.Category.Investigatable" in i and "Investigatable/NGSIEM" in i
            for i in issues
        )

    def test_base_investigatable_namespace_passes(self, tmp_path):
        f = tmp_path / "base_investigatable.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable
  next:
    - Notify
actions:
  Notify:
    id: 07413ef9ba7c47bf5a242799f59902cc
    version_constraint: ~1
    name: Send email
    properties:
      to:
        - analyst@crowdstrike.com
      subject: Detection ${data['Trigger.Category.Investigatable.InvestigatableID']}
      body: EPP detection
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("Trigger.Category.Investigatable" in i for i in issues)

    def test_ngsiem_trigger_source_event_url_passes(self, tmp_path):
        f = tmp_path / "ngsiem_url.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - Notify
actions:
  Notify:
    id: 07413ef9ba7c47bf5a242799f59902cc
    version_constraint: ~1
    name: Send email
    properties:
      to:
        - analyst@crowdstrike.com
      subject: Detection
      body: Link is ${data['Trigger.SourceEventURL']}
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("unknown variable" in i for i in issues)

    def test_ngsiem_array_field_string_compare_flagged(self, tmp_path):
        """A plural NG-SIEM array field gated with != '' is flagged (CEL type)."""
        f = tmp_path / "ngsiem_array_cmp.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - ip_present
actions:
  Enrich:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich
    properties:
      http_transaction:
        request_http_method: GET
        request_url: https://example.com/x
        request_content_type: NONE
        request_headers: {}
        request_body: '{}'
  Skip:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    version_constraint: ~1
    name: Skip
    properties:
      variable_schema:
        properties:
          x:
            type: string
        type: object
conditions:
  ip_present:
    next:
      - Enrich
    cel_expression: "data['Trigger.Detection.NGSIEM.SourceIPs'] != null && data['Trigger.Detection.NGSIEM.SourceIPs'] != ''"
    display:
      - IP present
    else:
      - Skip
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any(
            "list(string)" in i and "SourceIPs" in i for i in issues
        )

    def test_ngsiem_array_field_size_check_passes(self, tmp_path):
        """The correct .size() > 0 gate on an array field is not flagged."""
        f = tmp_path / "ngsiem_array_size.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  next:
    - ip_present
actions:
  Enrich:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: Enrich
    properties:
      http_transaction:
        request_http_method: GET
        request_url: https://example.com/x
        request_content_type: NONE
        request_headers: {}
        request_body: '{}'
  Skip:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    version_constraint: ~1
    name: Skip
    properties:
      variable_schema:
        properties:
          x:
            type: string
        type: object
conditions:
  ip_present:
    next:
      - Enrich
    cel_expression: "data['Trigger.Detection.NGSIEM.SourceIPs'] != null && data['Trigger.Detection.NGSIEM.SourceIPs'].size() > 0"
    display:
      - IP present
    else:
      - Skip
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("list(string)" in i for i in issues)

    def test_ngsiem_array_check_only_on_ngsiem_trigger(self, tmp_path):
        """The array-comparison check does not fire on a non-NG-SIEM trigger."""
        f = tmp_path / "epp_array_cmp.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable
  next:
    - Note
actions:
  Note:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    version_constraint: ~1
    name: Note
    properties:
      variable_schema:
        properties:
          x:
            type: string
        type: object
conditions:
  gate:
    next:
      - Note
    cel_expression: "data['Trigger.Detection.NGSIEM.SourceIPs'] != ''"
    display:
      - unused
    else:
      - Note
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("list(string)" in i for i in issues)


        f = tmp_path / "signal_params.yaml"
        content = """\
# Header
name: Test
trigger:
  type: Signal
  event: Investigatable/NGSIEM
  parameters:
    type: object
    properties:
      vt_api_key:
        type: string
  next:
    - CreateVariable
actions:
  CreateVariable:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    version_constraint: ~1
    name: Create variable
    properties:
      variable_schema:
        type: object
        properties:
          item:
            type: string
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert any("parameters" in i and "Signal" in i for i in issues)

    def test_on_demand_trigger_with_parameters_allowed(self, tmp_path):
        f = tmp_path / "ondemand_params.yaml"
        content = """\
# Header
name: Test
trigger:
  type: On demand
  parameters:
    type: object
    properties:
      device_id:
        type: string
  next:
    - CreateVariable
actions:
  CreateVariable:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    version_constraint: ~1
    name: Create variable
    properties:
      variable_schema:
        type: object
        properties:
          item:
            type: string
"""
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("parameters" in i for i in issues)


class TestDataReferenceSyntax:
    """Flag wrong-form runtime data references; accept the valid forms."""

    def _wf(self, prop_value):
        return f"""\
# Header
name: Test
trigger:
  type: On demand
  next:
    - A
actions:
  A:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    version_constraint: ~1
    name: A
    properties:
      http_transaction:
        request_http_method: GET
        request_content_type: NONE
        request_url: {prop_value}
"""

    def test_bare_dollar_token_flagged(self, tmp_path):
        f = tmp_path / "bare.yaml"
        f.write_text(self._wf("https://x/$Trigger.Detection.Id"))
        issues = validate.structural_check(str(f))
        assert any("Invalid data reference" in i and "$Trigger.Detection.Id" in i for i in issues)

    def test_shell_style_paren_flagged(self, tmp_path):
        f = tmp_path / "paren.yaml"
        f.write_text(self._wf("$(data['A.field'])"))
        issues = validate.structural_check(str(f))
        assert any("Invalid data reference" in i and "$(data[" in i for i in issues)

    def test_valid_data_ref_not_flagged(self, tmp_path):
        f = tmp_path / "ok.yaml"
        f.write_text(self._wf("https://x/${data['Trigger.Detection.Id']}"))
        issues = validate.structural_check(str(f))
        assert not any("Invalid data reference" in i for i in issues)

    def test_null_safe_optional_chaining_not_flagged(self, tmp_path):
        # ${data[?'...'].orValue(...)} is a documented VALID null-safe form.
        f = tmp_path / "nullsafe.yaml"
        f.write_text(self._wf("${data[?'Trigger.Detection.Id'].orValue('')}"))
        issues = validate.structural_check(str(f))
        assert not any("Invalid data reference" in i for i in issues)


class TestPreflightTriggerType:
    """Pre-flight should reject an invalid trigger type (e.g. On_demand)."""

    def test_on_demand_underscore_rejected(self, tmp_path):
        f = tmp_path / "u.yaml"
        f.write_text("# h\nname: T\ntrigger:\n  type: On_demand\n  next:\n    - A\nactions:\n  A:\n    id: 1ba474f407d9228fc8fa02cdce8ae8ef\n    name: A\n")
        issues = validate.preflight_check(str(f))
        assert any("Invalid trigger type 'On_demand'" in i for i in issues)

    def test_valid_type_not_rejected_by_preflight(self, tmp_path):
        f = tmp_path / "v.yaml"
        f.write_text("# h\nname: T\ntrigger:\n  type: On demand\n  next:\n    - A\nactions:\n  A:\n    id: 1ba474f407d9228fc8fa02cdce8ae8ef\n    name: A\n")
        issues = validate.preflight_check(str(f))
        assert not any("Invalid trigger type" in i for i in issues)

    def test_nested_schema_type_not_mistaken_for_trigger_type(self, tmp_path):
        # A nested `type: string` inside trigger.parameters must NOT be read as
        # the trigger's type (regression guard for the earlier text-scan bug).
        f = tmp_path / "nested.yaml"
        f.write_text(
            "# h\nname: T\ntrigger:\n  type: On demand\n  parameters:\n"
            "    type: object\n    properties:\n      x:\n        type: string\n"
            "  next:\n    - A\nactions:\n  A:\n    id: 1ba474f407d9228fc8fa02cdce8ae8ef\n    name: A\n"
        )
        issues = validate.preflight_check(str(f))
        assert not any("Invalid trigger type" in i for i in issues)


class TestCustomVariableRefs:
    """WorkflowCustomVariable.<name> references must resolve to a declared variable.

    An undeclared reference imports and api_validates fine, then fails at release
    with `unknown variable "WorkflowCustomVariable.<name>"`. These guard the
    structural-tier check that catches it before deploy.
    """

    # A CreateVariable declaring `url_enrichment`, then an HTTP action that
    # references it. `{ref}` is swapped per-test to the declared or an undeclared name.
    _BASE = """\
# Created by the CrowdStrike Falcon Fusion authoring skill
name: Enrichment
trigger:
  type: On demand
  name: On demand
  next:
    - InitVars
actions:
  InitVars:
    id: 702d15788dbbffdf0b68d8e2f3599aa4
    class: CreateVariable
    name: Create variable
    version_constraint: ~1
    next:
      - Enrich
    properties:
      variable_schema:
        properties:
          url_enrichment:
            type: string
        type: object
  Enrich:
    id: 1ba474f407d9228fc8fa02cdce8ae8ef
    class: Inline.HTTPRequest
    name: Cloud HTTP Request
    version_constraint: ~1
    properties:
      http_transaction:
        request_http_method: GET
        request_url: "https://example.com/${data['WorkflowCustomVariable.{ref}']}"
output_fields: []
"""

    def test_undefined_custom_variable_ref_flagged(self, tmp_path):
        # Mirrors the real bug: reference url_indicator, which is never declared
        # (InitVars declares url_enrichment).
        f = tmp_path / "undef.yaml"
        f.write_text(self._BASE.replace("{ref}", "url_indicator"))
        issues = validate.structural_check(str(f))
        assert any(
            "url_indicator" in i and i.startswith("ERROR") for i in issues
        ), issues

    def test_declared_via_create_variable_passes(self, tmp_path):
        # Referencing the declared name produces no undefined-variable error.
        f = tmp_path / "declared.yaml"
        f.write_text(self._BASE.replace("{ref}", "url_enrichment"))
        issues = validate.structural_check(str(f))
        assert not any("undefined WorkflowCustomVariable" in i for i in issues), issues

    def test_declared_via_update_variable_setter_passes(self, tmp_path):
        # A variable declared only by an UpdateVariable setter block (no
        # CreateVariable) still counts as declared.
        content = """\
# Created by the CrowdStrike Falcon Fusion authoring skill
name: Setter
trigger:
  type: On demand
  name: On demand
  next:
    - SetVar
actions:
  SetVar:
    id: 6c6eab39063fa3b72d98c82af60deb8a
    class: UpdateVariable
    name: Update variable
    version_constraint: ~1
    next:
      - Email
    properties:
      WorkflowCustomVariable:
        notify_email: soc@example.org
  Email:
    id: 07413ef9ba7c47bf5a242799f59902cc
    name: Send email
    version_constraint: ~1
    properties:
      to:
        - ${data['WorkflowCustomVariable.notify_email']}
      subject: Hi
      msg: Body
      msg_type: html
output_fields: []
"""
        f = tmp_path / "setter.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        assert not any("undefined WorkflowCustomVariable" in i for i in issues), issues

    def test_undefined_ref_listed_once(self, tmp_path):
        # The same undeclared name referenced twice is reported once.
        content = self._BASE.replace("{ref}", "url_indicator").replace(
            "output_fields: []",
            "  Enrich2:\n"
            "    id: 1ba474f407d9228fc8fa02cdce8ae8ef\n"
            "    class: Inline.HTTPRequest\n"
            "    name: Cloud HTTP Request 2\n"
            "    version_constraint: ~1\n"
            "    properties:\n"
            "      http_transaction:\n"
            "        request_http_method: GET\n"
            "        request_url: \"https://example.com/${data['WorkflowCustomVariable.url_indicator']}\"\n"
            "output_fields: []",
        )
        f = tmp_path / "twice.yaml"
        f.write_text(content)
        issues = validate.structural_check(str(f))
        matches = [i for i in issues if "url_indicator" in i]
        assert len(matches) == 1, matches
