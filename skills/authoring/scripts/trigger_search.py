"""
List and describe CrowdStrike Fusion workflow trigger types.

Queries the API for trigger activities and supplements with a built-in
catalog of trigger type YAML structures.

Usage:
    python trigger_search.py --list                  # Show the 4 trigger types
    python trigger_search.py --type "On demand"      # YAML structure for a type
    python trigger_search.py --events                # All Signal event values (API)
    python trigger_search.py --events detection      # Filter event values by text
    python trigger_search.py --fields Investigatable/EPP  # Payload field paths for a trigger
    python trigger_search.py --list --json           # Machine-readable output
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "common", "scripts"))
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_client  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Built-in trigger catalog ────────────────────────────────────────────────
# These document the YAML structure for each trigger type, derived from our
# 30 production workflows and the CrowdStrike API Reference.

TRIGGER_CATALOG = {
    "On demand": {
        "description": (
            "Manually executed via the Falcon console or the Workflow Execution "
            "API. Accepts user-defined input parameters via JSON Schema. "
            "(API-invoked workflows use this same type — 'API' is an execution "
            "method, not a distinct trigger type.)"
        ),
        "yaml_example": """\
trigger:
    next:
        - FirstActionName
    name: On demand
    parameters:
        $schema: https://json-schema.org/draft-07/schema
        properties:
            my_param:
                type: string
                title: My Parameter
                description: Describe this input field.
        required:
            - my_param
        type: object
    type: On demand""",
    },
    "Signal": {
        "description": (
            "Fires automatically when a CrowdStrike event occurs (detection, "
            "case, identity event, etc.). trigger.type is always 'Signal' and the "
            "trigger MUST carry an 'event' field naming the event source "
            "(the trigger category, e.g. 'Investigatable/NGSIEM'). Find event "
            "values with --events, and the payload field paths a trigger delivers "
            "with --fields <category>."
        ),
        "yaml_example": """\
trigger:
    next:
        - FirstActionName
    name: Detection > NG-SIEM Detection   # descriptive event-source label
    event: Investigatable/NGSIEM          # REQUIRED — the trigger category (--events)
    type: Signal                          # always 'Signal' for event triggers
    version_constraint: ~1
    # A Signal trigger is identified by 'event' + 'name'. Omitting 'event' fails
    # import with code 2003 'unknown trigger event named '. Do not add a hex id.""",
    },
    "Scheduled": {
        "description": "Runs on a cron-like schedule (e.g., every hour, daily).",
        "yaml_example": """\
trigger:
    next:
        - FirstActionName
    name: Scheduled
    type: Scheduled
    schedule:
        cron: "0 */6 * * *"   # Every 6 hours
        timezone: UTC""",
    },
    "SubModel": {
        "description": (
            "Fires when this workflow is invoked by another workflow (chaining). "
            "The parent calls it via an 'Execute workflow' action, passing "
            "parameters that become trigger data."
        ),
        "yaml_example": """\
trigger:
    next:
        - FirstActionName
    name: SubModel
    parameters:
        $schema: https://json-schema.org/draft-07/schema
        properties:
            my_param:
                type: string
                title: My Parameter
        required:
            - my_param
        type: object
    type: SubModel""",
    },
}


def search_event_triggers(query=None):
    """
    Fetch Signal event triggers from the Fusion trigger catalog.

    Each Signal trigger's `event:` value is its `category` in the API. Returns a
    list of {name, event, description, version} dicts, optionally filtered to
    those whose name or category contains `query` (case-insensitive).
    """
    try:
        client = get_client()
        triggers = []
        offset = 0
        while True:
            resp = client.search_triggers(offset=offset, limit=100)
            if not isinstance(resp, dict):
                break
            resources = resp.get("body", {}).get("resources", [])
            if not resources:
                break
            for res in resources:
                triggers.append(
                    {
                        "name": res.get("name", ""),
                        "event": res.get("category", ""),
                        "description": res.get("description", ""),
                        "version": res.get("version", ""),
                    }
                )
            if len(resources) < 100:
                break
            offset += 100
    except (ConnectionError, RuntimeError, OSError):
        return []

    if query:
        needle = query.lower()
        triggers = [
            t
            for t in triggers
            if needle in t["name"].lower() or needle in t["event"].lower()
        ]
    triggers.sort(key=lambda t: t["name"])
    return triggers


