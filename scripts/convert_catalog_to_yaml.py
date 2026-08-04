#!/usr/bin/env python3
"""
Convert a Falcon Fusion Content Library catalog record (JSON) into import YAML.

Fusion's Content Library exports workflows as a JSON catalog record whose `model`
key holds a BPMN-style graph: `trigger`, `activities`, `flows` (source/target
edges), `gateways` (branching), and `sub_models` (loops). The Falcon console's
own import/export format is a flatter YAML: a `trigger` plus an `actions` map
where routing is expressed with `next` arrays, `conditions`, and `loops`.

This script performs that transform deterministically so examples are never
hand-converted (hand conversion previously dropped the Signal trigger's required
`event` field and invented a trigger id). The Signal `event` value is resolved
from the trigger catalog (`search_triggers`) by matching the trigger id to its
category, so it can never be forgotten.

Usage:
    python convert_catalog_to_yaml.py INPUT.json [-o OUTPUT.yaml]
    python convert_catalog_to_yaml.py INPUT.json --stdout
    python convert_catalog_to_yaml.py --dir SRC_DIR --out-dir DST_DIR

Without credentials, the event lookup is skipped and a warning is emitted; pass
--event VALUE to supply it explicitly, or run with credentials so the catalog can
be queried.
"""

import argparse
import os
import sys
import glob
import json

import yaml

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common", "scripts")
)
try:
    from auth import get_client  # pylint: disable=wrong-import-position
except ImportError:
    get_client = None

# Trigger-id -> event(category) cache, filled lazily from search_triggers.
_EVENT_CACHE = {}


def _load_event_map():
    """Return {trigger_id: category} from the trigger catalog, or {} if offline."""
    if _EVENT_CACHE:
        return _EVENT_CACHE
    if get_client is None:
        return {}
    try:
        client = get_client()
        offset = 0
        while True:
            resp = client.search_triggers(offset=offset, limit=100)
            if not isinstance(resp, dict):
                break
            resources = resp.get("body", {}).get("resources", [])
            if not resources:
                break
            for res in resources:
                tid = res.get("id")
                if tid:
                    _EVENT_CACHE[tid] = res.get("category", "")
            if len(resources) < 100:
                break
            offset += 100
    except (ConnectionError, RuntimeError, OSError):
        return {}
    return _EVENT_CACHE


def _flow_target(model, flow_id):
    """Return the target node of a flow id, or None."""
    return model.get("flows", {}).get(flow_id, {}).get("target")


def _outgoing_targets(node):
    """Return the list of outgoing flow ids for an activity/gateway node."""
    flows = node.get("flows", {})
    outgoing = flows.get("outgoing")
    if outgoing is None:
        return []
    return outgoing if isinstance(outgoing, list) else [outgoing]


def _node_type(model, node_id):
    """Classify a node id as end / exclusive / parallel / activity / loop / unknown.

    The transform hinges on gateway *type*: an exclusive gateway becomes a single
    branching condition, while a parallel gateway (fork or join) has no visual
    equivalent in the flat YAML and must be dissolved into the edges around it.
    """
    if not node_id or node_id == "end":
        return "end"
    gateway = model.get("gateways", {}).get(node_id)
    if gateway is not None:
        return gateway.get("type", "exclusive")
    if node_id in model.get("activities", {}):
        return "activity"
    if node_id in model.get("sub_models", {}):
        return "loop"
    return "unknown"


def _resolve_targets(model, target, seen=None):
    """Resolve a flow target to the flat-YAML node labels it points at.

    - end / missing        -> [] (end is implicit)
    - exclusive gateway     -> [gateway_id] (the single condition that represents it)
    - parallel gateway      -> dissolve: recurse into every outgoing target, so a
                               fork expands to its branch targets and a join passes
                               through to whatever follows it
    - activity / loop       -> [node_id] verbatim

    A parallel gateway never appears as a `next` target itself; that synthetic node
    is exactly what the console canvas cannot build an edge from. The `seen` set
    guards against cycles through parallel joins.
    """
    if seen is None:
        seen = set()
    kind = _node_type(model, target)
    if kind == "end":
        return []
    if kind == "exclusive":
        return [target]
    if kind == "parallel":
        if target in seen:
            return []
        seen.add(target)
        resolved = []
        for flow_id in _outgoing_targets(model["gateways"][target]):
            resolved.extend(_resolve_targets(model, _flow_target(model, flow_id), seen))
        return resolved
    return [target]


def _resolve_next(model, node):
    """Resolve a node's outgoing flows to a flat list of next-node labels."""
    nxt = []
    for flow_id in _outgoing_targets(node):
        nxt.extend(_resolve_targets(model, _flow_target(model, flow_id)))
    return nxt


