"""
Fetch results for a CrowdStrike Fusion workflow execution.

Given an execution ID (returned by trigger_workflow.py), retrieve the current
status and output of that execution. This performs a single fetch — use
monitor_execution.py to poll until the execution reaches a terminal state.

Usage:
    python get_execution_results.py --execution-id <exec_id>
    python get_execution_results.py --execution-id <exec_id> --json
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

# Terminal statuses returned by the execution-results API. The API returns
# capitalized values; callers should match case-insensitively. "ActionRequired"
# is terminal for polling purposes — it waits on human input and won't progress
# on its own.
TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "nonrecoverable", "actionrequired"}


def fetch_results(execution_id):
    """
    Fetch the result record for a single execution ID.

    FalconPy returns the standard {body: {resources: [...], errors: [...]}}
    envelope. The execution-results endpoint puts the execution record (status,
    output, etc.) in resources[0]. Returns (success, message, result_dict).
    """
    try:
        client = get_client()
        resp = client.execution_results(ids=execution_id)
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            return False, msg, None

        resources = body.get("resources", [])
        if not resources:
            return False, "No execution record found for that ID", None
        return True, "OK", resources[0]
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, str(exc), None


def main():
    """CLI entry point for fetching execution results."""
    parser = argparse.ArgumentParser(
        description="Fetch results for a Fusion workflow execution"
    )
    parser.add_argument(
        "--execution-id", required=True, metavar="EXEC_ID",
        help="Workflow execution ID to fetch results for",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output",
    )
    args = parser.parse_args()

    ok, msg, result = fetch_results(args.execution_id)

    if not ok:
        if args.json:
            print(json.dumps({"execution_id": args.execution_id, "error": msg}, indent=2))
        else:
            print(f"\n  Could not fetch results: {msg}\n", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    status = result.get("status", "?")
    print(f"\n  Execution {args.execution_id}")
    print(f"  Status: {status}")
    output = result.get("output", {})
    if output:
        print(f"  Output:\n{json.dumps(output, indent=4)}")


if __name__ == "__main__":
    main()
