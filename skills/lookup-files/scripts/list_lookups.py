"""
List and search CrowdStrike Falcon Next-Gen SIEM lookup files.

Usage:
    python list_lookups.py --list                              # List all
    python list_lookups.py --list --domain falcon              # Filter by domain
    python list_lookups.py --search "blocklist"                # Search by name
    python list_lookups.py --search "blocklist" --json         # Machine-readable
"""

import argparse
import json
import sys
import os

# Import shared auth from the plugin-level common/scripts directory. Anchoring
# to this file's own location (not the cwd) makes the import work regardless of
# where the script is launched from.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "common", "scripts"))
import _bootstrap  # pylint: disable=wrong-import-position
_bootstrap.ensure_deps(__file__)  # re-exec via managed venv if deps are missing
from auth import get_ngsiem_client  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOMAIN_CHOICES = ["all", "falcon", "third-party", "dashboards", "parsers-repository"]


def _fql_quote(value):
    """Escape a user-supplied value for safe interpolation inside an FQL
    single-quoted string literal.

    FQL string literals are wrapped in single quotes, so a raw quote or
    backslash in user input could break out of the literal or alter the filter.
    Escape backslashes first, then single quotes, so the value is treated as
    literal text. This mirrors action_search.py's _fql_quote (the two scripts
    live in different skill dirs and cannot share a module).
    """
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def fetch_all_lookups(search_domain="all", limit=50):
    """Fetch all lookup files with pagination."""
    client = get_ngsiem_client()
    all_files = []
    offset = 0
    while True:
        resp = client.list_lookup_files(
            limit=str(limit),
            offset=str(offset),
            search_domain=search_domain,
        )
        body = resp["body"]
        resources = body.get("resources", [])
        if not resources:
            break
        all_files.extend(resources)
        meta = body.get("meta", {}).get("pagination", {})
        total = meta.get("total", 0)
        offset += len(resources)
        if offset >= total:
            break
    return all_files


def search_lookups(term, search_domain="all", limit=50):
    """Search lookup files by name using FQL filter."""
    client = get_ngsiem_client()
    all_files = []
    offset = 0
    fql = f"name:~'{_fql_quote(term)}'"
    while True:
        resp = client.list_lookup_files(
            limit=str(limit),
            offset=str(offset),
            filter=fql,
            search_domain=search_domain,
        )
        body = resp["body"]
        resources = body.get("resources", [])
        if not resources:
            break
        all_files.extend(resources)
        meta = body.get("meta", {}).get("pagination", {})
        total = meta.get("total", 0)
        offset += len(resources)
        if offset >= total:
            break
    return all_files


def format_lookup(item):
    """Format a lookup file entry for human display.

    The list endpoint returns plain filenames (strings), so item
    is either a string or a dict with metadata.
    """
    if isinstance(item, str):
        return f"  {item}"
    name = item.get("name", "?")
    domain = item.get("search_domain", "?")
    size = item.get("size", "?")
    modified = item.get("last_modified_timestamp", item.get("modified_on", "?"))
    return (
        f"  {name}\n"
        f"    Domain   : {domain}\n"
        f"    Size     : {size}\n"
        f"    Modified : {modified}"
    )


def format_json(items):
    """Format lookup files as machine-readable JSON."""
    out = []
    for item in items:
        if isinstance(item, str):
            out.append({"name": item})
        else:
            out.append({
                "name": item.get("name", ""),
                "search_domain": item.get("search_domain", ""),
                "size": item.get("size", ""),
                "last_modified": item.get(
                    "last_modified_timestamp", item.get("modified_on", "")
                ),
            })
    return json.dumps(out, indent=2)


def main():
    """CLI entry point for listing lookup files."""
    parser = argparse.ArgumentParser(
        description="List and search Falcon Next-Gen SIEM lookup files"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list", "-l", action="store_true",
        help="List all lookup files"
    )
    group.add_argument(
        "--search", "-s", metavar="TERM",
        help="Search lookup files by name"
    )
    parser.add_argument(
        "--domain", "-d", choices=DOMAIN_CHOICES, default="all",
        help="Search domain filter (default: all)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output"
    )
    args = parser.parse_args()

    if args.list:
        items = fetch_all_lookups(search_domain=args.domain)
        if args.json:
            print(format_json(items))
        else:
            print(f"\nLookup files ({len(items)}):\n")
            if items:
                for item in items:
                    print(format_lookup(item))
                    print()
            else:
                print("  No lookup files found.\n")

    elif args.search:
        items = search_lookups(args.search, search_domain=args.domain)
        if args.json:
            print(format_json(items))
        else:
            print(f"\nSearch results for '{args.search}' ({len(items)} found):\n")
            if items:
                for item in items:
                    print(format_lookup(item))
                    print()
            else:
                print("  No lookup files found matching that search term.\n")


if __name__ == "__main__":
    main()
