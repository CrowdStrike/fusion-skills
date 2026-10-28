"""
Import CrowdStrike Fusion workflow definitions via the API.

Validates first (unless --skip-validate), checks for duplicate names
(unless --skip-duplicate-check), then imports. Accepts YAML/JSON files or a
directory (all *.yaml/*.yml inside it are imported). Prints the workflow
definition ID on success.

Usage:
    python import_workflows.py workflow.yaml                         # Validate + dup check + import
    python import_workflows.py --skip-validate workflow.yaml         # Skip validation
    python import_workflows.py --skip-duplicate-check workflow.yaml  # Skip duplicate check
    python import_workflows.py --replace workflow.yaml               # Iterate: delete same-name def, then re-import
    python import_workflows.py *.yaml                                # Multiple files
    python import_workflows.py ./workflows/                          # Every YAML in a directory
"""

import argparse
import glob
import sys
import os
import tempfile

import yaml

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

# The validator lives in the sibling authoring skill. Add its scripts directory
# (../../authoring/scripts) to sys.path so validate_file is importable.
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "..", "..", "authoring", "scripts",
    ),
)
try:
    from validate import validate_file
except ImportError:
    validate_file = None  # Validation unavailable; --skip-validate is implied.

# query_workflows and export_workflow live alongside this script in deployment/scripts.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from query_workflows import fetch_all_definitions  # pylint: disable=wrong-import-position
from export_workflow import export_definition  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def expand_inputs(paths):
    """
    Expand the given paths into a flat list of workflow definition files.

    A directory expands to all *.yaml/*.yml files it directly contains. Plain
    files pass through unchanged. Results are de-duplicated while preserving
    order so the same file is never imported twice.
    """
    files = []
    for path in paths:
        if os.path.isdir(path):
            matched = sorted(
                glob.glob(os.path.join(path, "*.yaml"))
                + glob.glob(os.path.join(path, "*.yml"))
            )
            if not matched:
                print(f"  No *.yaml/*.yml files found in directory: {path}", file=sys.stderr)
            files.extend(matched)
        else:
            files.append(path)

    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for f in files:
        key = os.path.abspath(f)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def extract_name_from_yaml(file_path):
    """Extract the top-level workflow name from a YAML file.

    Parses the YAML rather than matching lines, so quoting and trailing
    comments (``name: "X" # note``) can't produce a name that dodges the
    duplicate check. Returns None if the file is missing, unparseable, or has
    no string ``name``.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def index_by_name(defs):
    """Group definitions by lowercased name: {name_lower: [def, ...]}."""
    index = {}
    for d in defs:
        index.setdefault(d.get("name", "").lower(), []).append(d)
    return index


def check_duplicate(name, existing_names):
    """
    Check if a workflow name already exists (case-insensitive).
    Returns the list of existing definitions with that name (empty if none).
    """
    return existing_names.get(name.lower(), [])


def delete_definition_by_id(wf_id):
    """Delete a definition by ID via the supported delete API.

    Returns (success, message). Used by --replace to remove an existing
    same-name definition before re-importing the corrected YAML, so iterating
    on a workflow yields ONE definition per name instead of a renamed copy per
    attempt. (True update-in-place via the PUT endpoint is not usable — it
    returns an opaque 500 on every payload variant — so delete-then-reimport is
    the supported way to iterate; the tradeoff is a new definition ID each time.)
    """
    try:
        resp = get_client().delete_definitions(ids=[wf_id])
        status = resp.get("status_code")
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            return False, "; ".join(e.get("message", str(e)) for e in errors)
        if status and status >= 400:
            return False, f"API returned status {status}"
        return True, "OK"
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, str(exc)


def import_file(file_path):
    """
    Import a single definition file. Returns (success, message, workflow_id).

    FalconPy returns the new definition in body["resources"]; the API response
    shape is the standard {body: {resources: [...], errors: [...]}} envelope.
    """
    try:
        client = get_client()
        resp = client.import_definition(data_file=file_path)
        status = resp.get("status_code")
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            return False, msg, None
        if status and status >= 400:
            return False, f"API returned status {status}", None

        resources = body.get("resources", [])
        wf_id = resources[0].get("id") if resources else None
        # A 200 with no resources / no id means the API accepted the call but
        # created nothing — treat that as a failure, not a silent success with
        # a null ID. Reporting "Imported — ID: None" as success hides a workflow
        # that never actually landed in the CID.
        if not wf_id:
            return False, "import returned no definition ID (workflow not created)", None
        return True, "OK", wf_id
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, str(exc), None


def _import_single_file(fp, existing_names, skip_validate, replace=False):
    """Import a single file with optional duplicate check and validation.
    Returns (basename, status, wf_id)."""
    basename = os.path.basename(fp)
    print(f"\n  {basename}")

    # Validate before the duplicate check so --replace never deletes the
    # existing definition in favor of a replacement that can't be imported.
    if not skip_validate and validate_file is not None:
        passed, messages = validate_file(fp)
        for m in messages:
            print(f"    {m}")
        if not passed:
            return basename, "VALIDATION FAILED", None

    wf_name = extract_name_from_yaml(fp) if existing_names is not None else None
    backups = []
    if wf_name:
        dups = check_duplicate(wf_name, existing_names)
        exact = [d for d in dups if d.get("name") == wf_name]
        if dups and replace and len(exact) == len(dups):
            # Iterate in place (YAML-only): delete every existing definition with
            # exactly this name, then re-import the corrected YAML below. Yields
            # ONE definition per name instead of a renamed copy per retry.
            # Export each one first: validation can pass and the import still
            # fail server-side, and the backup is how we put the old one back.
            backups = _backup_definitions(exact)
            if backups is None:
                return basename, "REPLACE FAILED", None
            for d in exact:
                dup_id = d.get("id", "?")
                print(f"    REPLACE: deleting existing '{wf_name}' (ID: {dup_id})")
                ok, msg = delete_definition_by_id(dup_id)
                if not ok:
                    print(f"    REPLACE FAILED: could not delete existing definition: {msg}")
                    return basename, "REPLACE FAILED", None
            existing_names.pop(wf_name.lower(), None)
        elif dups:
            for d in dups:
                print(f"    DUPLICATE: '{d.get('name', '?')}' already exists (ID: {d.get('id', '?')})")
            if replace:
                print("    Not replacing — an existing name differs only in case, so it "
                      "may be a different workflow. Delete it explicitly with "
                      "delete_workflow.py --id if it should go.")
            else:
                print("    Skipping — re-run with --replace to iterate in place "
                      "(deletes the old definition, then re-imports). Do NOT "
                      "rename the workflow to dodge this check: a renamed copy "
                      "orphans the existing definition and sprawls the CID.")
            return basename, "DUPLICATE", None

    ok, msg, wf_id = import_file(fp)
    if ok:
        print(f"    Imported — ID: {wf_id}")
        if wf_name and existing_names is not None:
            # Later files in the same batch must see this name as taken.
            existing_names.setdefault(wf_name.lower(), []).append({"id": wf_id, "name": wf_name})
        return basename, "IMPORTED", wf_id
    _report_import_failure(msg, backups)
    return basename, "IMPORT FAILED", None


def _report_import_failure(msg, backups):
    """Explain a failed import and restore anything --replace deleted."""
    print(f"    IMPORT FAILED: {msg}")
    # A 500 is server-side and not fixable by editing the YAML or reimporting.
    # Say so explicitly so the caller stops instead of looping.
    if "500" in msg or "Internal Server Error" in msg:
        print("    This is a server-side error, not a YAML problem. Do not retry "
              "or edit the workflow — report the trace_id and stop.")
    for backup in backups:
        _restore_backup(backup)


def _backup_definitions(defs):
    """Export each definition --replace is about to delete. Returns the YAML
    exports, or None (after printing why) if any export fails."""
    backups = []
    for d in defs:
        try:
            backups.append(export_definition(d.get("id", "?")))
        except (ConnectionError, RuntimeError, OSError) as exc:
            print(f"    REPLACE FAILED: could not back up existing definition "
                  f"(ID: {d.get('id', '?')}) before deleting it: {exc}")
            print("    Nothing was deleted. If that definition is broken and you don't "
                  "need it back, delete it with delete_workflow.py --id, then import "
                  "without --replace.")
            return None
    return backups


def _restore_backup(backup_yaml):
    """Re-import an exported definition that --replace deleted. The restored
    copy gets a new ID. If the restore fails too, keep the export on disk."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", prefix="fusion-replace-backup-", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(backup_yaml)
        path = fh.name
    ok, msg, wf_id = import_file(path)
    if ok:
        os.remove(path)
        print(f"    RESTORED previous version (new ID: {wf_id})")
    else:
        print(f"    RESTORE FAILED: {msg} — backup saved at {path}")