# Endings that look plural but are singular scalars (Status, Address, Analysis,
# Progress). Used only by the plural-name fallback below.
_SCALAR_PLURAL_SUFFIXES = ("ss", "us", "is")


def _field_is_array(field):
    """Return (is_array, certain) for a trigger/action field object.

    The Fusion action schema (``ActivityExtField``) carries a ``multiple``
    boolean — "Indicates this field is a list" — and populates it. When that
    flag is present it is authoritative: ``certain`` is True. The public
    ``search_triggers`` response (``TriggerExtField``) omits ``multiple``
    entirely, so for trigger fields there is no API signal and we fall back to a
    name heuristic: a leaf whose name is plural (ends in a plain ``s``, not the
    scalar-looking ``ss``/``us``/``is`` endings such as Status/Address/Analysis)
    is treated as a list. ``certain`` is False for the heuristic, so callers can
    label it as inferred. Threading ``multiple`` through means the annotation
    becomes exact automatically if the trigger endpoint ever exposes the flag.
    """
    if "multiple" in field:
        return bool(field["multiple"]), True
    name = field.get("name", "")
    leaf = name.rsplit(".", 1)[-1]
    if len(leaf) > 1 and leaf.endswith("s") and not leaf.endswith(_SCALAR_PLURAL_SUFFIXES):
        return True, False
    return False, False


def _flatten_trigger_fields(fields, prefix=""):
    """Flatten a trigger's recursive fields[] tree into (path, type, display, array) rows.

    Top-level field `name` values are already fully-qualified dotted paths
    (e.g. ``Trigger.Detection.DetectionID``). Nested `fields[]` children carry
    only a relative `name` (e.g. ``Tactic`` under ``Trigger.Detection.MitreAttack``),
    so those are joined onto the parent path with a dot. The fourth tuple element
    is an array marker: "list" when the field is a confirmed list (API `multiple`
    flag), "list?" when inferred from a plural name, or "" for a scalar.
    """
    rows = []
    for field in fields or []:
        name = field.get("name", "")
        path = f"{prefix}.{name}" if prefix else name
        children = field.get("fields")
        if children:
            rows.extend(_flatten_trigger_fields(children, path))
        else:
            is_array, certain = _field_is_array(field)
            marker = ("list" if certain else "list?") if is_array else ""
            rows.append((path, field.get("type", ""), field.get("display", ""), marker))
    return rows


def search_trigger_fields(category):
    """Return the payload field paths a trigger delivers, for a given category.

    `category` is a Signal `event:` value (e.g. ``Investigatable/EPP``). Returns
    a list of {path, type, display} dicts describing every leaf field in the
    trigger payload — the exact ``${data['Trigger....']}`` references available
    to downstream actions. Returns an empty list if the category is unknown.
    """
    try:
        client = get_client()
        resp = client.search_triggers(filter=f"category:'{category}'")
        if not isinstance(resp, dict):
            return []
        resources = resp.get("body", {}).get("resources", [])
    except (ConnectionError, RuntimeError, OSError):
        return []

    if not resources:
        return []
    rows = _flatten_trigger_fields(resources[0].get("fields", []))
    rows.sort(key=lambda r: r[0])
    return [
        {"path": p, "type": t, "display": d, "array": marker}
        for p, t, d, marker in rows
    ]


# Fields the trigger API advertises but the release validator rejects as an
# unknown variable (confirmed live). Keyed by category. validate.py carries the
# matching guard (NGSIEM_REJECTED_MITRE_FIELDS) that flags a workflow using them.
RELEASE_REJECTED_FIELDS = {
    "Investigatable/NGSIEM": frozenset({
        "Trigger.Detection.MitreAttack.Tactic",
        "Trigger.Detection.MitreAttack.Technique",
    }),
}


