"""
Monitor a CrowdStrike Fusion workflow execution until it completes.

Polls the execution-results API at a fixed interval until the execution reaches
a terminal state (succeeded, failed, canceled, nonrecoverable, actionrequired)
or the timeout elapses. Status updates are printed to stderr so the final
result on stdout stays clean for piping.

Usage:
    python monitor_execution.py --execution-id <exec_id>
    python monitor_execution.py --execution-id <exec_id> --interval 10 --timeout 600
    python monitor_execution.py --execution-id <exec_id> --json
"""

import argparse
import json
import time
import sys
import os

# fetch_results and TERMINAL_STATUSES live alongside this script; they own the
# auth client and API-response parsing, so this script needs no direct client.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from get_execution_results import fetch_results, TERMINAL_STATUSES  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def monitor(execution_id, interval=5, timeout=300):
    """
    Poll an execution until it reaches a terminal state or times out.

    Status updates are written to stderr. Returns the final result dict when a
    terminal state is reached, or None on timeout. Transient poll errors are
    reported to stderr but do not abort the loop — the execution may still be
    progressing server-side.
    """
    start = time.time()
    print(
        f"  Monitoring execution {execution_id} "
        f"(interval: {interval}s, timeout: {timeout}s)...",
        file=sys.stderr,
    )
    while time.time() - start < timeout:
        ok, msg, result = fetch_results(execution_id)
        elapsed = int(time.time() - start)
        if ok and result:
            status = result.get("status", "")
            print(f"    Status: {status} ({elapsed}s elapsed)", file=sys.stderr)
            if status.lower() in TERMINAL_STATUSES:
                return result
        else:
            print(f"    Poll error ({elapsed}s): {msg}", file=sys.stderr)
        time.sleep(interval)

    print(f"  Timeout after {timeout}s — execution may still be running.", file=sys.stderr)
    return None


def main():
    """CLI entry point for monitoring an execution."""
    parser = argparse.ArgumentParser(
        description="Monitor a Fusion workflow execution until completion"
    )
    parser.add_argument(
        "--execution-id", required=True, metavar="EXEC_ID",
        help="Workflow execution ID to monitor",
    )
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Seconds between status polls (default: 5)",
    )
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="Maximum seconds to wait for a terminal state (default: 300)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output on stdout",
    )
    args = parser.parse_args()

    result = monitor(args.execution_id, interval=args.interval, timeout=args.timeout)

    if result is None:
        if args.json:
            print(json.dumps({"execution_id": args.execution_id, "status": "timeout"}, indent=2))
        sys.exit(1)

    status = result.get("status", "?")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n  Execution {status}")
        output = result.get("output", {})
        if output:
            print(f"  Output:\n{json.dumps(output, indent=4)}")

    # Non-zero exit for non-successful terminal states so callers/CI can react.
    sys.exit(0 if status.lower() == "succeeded" else 1)


if __name__ == "__main__":
    main()
