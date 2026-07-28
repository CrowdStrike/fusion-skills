"""
Export a CrowdStrike Fusion workflow definition to YAML.

Fetches a deployed workflow definition by ID and writes its console
import/export YAML — the same format the Falcon console produces via
Workflows > (workflow) > Export. Use this to capture a live workflow as a
reproducible artifact, verify what a deployed definition actually contains,
or ground a new example in a real export.

By default the export is PII-sanitized: the platform strips authoring
metadata such as `last_modified_by` / `last_modified_by_user` (which can carry
an author's email) before returning the YAML. Pass --no-sanitize only when you
deliberately need the raw definition and understand it may contain PII.

Usage:
    python export_workflow.py <definition_id>                 # sanitized YAML to stdout
    python export_workflow.py <definition_id> -o wf.yaml      # write to a file
    python export_workflow.py <definition_id> --no-sanitize   # raw (may contain PII)
"""

import argparse
import os
import sys

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


def export_definition(definition_id, sanitize=True):
    """Export one workflow definition as YAML text.

    Returns the export as a decoded string. The Fusion export endpoint returns
    raw YAML bytes (not a JSON envelope), so bytes are decoded directly; a JSON
    error envelope is surfaced as a RuntimeError.
    """
    client = get_client()
    resp = client.export_definition(id=definition_id, sanitize=sanitize)

    # Success returns raw YAML bytes. An error returns a JSON dict envelope.
    if isinstance(resp, (bytes, bytearray)):
        return resp.decode("utf-8", "replace")

    if isinstance(resp, dict):
        status = resp.get("status_code")
        body = resp.get("body", resp)
        if isinstance(body, (bytes, bytearray)):
            return body.decode("utf-8", "replace")
        errors = body.get("errors") if isinstance(body, dict) else None
        raise RuntimeError(
            f"Export failed (status {status}): {errors or body}"
        )

    return str(resp)


def main():
    """CLI entry point for exporting a workflow definition."""
    parser = argparse.ArgumentParser(
        description="Export a Fusion workflow definition to YAML"
    )
    parser.add_argument(
        "definition_id",
        help="The 32-char hex ID of the workflow definition to export",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write the YAML to this file (default: stdout)",
    )
    parser.add_argument(
        "--no-sanitize", dest="sanitize", action="store_false",
        help="Do NOT strip PII/authoring metadata (default: sanitize). "
             "The raw export may contain an author's email.",
    )
    parser.set_defaults(sanitize=True)
    args = parser.parse_args()

    try:
        yaml_text = export_definition(args.definition_id, sanitize=args.sanitize)
    except RuntimeError as exc:
        print(f"  {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        note = "" if args.sanitize else " (raw, not sanitized)"
        print(f"  Exported {args.definition_id} to {args.output}{note}")
    else:
        print(yaml_text)


if __name__ == "__main__":
    main()