def _print_fields(category, as_json):
    """Print the payload field paths for a trigger category (from the API)."""
    fields = search_trigger_fields(category)
    if as_json:
        print(json.dumps(fields, indent=2))
        return
    if not fields:
        print(
            f"No fields found for category '{category}'. Check the value with "
            "--events, or verify credentials."
        )
        return
    print(f"\nPayload fields for '{category}' ({len(fields)}):\n")
    print("  Reference any of these downstream as ${data['<path>']}.")
    print(
        "  Fields marked (list) or (list?) are multivalued: gate with "
        ".size() > 0 (not != ''),\n  and index an element with [0] "
        "(e.g. for a URL or variable). '?' = inferred from a plural name;\n"
        "  the trigger API omits an explicit array flag.\n"
    )
    rejected = RELEASE_REJECTED_FIELDS.get(category, frozenset())
    for field in fields:
        print(f"  ${{data['{field['path']}']}}")
        array_note = {
            "list": "  (list)",
            "list?": "  (list?)",
        }.get(field.get("array", ""), "")
        meta = field["type"] + (f" — {field['display']}" if field["display"] else "")
        line = (meta.strip() + array_note).strip()
        if line:
            print(f"      {line}")
        if field["path"] in rejected:
            print(
                "      NOT release-valid: advertised here but release rejects it "
                "as an unknown variable. Hydrate this from the detection instead."
            )
    print()


def list_all_triggers():
    """Return the built-in catalog of the four trigger types."""
    return {name: info.copy() for name, info in TRIGGER_CATALOG.items()}


def _print_events(query, as_json):
    """Print Signal event sources (name -> event value) from the API."""
    events = search_event_triggers(query or None)
    if as_json:
        print(json.dumps(events, indent=2))
    elif not events:
        print("No event triggers found (check credentials or QUERY filter).")
    else:
        print(f"\nSignal event sources ({len(events)}):\n")
        print("  Set trigger.event to the value shown; keep trigger.type: Signal.\n")
        for trigger in events:
            print(f"  {trigger['name']}")
            print(f"    event: {trigger['event']}")
        print()


def _print_list(triggers, as_json):
    """Print the built-in trigger type catalog."""
    if as_json:
        out = {name: {"description": info.get("description", "")} for name, info in triggers.items()}
        print(json.dumps(out, indent=2))
        return
    print(f"\nTrigger types ({len(triggers)}):\n")
    for name, info in triggers.items():
        desc = info.get("description", "")
        print(f"  {name}")
        if desc:
            print(f"    {desc[:120]}")
        print()


def _print_type(triggers, type_name, as_json):
    """Print the YAML structure for a single trigger type (case-insensitive)."""
    match = next(
        ((name, info) for name, info in triggers.items() if name.lower() == type_name.lower()),
        None,
    )
    if not match:
        print(f"Unknown trigger type '{type_name}'.")
        print(f"Available: {', '.join(triggers.keys())}")
        sys.exit(1)

    name, info = match
    if as_json:
        print(json.dumps({name: info}, indent=2))
        return
    print(f"\nTrigger type: {name}")
    print(f"  {info.get('description', '')}\n")
    example = info.get("yaml_example")
    if example:
        print("YAML structure:")
        print(example)
    else:
        print("  (No YAML example available — use the exported structure from an existing workflow)")
    print()


def main():
    """CLI entry point for trigger search."""
    parser = argparse.ArgumentParser(description="List CrowdStrike Fusion trigger types")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", "-l", action="store_true", help="List all trigger types")
    group.add_argument("--type", "-t", metavar="NAME", help="Show YAML structure for a trigger type")
    group.add_argument(
        "--events",
        "-e",
        "--search",
        "-s",
        nargs="?",
        const="",
        metavar="QUERY",
        help="List Signal event sources (name -> event value) from the API, "
        "optionally filtered by QUERY (e.g. --events detection). --search/-s is "
        "an alias, mirroring action_search.py.",
    )
    group.add_argument(
        "--fields",
        "-f",
        metavar="CATEGORY",
        help="List the payload field paths a trigger delivers, for a Signal "
        "category (e.g. --fields Investigatable/EPP). Prints ready-to-use "
        "${data['Trigger....']} references so you never guess a field path.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    if args.events is not None:
        _print_events(args.events, args.json)
    elif args.fields:
        _print_fields(args.fields, args.json)
    elif args.list:
        _print_list(list_all_triggers(), args.json)
    elif args.type:
        _print_type(list_all_triggers(), args.type, args.json)


if __name__ == "__main__":
    main()