def _print_summary(results):
    """Print import summary and exit with appropriate code."""
    print(f"\n{'─' * 50}")
    imported = [r for r in results if r[1] == "IMPORTED"]
    duplicates = [r for r in results if r[1] == "DUPLICATE"]
    failed = [r for r in results if "FAILED" in r[1]]

    if imported:
        print(f"  Imported ({len(imported)}):")
        for name, _, wf_id in imported:
            print(f"    {name} → {wf_id}")

    if duplicates:
        print(f"  Skipped — duplicate ({len(duplicates)}):")
        for name, _, _ in duplicates:
            print(f"    {name}")

    if failed:
        print(f"  Failed ({len(failed)}):")
        for name, status, _ in failed:
            print(f"    {name}: {status}")

    if failed or duplicates:
        sys.exit(1)


def main():
    """CLI entry point for workflow import."""
    parser = argparse.ArgumentParser(description="Import Fusion workflow definitions")
    parser.add_argument(
        "files", nargs="+", metavar="PATH",
        help="YAML/JSON file(s) or a directory of definitions to import",
    )
    parser.add_argument(
        "--skip-validate", action="store_true",
        help="Skip pre-import validation (NOT recommended — see warning)",
    )
    parser.add_argument("--skip-duplicate-check", action="store_true", help="Skip duplicate name check")
    parser.add_argument(
        "--replace", action="store_true",
        help="If a workflow with the same name exists, delete it then re-import "
             "(iterate in place). Assigns a new definition ID each time.",
    )
    args = parser.parse_args()

    if args.replace and args.skip_duplicate_check:
        parser.error("--replace needs the duplicate check to find the definition "
                     "to replace; drop --skip-duplicate-check")

    if args.skip_validate:
        print(
            "  NOTE: --skip-validate bypasses local validation. An invalid workflow "
            "(e.g. a bad trigger type) then gets rejected by the API late, often as "
            "an opaque HTTP 500 that looks like a server problem but is really a "
            "broken definition. Do not use this flag to get past validation; fix the "
            "workflow and let validation run. It is only for a file already validated "
            "separately (as verify-workflows.sh does).",
            file=sys.stderr,
        )

    files = expand_inputs(args.files)
    if not files:
        print("  No definition files to import.", file=sys.stderr)
        sys.exit(1)

    existing_names = None
    if not args.skip_duplicate_check:
        print("\n  Checking for duplicate workflow names...")
        try:
            all_defs = fetch_all_definitions()
            existing_names = index_by_name(all_defs)
            print(f"    Found {len(all_defs)} existing workflow(s)")
        except (ConnectionError, RuntimeError, OSError) as exc:
            # Fail closed: importing blind can create a duplicate definition.
            print(f"    ERROR: Could not fetch existing workflows: {exc}", file=sys.stderr)
            print("    Nothing was imported. Fix the connection and re-run, or pass "
                  "--skip-duplicate-check to import without the check.", file=sys.stderr)
            sys.exit(1)

    results = [_import_single_file(fp, existing_names, args.skip_validate, args.replace) for fp in files]
    _print_summary(results)


if __name__ == "__main__":
    main()
