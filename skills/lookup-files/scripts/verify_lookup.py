"""
Verify a CrowdStrike Falcon Next-Gen SIEM lookup file works end to end.

Uploads a CSV lookup, then runs a CQL query that synthesizes an event carrying a
known value from the file's first data row and joins it back with match(). If the
row comes back, the file is real, correctly formatted, and resolvable by match()
in a search — a far stronger check than a 200 on upload. The probe lookup is
deleted afterward unless --keep is passed.

This models how foundry-sample-anomali-threatstream verifies its lookups: a
createEvents(...) | match(file=...) query with a value known to be present.

Usage:
    python verify_lookup.py --file blocklist.csv                     # upload, verify, delete
    python verify_lookup.py --file blocklist.csv --column ip         # match on a specific column
    python verify_lookup.py --file blocklist.csv --keep --json       # keep the file, JSON output

Requires an API key with NGSIEM Lookup Files (read+write) AND NGSIEM (read+write):
the search that runs match() starts a query job (POST), which needs NGSIEM write.
"""

import argparse
import csv
import json
import sys
import os
import time

# Import shared auth from the plugin-level common/scripts directory. Anchoring
# to this file's own location (not the cwd) makes the import work regardless of
# where the script is launched from.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "common", "scripts"))
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_ngsiem_client  # pylint: disable=wrong-import-position

# Import create/delete helpers from sibling scripts.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from create_lookup import create_lookup  # pylint: disable=wrong-import-position
from delete_lookup import delete_lookup  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The virtual repository that spans all views; match() resolves global-namespace
# lookups here. Search jobs are polled against the same repository.
SEARCH_REPOSITORY = "search-all"


def read_probe_row(file_path, column=None):
    """Return (column_name, known_value) from the CSV's first data row.

    Picks the requested column, or the first column if none is given. The value
    is what the verification query will look up, so it must exist in the file.
    Returns (None, None) if the file has no header or no data rows.
    """
    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
            first = next(reader)
        except StopIteration:
            return None, None

    if not header or not first:
        return None, None

    if column:
        if column not in header:
            return None, None
        idx = header.index(column)
    else:
        idx = 0

    if idx >= len(first):
        return None, None
    return header[idx], first[idx]


def build_match_query(filename, column, value):
    """Build the CQL that synthesizes an event with `value` and joins the lookup.

    createEvents injects one in-query event whose `column` field equals the known
    value; match() with strict=true then only passes rows that resolve against the
    uploaded file, so a returned event proves the join worked.
    """
    event = json.dumps({column: value})
    # Escape for embedding inside the CQL string literal.
    event_literal = event.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'createEvents(["{event_literal}"]) | parseJson() | {column}=* '
        f'| match(file="{filename}", column={column}, field={column}, strict=true)'
    )


def run_match_query(client, query, timeout=60):
    """Start the match() search and poll until done. Returns (ok, events, message).

    ok is True when the query completed. events is the list of returned rows
    (a non-empty list means the lookup matched). On failure ok is False and the
    message explains why.
    """
    started = client.start_search(
        repository=SEARCH_REPOSITORY,
        search={"queryString": query, "start": "1h"},
    )
    if not isinstance(started, dict) or started.get("status_code") not in (200, 201):
        return False, [], f"start_search failed (status {started.get('status_code') if isinstance(started, dict) else '?'})"

    job_id = (started.get("resources") or {}).get("id")
    if not job_id:
        return False, [], "start_search returned no job id"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get_search_status(repository=SEARCH_REPOSITORY, id=job_id)
        body = status.get("resources") or status.get("body") or {}
        if isinstance(body, dict) and body.get("done"):
            return True, body.get("events", []) or [], "query completed"
        time.sleep(2)

    return False, [], f"query did not complete within {timeout}s"


def verify_lookup(file_path, filename=None, column=None, keep=False, timeout=60):
    """Upload, verify via match(), and (unless keep) delete a lookup file.

    Returns (success, message). success is True only when the known probe value
    is returned by a match() query against the uploaded file.
    """
    if filename is None:
        filename = os.path.basename(file_path)

    probe_column, probe_value = read_probe_row(file_path, column)
    if probe_column is None:
        return False, "Could not read a probe value from the CSV (needs a header and one data row)"

    ok, msg = create_lookup(file_path, filename=filename)
    if not ok:
        return False, f"Upload failed: {msg}"

    try:
        client = get_ngsiem_client()
        query = build_match_query(filename, probe_column, probe_value)
        completed, events, run_msg = run_match_query(client, query, timeout=timeout)
        if not completed:
            return False, f"Verification query failed: {run_msg}"
        if not events:
            return False, (
                f"match() returned no rows for {probe_column}={probe_value!r} — "
                "the file uploaded but is not resolvable/joinable by match()"
            )
        return True, (
            f"Verified: match() resolved '{filename}' and returned the probe row "
            f"({probe_column}={probe_value!r})"
        )
    finally:
        if not keep:
            delete_lookup(filename, search_domain="all")


def main():
    """CLI entry point for verifying a lookup file end to end."""
    parser = argparse.ArgumentParser(
        description="Verify a Falcon Next-Gen SIEM lookup file resolves via CQL match()"
    )
    parser.add_argument(
        "--file", "-f", required=True, metavar="FILE",
        help="Local CSV lookup file to verify"
    )
    parser.add_argument(
        "--name", "-n", metavar="FILENAME",
        help="Remote filename (defaults to local filename)"
    )
    parser.add_argument(
        "--column", "-c", metavar="COLUMN",
        help="CSV column to match on (defaults to the first column)"
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Keep the uploaded lookup file instead of deleting it after verification"
    )
    parser.add_argument(
        "--timeout", type=int, default=60, metavar="SECONDS",
        help="Max seconds to wait for the verification query (default: 60)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        msg = f"File not found: {args.file}"
        if args.json:
            print(json.dumps({"success": False, "error": msg}, indent=2))
        else:
            print(f"  ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    remote_name = args.name or os.path.basename(args.file)
    success, message = verify_lookup(
        args.file, filename=remote_name, column=args.column,
        keep=args.keep, timeout=args.timeout,
    )

    if args.json:
        print(json.dumps({
            "success": success,
            "filename": remote_name,
            "message": message,
        }, indent=2))
    else:
        if success:
            print(f"\n  {message}\n")
        else:
            print(f"  FAILED: {message}", file=sys.stderr)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
