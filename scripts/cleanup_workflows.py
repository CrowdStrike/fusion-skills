#!/usr/bin/env python3
"""
Delete CrowdStrike Falcon Fusion workflows via the Workflows delete API.

Fusion DOES expose a workflow-delete API: FalconPy ``delete_definitions``
(endpoint ``WorkflowDefinitionsDelete``, DELETE /workflows/entities/definitions/v1).
This script uses it to remove test/duplicate workflows by name or pattern —
no browser, no console UI, no persistent login profile.

For a single known definition ID, prefer the deployment skill's
``delete_workflow.py --id <id>``. This script is the bulk/name-pattern cleaner
that test harnesses call to remove throwaway workflows after a run.

Usage:
    python bin/cleanup_workflows.py --names "wf-1" "wf-2"
    python bin/cleanup_workflows.py --pattern "contain-host-*-run-*"
    python bin/cleanup_workflows.py --all-test
    python bin/cleanup_workflows.py --all-test --dry-run

Flags:
    --names NAME [NAME ...]  Delete specific workflows by exact name
    --pattern GLOB           Delete workflows whose name matches a glob (fnmatch)
    --all-test               Delete workflows matching test conventions
    --dry-run                List what would be deleted; delete nothing

Exit codes:
    0  All targeted deletions succeeded (or dry-run, or nothing matched)
    1  One or more deletions failed, or credentials/API error
    2  Bad arguments
"""

import argparse
import fnmatch
import os
import sys

# Add the shared common/scripts directory to sys.path so auth resolves no
# matter where this script is launched from.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common", "scripts"),
)
try:
    from auth import get_client  # pylint: disable=wrong-import-position
except ImportError:
    get_client = None

# Test naming conventions swept by --all-test:
#   "*-run-*"  — workflows named by run-ab-test.sh / verify-workflows.sh
#   "Test *"   — probe/scratch workflows a model may create while iterating
TEST_PATTERNS = ("*-run-*", "Test *")


def parse_args(argv=None):
    """Parse CLI arguments and enforce that exactly one selector is given."""
    parser = argparse.ArgumentParser(
        description="Delete Falcon Fusion workflows via the delete API",
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--names", nargs="+", metavar="NAME",
        help="Delete specific workflows by exact name",
    )
    selector.add_argument(
        "--pattern", metavar="GLOB",
        help="Delete workflows whose name matches a glob pattern (fnmatch)",
    )
    selector.add_argument(
        "--all-test", action="store_true",
        help="Delete workflows matching test conventions "
             f"({' or '.join(TEST_PATTERNS)})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be deleted without deleting",
    )
    return parser.parse_args(argv)


def matches_selector(name, args):
    """Return True if a workflow name matches the active selector."""
    if args.names:
        return name in args.names
    if args.pattern:
        return fnmatch.fnmatch(name, args.pattern)
    if args.all_test:
        return any(fnmatch.fnmatch(name, p) for p in TEST_PATTERNS)
    return False


def fetch_all_definitions(client):
    """Return all workflow definitions (id + name) with pagination."""
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
    return all_defs


def _select_targets(all_names, args):
    """
    Build the ordered, de-duplicated list of workflow names to delete.

    Starts from the tenant names that match the selector. For --names, also
    surfaces explicitly requested names that aren't present (so they're reported
    as SKIP rather than silently ignored).
    """
    targets = [n for n in all_names if matches_selector(n, args)]
    if args.names:
        for requested in args.names:
            if requested not in targets:
                targets.append(requested)
    seen = set()
    return [n for n in targets if not (n in seen or seen.add(n))]


def _list_definitions():
    """Return all workflow definitions, or raise RuntimeError with a message."""
    if get_client is None:
        raise RuntimeError("could not import auth (check credentials setup).")
    try:
        return fetch_all_definitions(get_client())
    except (ConnectionError, RuntimeError, OSError) as exc:
        raise RuntimeError(f"could not list workflows: {exc}") from exc


def run(args):
    """Resolve the selected names to IDs and delete them. Returns exit code."""
    try:
        definitions = _list_definitions()
    except RuntimeError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    all_names = [d.get("name", "") for d in definitions]
    name_to_ids = {}
    for d in definitions:
        name_to_ids.setdefault(d.get("name", ""), []).append(d.get("id"))

    targets = _select_targets(all_names, args)
    if not targets:
        print("  No workflows matched the selector. Nothing to delete.")
        return 0

    ids = []
    skipped = []
    for name in targets:
        matched = [i for i in name_to_ids.get(name, []) if i]
        if matched:
            ids.extend(matched)
        else:
            skipped.append(name)

    print(f"  Matched {len(targets)} workflow(s):")
    for name in targets:
        state = "SKIP (not found)" if name in skipped else "delete"
        print(f"    [{state}] {name}")

    if args.dry_run:
        print("\n  --dry-run: no workflows were deleted.")
        return 0

    for name in skipped:
        print(f"  — SKIP: {name} (not found)")

    if not ids:
        print(f"\n  Summary: 0 deleted, {len(skipped)} skipped (not found), 0 failed.")
        return 0

    try:
        body = client_delete(ids)
    except RuntimeError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    errors = body.get("errors", [])
    deleted = body.get("resources", [])
    for did in deleted:
        print(f"  ✓ PASS: deleted {did}")
    for err in errors:
        print(f"  ✗ FAIL: {err.get('message', err)}", file=sys.stderr)

    print(
        f"\n  Summary: {len(deleted)} deleted, "
        f"{len(skipped)} skipped (not found), {len(errors)} failed."
    )
    return 1 if errors else 0


def client_delete(ids):
    """Delete definitions by ID; return the response body or raise RuntimeError."""
    try:
        return get_client().delete_definitions(ids=ids)["body"]
    except (ConnectionError, RuntimeError, OSError) as exc:
        raise RuntimeError(f"delete failed: {exc}") from exc


def main():
    """CLI entry point."""
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