def _convert_activity(activity, model):
    """Convert one model activity into an actions-map entry (order-preserving)."""
    out = {}
    for key in ("id", "class", "default_name", "continue_on_error", "name", "version_constraint"):
        if key in activity:
            out[key] = activity[key]
    nxt = _resolve_next(model, activity)
    if nxt:
        out["next"] = nxt
    for key in ("properties", "inline_configuration"):
        if key in activity:
            out[key] = activity[key]
    return out


def _match_expression(entry, condition):
    """Copy a flow's match expression (expression/cel_expression/display) onto entry."""
    for key in ("expression", "cel_expression", "display"):
        if key in condition:
            entry[key] = condition[key]


def _branch_condition_name(flow_id, gid, index):
    """Return the standalone-condition key for an else-if branch.

    The console encodes each conditional flow id as `FROM_<conditionName>_TO_<target>`,
    where `<conditionName>` is that branch's own condition id (the primary branch's
    equals the gateway id). Recovering it makes the emitted condition key match what
    the console import expects. Falls back to a synthetic, unique name when the flow
    id does not follow that pattern (e.g. hand-authored fixtures).
    """
    if flow_id and flow_id.startswith("FROM_") and "_TO_" in flow_id:
        return flow_id[len("FROM_") :].split("_TO_", 1)[0]
    return f"{gid}_elseif{index}"


def _convert_conditions(model):
    """Convert every EXCLUSIVE gateway into one or more linked condition entries.

    Parallel gateways are dissolved by `_resolve_targets` and never emit a
    condition. An exclusive gateway with a single conditional flow becomes one entry
    keyed by the gateway id (its `next`, match expression, and `name`), with the
    `default` flow supplying `else`.

    A gateway with MORE than one conditional flow (if / else-if / … / else) becomes a
    chain: the gateway-id entry carries the first branch and an `else_if` that is a
    STRING naming the next branch's standalone condition; each subsequent branch is
    its own top-level condition, linked by its own `else_if` string, and the final
    branch carries the `else` (default flow). The import API requires `else_if` to be
    a string reference to another condition — an inline list is rejected at import as
    an invalid YAML file.

    No `default: true` pass-through is ever emitted — that shape (and the synthetic
    parallel nodes it accompanied) is what broke the console canvas renderer.
    """
    conditions = {}
    flows = model.get("flows", {})
    for gid, gateway in model.get("gateways", {}).items():
        if gateway.get("type") == "parallel":
            continue
        gw_flows = gateway.get("flows", {})
        default_flow_id = gw_flows.get("default")
        # Use _outgoing_targets so a scalar-string `outgoing` is normalized to a
        # list (matching every other consumer) rather than iterated character by
        # character. Keep the flow id alongside the flow so else-if branch keys can
        # be recovered from it.
        conditional = [
            (fid, flows[fid])
            for fid in _outgoing_targets(gateway)
            if fid != default_flow_id and fid in flows
        ]
        # Only treat the default flow as present if it actually exists in the flows
        # map — a dangling default id must not produce a spurious `else`.
        default_flow = flows.get(default_flow_id) if default_flow_id in flows else None

        # A gateway with only a default flow (no conditional) still needs its `else`
        # to route somewhere — emit a bare entry keyed by the gateway id.
        if not conditional:
            if default_flow is not None:
                else_next = _resolve_targets(model, default_flow.get("target"))
                if else_next:
                    conditions[gid] = {"else": else_next}
            continue

        # Condition keys: the first branch is keyed by the gateway id; each extra
        # branch derives its own condition id from its flow id.
        keys = [gid] + [
            _branch_condition_name(fid, gid, index)
            for index, (fid, _flow) in enumerate(conditional[1:], start=1)
        ]

        for pos, (_fid, flow) in enumerate(conditional):
            entry = {}
            branch_next = _resolve_targets(model, flow.get("target"))
            if branch_next:
                entry["next"] = branch_next
            _match_expression(entry, flow.get("condition", {}))
            if pos + 1 < len(conditional):
                entry["else_if"] = keys[pos + 1]
            elif default_flow is not None:
                entry["else"] = _resolve_targets(model, default_flow.get("target"))
            if flow.get("name"):
                entry["name"] = flow["name"]
            if entry:
                conditions[keys[pos]] = entry
    return conditions


