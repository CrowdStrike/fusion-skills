"""
Delete a CrowdStrike Fusion workflow definition.

Removes a workflow definition from the CID via the Workflows delete endpoint
(FalconPy ``delete_definitions`` / ``WorkflowDefinitionsDelete``). Use this to
clean up test, duplicate, or throwaway workflows. Deletion is permanent.

This is the supported removal path — a clean delete of a whole definition. It is
NOT a way to hand-patch a broken release: never reach for the raw
``update_definition`` API to edit a deployed workflow in place. To change a
workflow, fix the source YAML and re-import.

Usage:
    python delete_workflow.py --id <def_id>              # Delete by definition ID
    python delete_workflow.py --id <id1> --id <id2>      # Delete several by ID
    python delete_workflow.py --name "My Workflow"       # Delete by exact name
    python delete_workflow.py --id <def_id> --json       # Machine-readable output
    python delete_workflow.py --name "Probe" --yes       # Skip the confirmation prompt
"""

import argparse
import json
import sys
import os

# Add the shared common/scripts directory (two levels up) to sys.path so the
# auth module resolves no matter where this script is launched from.
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "..", "..", "..", "common", "scripts",
    ),
)
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_client  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def resolve_names_to_ids(names):
    """Resolve exact workflow names to definition IDs.

    Returns (id_map, missing) where id_map is {name: [ids]} for names that
    matched at least one definition, and missing is the list of names with no
    match. Name matching is case-insensitive, mirroring query_workflows.py.
    """
    client = get_client()
    all_defs = []
    offset = 0
    limit = 100
    while True:
        resp = client.search_definitions(limit=limit, offset=offset)
        resources = resp["body"].get("resources", [])
        if not resources:
            break
        all_defs.extend(resources)
        meta = resp["body"].get("meta", {}).get("pagination", {})
        offset += len(resources)
        if offset >= meta.get("total", 0):
            break

    id_map = {}
    missing = []
    for name in names:
        matches = [d.get("id") for d in all_defs if d.get("name", "").lower() == name.lower()]
        if matches:
            id_map[name] = matches
        else:
            missing.append(name)
    return id_map, missing


def delete_definitions(ids):
    """Delete workflow definitions by ID.

    Calls the Workflows delete endpoint. FalconPy returns the standard
    {body: {resources: [...], errors: [...]}} envelope; ``resources`` lists the
    IDs actually deleted. Returns (success, message, resources).
    """
    try:
        client = get_client()
        resp = client.delete_definitions(ids=ids)
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            return False, msg, body.get("resources", [])
        return True, "OK", body.get("resources", [])
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, str(exc), None


def _gather_ids(args):
    """Resolve the --id/--name arguments to a de-duplicated list of IDs.

    Returns (ids, missing_names). Exits with an error if nothing resolves.
    """
    ids = list(args.id or [])
    missing = []
    if args.name:
        id_map, missing = resolve_names_to_ids(args.name)
        for matched in id_map.values():
            ids.extend(matched)
    # De-duplicate while preserving order.
    seen = set()
    unique = [i for i in ids if i and not (i in seen or seen.add(i))]
    return unique, missing


def main():
    """CLI entry point for workflow deletion."""
    parser = argparse.ArgumentParser(
        description="Delete a Fusion workflow definition (permanent)"
    )
    parser.add_argument(
        "--id", action="append", metavar="DEF_ID",
        help="Workflow definition ID to delete (repeatable)",
    )
    parser.add_argument(
        "--name", action="append", metavar="NAME",
        help="Exact workflow name to delete (repeatable, resolved to ID)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirmation prompt (for scripted cleanup)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output",
    )
    args = parser.parse_args()

    if not args.id and not args.name:
        parser.error("provide at least one --id or --name")

    ids, missing = _gather_ids(args)

    if not ids:
        msg = "No matching workflow definitions found"
        if args.json:
            print(json.dumps({"deleted": [], "missing": missing, "message": msg}, indent=2))
        else:
            print(f"\n  {msg}.\n", file=sys.stderr)
            for name in missing:
                print(f"    no match: {name}", file=sys.stderr)
        sys.exit(1)

    # Confirm before a permanent delete unless suppressed (scripts pass --yes,
    # tests set the env var used by import's confirm suppression).
    suppress = args.yes or os.environ.get("FUSION_SKILLS_SUPPRESS_CONFIRM") == "1"
    if not args.json and not suppress:
        print(f"\n  About to permanently delete {len(ids)} workflow definition(s):")
        for i in ids:
            print(f"    {i}")
        answer = input("\n  Type 'delete' to confirm: ").strip().lower()
        if answer != "delete":
            print("  Aborted.\n", file=sys.stderr)
            sys.exit(1)

    ok, msg, resources = delete_definitions(ids)

    if args.json:
        print(json.dumps({
            "requested": ids,
            "deleted": resources or [],
            "missing": missing,
            "success": ok,
            "message": msg,
        }, indent=2))
        sys.exit(0 if ok else 1)

    if ok:
        # Report what the API actually deleted (resources), not the request
        # size — a 200 with empty resources means the IDs were already gone.
        deleted_count = len(resources or [])
        print(f"\n  Deleted {deleted_count} workflow definition(s).\n")
        if deleted_count < len(ids):
            print(f"    ({len(ids) - deleted_count} requested ID(s) not found / already deleted)")
        for name in missing:
            print(f"    (no match, skipped: {name})")
    else:
        print(f"\n  DELETE FAILED: {msg}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
