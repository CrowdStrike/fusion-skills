"""
Release (enable) a CrowdStrike Fusion workflow definition.

In Falcon Fusion, "releasing" a workflow means enabling its definition so the
Fusion engine runs it against new trigger events. A freshly imported definition
is disabled until it is enabled here. This script enables a definition by ID
using the Workflows definition-action endpoint.

Usage:
    python release_workflow.py --id <def_id>            # Enable (release) the definition
    python release_workflow.py --id <def_id> --json     # Machine-readable output
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
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "common", "scripts",
    ),
)
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_client  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def release_workflow(definition_id):
    """
    Enable (release) a workflow definition by ID.

    Calls the Workflows definition-action endpoint with action 'enable'. The
    body carries the definition IDs; FalconPy returns the standard
    {body: {resources: [...], errors: [...]}} envelope.

    Returns (success, message, resources).
    """
    try:
        client = get_client()
        resp = client.workflow_definition_action(
            action_name="enable",
            body={"ids": [definition_id]},
        )
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            return False, msg, None

        resources = body.get("resources", [])
        return True, "OK", resources
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, str(exc), None


def main():
    """CLI entry point for workflow release."""
    parser = argparse.ArgumentParser(
        description="Release (enable) a Fusion workflow definition"
    )
    parser.add_argument(
        "--id", required=True, metavar="DEF_ID",
        help="Workflow definition ID to release (enable)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output",
    )
    args = parser.parse_args()

    ok, msg, resources = release_workflow(args.id)

    if args.json:
        print(json.dumps({
            "id": args.id,
            "released": ok,
            "message": msg,
            "resources": resources or [],
        }, indent=2))
        sys.exit(0 if ok else 1)

    if ok:
        print(f"\n  Released — workflow {args.id} is now enabled.\n")
    else:
        print(f"\n  RELEASE FAILED: {msg}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