def _convert_trigger(model, event_override):
    """Convert model.trigger into the flat import trigger, resolving `event`."""
    src = model.get("trigger", {})
    trigger = {}
    outgoing = src.get("outgoing_flow")
    if outgoing:
        nxt = _resolve_targets(model, _flow_target(model, outgoing))
        if nxt:
            trigger["next"] = nxt
    if "name" in src:
        trigger["name"] = src["name"]

    # Resolve the Signal event (category). This is the field hand-conversion lost.
    ttype = src.get("trigger_type")
    event = event_override
    if event is None and ttype == "Signal":
        event = _load_event_map().get(src.get("id"), "")
        if not event:
            print(
                f"  WARNING: could not resolve trigger event for id {src.get('id')} "
                "(run with credentials or pass --event). Signal import will fail "
                "without it.",
                file=sys.stderr,
            )
    if event:
        trigger["event"] = event
    if ttype:
        trigger["type"] = ttype
    # Pass through parameters / schedule config that some trigger types carry.
    for key in ("parameters", "timer_event_definition", "schedule"):
        if key in src:
            trigger[key] = src[key]
    if "version_constraint" in src:
        trigger["version_constraint"] = src["version_constraint"]
    return trigger


def convert_model(record, event_override=None):
    """Convert a full catalog record (with a `model`) into an import-YAML dict."""
    model = record.get("model", {})

    workflow = {}
    if "name" in record:
        workflow["name"] = record["name"]
    if "description" in record:
        workflow["description"] = record["description"]
    workflow["trigger"] = _convert_trigger(model, event_override)

    actions = {}
    for aname, activity in model.get("activities", {}).items():
        actions[aname] = _convert_activity(activity, model)
    if actions:
        workflow["actions"] = actions

    conditions = _convert_conditions(model)
    if conditions:
        workflow["conditions"] = conditions

    # sub_models -> loops (recursively convert the nested model).
    loops = {}
    for sname, sub in model.get("sub_models", {}).items():
        loop = {}
        if "name" in sub:
            loop["display"] = sub["name"]
            loop["name"] = sub["name"]
        loop["next"] = _resolve_next(model, sub)
        multi = sub.get("multi", {})
        if multi:
            for_block = {"input": multi.get("array_field", "")}
            # A while-style loop carries a cel_condition instead of iterating an array.
            cond = multi.get("condition", {})
            if cond.get("cel_expression"):
                for_block["cel_condition"] = cond["cel_expression"]
            if cond.get("display"):
                for_block["condition_display"] = cond["display"]
            for_block["continue_on_partial_execution"] = multi.get(
                "continue_on_partial_execution", False
            )
            for_block["sequential"] = multi.get("sequential", True)
            loop["for"] = for_block
        inner = convert_model({"model": sub.get("model", {})}, event_override)
        if "trigger" in inner:
            loop["trigger"] = {"next": inner["trigger"].get("next", [])}
        if "actions" in inner:
            loop["actions"] = inner["actions"]
        if "conditions" in inner:
            loop["conditions"] = inner["conditions"]
        # Carry through any nested loops (sub_models within this sub_model),
        # otherwise their nodes become dangling references at import time.
        if "loops" in inner:
            loop["loops"] = inner["loops"]
        loops[sname] = loop
    if loops:
        workflow["loops"] = loops

    return workflow


class _IndentDumper(yaml.Dumper):  # pylint: disable=too-many-ancestors
    """Dumper that indents block sequences under their key (console style)."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _dump_yaml(workflow):
    """Serialize the workflow dict to YAML with stable key order."""
    return yaml.dump(
        workflow,
        Dumper=_IndentDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=1000,
    )


def convert_file(in_path, event_override=None):
    """Load a catalog JSON file and return its import-YAML string."""
    with open(in_path, encoding="utf-8") as handle:
        record = json.load(handle)
    workflow = convert_model(record, event_override)
    header = "# Converted from a CrowdStrike Content Library catalog record.\n"
    return header + _dump_yaml(workflow)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument("input", nargs="?", help="Catalog JSON file to convert")
    parser.add_argument("-o", "--output", help="Write YAML to this path")
    parser.add_argument("--stdout", action="store_true", help="Print YAML to stdout")
    parser.add_argument("--dir", help="Convert every *.json under this directory")
    parser.add_argument("--out-dir", help="Output directory for --dir mode")
    parser.add_argument("--event", help="Override the Signal trigger event value")
    args = parser.parse_args()

    if args.dir:
        if not args.out_dir:
            parser.error("--dir requires --out-dir")
        for path in sorted(glob.glob(os.path.join(args.dir, "**", "*.json"), recursive=True)):
            rel = os.path.relpath(path, args.dir)
            dst = os.path.join(args.out_dir, os.path.splitext(rel)[0] + ".yaml")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as handle:
                handle.write(convert_file(path, args.event))
            print(f"  {rel} -> {dst}")
        return

    if not args.input:
        parser.error("provide an input JSON file (or use --dir)")
    yaml_text = convert_file(args.input, args.event)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(yaml_text)
        print(f"Wrote {args.output}")
    else:
        print(yaml_text)


if __name__ == "__main__":
    main()
