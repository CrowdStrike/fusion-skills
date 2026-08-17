"""
Download or display a CrowdStrike Falcon Next-Gen SIEM lookup file.

Usage:
    python get_lookup.py --name "blocklist.csv"                     # Print to stdout
    python get_lookup.py --name "blocklist.csv" --output file.csv   # Save to file
    python get_lookup.py --name "blocklist.csv" --domain falcon     # Specific domain
"""

import argparse
import sys
import os

# Import shared auth from the plugin-level common/scripts directory. Anchoring
# to this file's own location (not the cwd) makes the import work regardless of
# where the script is launched from.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "common", "scripts"))
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_ngsiem_client  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOMAIN_CHOICES = ["all", "falcon", "third-party", "dashboards", "parsers-repository"]


def get_lookup(filename, search_domain="all"):
    """
    Download a lookup file. Returns the content as a string.
    """
    client = get_ngsiem_client()
    resp = client.get_lookup_file(filename=filename, search_domain=search_domain)

    # FalconPy may return raw bytes for file content
    if isinstance(resp, bytes):
        return resp.decode("utf-8")
    if isinstance(resp, str):
        return resp

    # Dict response — check for errors
    body = resp.get("body", resp) if isinstance(resp, dict) else resp
    if isinstance(body, dict):
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            print(f"  Error: {msg}", file=sys.stderr)
            sys.exit(1)
        # Some endpoints return content in body
        content = body.get("content", body.get("resources", ""))
        if isinstance(content, (bytes, str)):
            return content.decode("utf-8") if isinstance(content, bytes) else content

    return str(resp)


def main():
    """CLI entry point for downloading a lookup file."""
    parser = argparse.ArgumentParser(
        description="Download a Falcon Next-Gen SIEM lookup file"
    )
    parser.add_argument(
        "--name", "-n", required=True, metavar="FILENAME",
        help="Lookup file name to download"
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Save content to file instead of printing"
    )
    parser.add_argument(
        "--domain", "-d", choices=DOMAIN_CHOICES, default="all",
        help="Search domain (default: all)"
    )
    args = parser.parse_args()

    content = get_lookup(args.name, search_domain=args.domain)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Saved to {args.output}")
    else:
        print(content)


if __name__ == "__main__":
    main()
