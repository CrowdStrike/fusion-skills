"""
Trigger a CrowdStrike Fusion workflow and optionally wait for results.

Executes an on-demand workflow by definition ID, passing parameters either as a
JSON string (--params) or via interactive prompts derived from the workflow's
parameter schema. With --wait, polls until the execution reaches a terminal
state.

Usage:
    python trigger_workflow.py --id <def_id> --params '{"device_id":"abc123"}'
    python trigger_workflow.py --id <def_id>                     # Interactive parameter prompt
    python trigger_workflow.py --id <def_id> --params '{}' --wait --timeout 120
    python trigger_workflow.py --id <def_id> --autofill --email me@example.com --wait
"""

import argparse
import json
import time
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

# fetch_results and TERMINAL_STATUSES live alongside this script.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from get_execution_results import fetch_results, TERMINAL_STATUSES  # pylint: disable=wrong-import-position

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def get_workflow_params_schema(definition_id):
    """Fetch the parameter schema for a workflow definition."""
    try:
        client = get_client()
        resp = client.search_definitions(filter=f"id:'{definition_id}'")
        body = resp["body"]
        resources = body.get("resources", [])
        if not resources:
            return None
        trigger = resources[0].get("trigger", {})
        return trigger.get("parameters", {}).get("properties", {})
    except (ConnectionError, RuntimeError, OSError):
        return None


def get_trigger_parameters(definition_id):
    """
    Fetch the full On-demand parameter schema (properties AND required list).

    Returns a (properties, required) tuple. Unlike get_workflow_params_schema,
    which returns only the properties for interactive prompting, this exposes the
    JSON-schema `required` array so callers can autofill the mandatory inputs a
    workflow will reject an execution for if omitted.
    """
    try:
        client = get_client()
        resp = client.search_definitions(filter=f"id:'{definition_id}'")
        resources = resp["body"].get("resources", [])
        if not resources:
            return {}, []
        params = resources[0].get("trigger", {}).get("parameters", {})
        return params.get("properties", {}) or {}, params.get("required", []) or []
    except (ConnectionError, RuntimeError, OSError):
        return {}, []


def heuristic_value(field_name, field_schema):
    """
    Derive a schema-valid placeholder value for a required parameter.

    Used by autofill_params to satisfy a workflow's required On-demand inputs
    when no explicit override is supplied. The goal is a value that (a) matches
    the declared JSON-schema type so input validation passes, and (b) is
    semantically plausible for common indicator field names so an enrichment
    workflow has something real to act on. Explicit overrides always win over
    these guesses — see autofill_params.
    """
    ftype = (field_schema or {}).get("type", "string")

    # Non-string types get a minimal schema-valid value regardless of name.
    non_string = {"integer": 1, "number": 1, "boolean": False, "array": [], "object": {}}
    if ftype in non_string:
        return non_string[ftype]

    # String-typed: guess by field name so indicator enrichment gets real input.
    name = field_name.lower()
    # Ordered (substring, value) pairs; first match wins.
    guesses = [
        ("email", "verify@example.com"),
        ("ip", "185.220.101.1"),   # a Tor exit node — returns a real VirusTotal verdict
        ("domain", "example.com"),
        ("url", "https://example.com"),
        ("sha256", "44d88612fea8a8f36de82e1278abb02f"),  # EICAR test-file MD5
        ("hash", "44d88612fea8a8f36de82e1278abb02f"),
    ]
    for needle, value in guesses:
        if needle in name:
            return value
    return "test"


def autofill_params(params, properties, required, overrides=None):
    """
    Fill any required parameters missing from `params`.

    Precedence for each missing required field: an explicit override (from the
    caller) first, then a name/type heuristic. Fields already present in `params`
    are left untouched. Returns a new merged dict; does not mutate the input.
    """
    merged = dict(params or {})
    overrides = overrides or {}
    for name in required:
        if name in merged:
            continue
        if name in overrides:
            merged[name] = overrides[name]
        else:
            merged[name] = heuristic_value(name, (properties or {}).get(name, {}))
    return merged


def prompt_for_params(schema):
    """Interactively prompt the user for each parameter."""
    params = {}
    if not schema:
        print("  No parameter schema found. Enter JSON manually:")
        raw = input("  > ")
        return json.loads(raw) if raw.strip() else {}

    print("\n  Enter parameter values (leave blank for optional fields):\n")
    for field_name, field_schema in schema.items():
        title = field_schema.get("title", field_name)
        ftype = field_schema.get("type", "string")
        desc = field_schema.get("description", "")
        prompt_text = f"  {title} ({ftype})"
        if desc:
            prompt_text += f" — {desc}"
        prompt_text += ": "

        value = input(prompt_text)
        if not value:
            continue

        # Type coercion
        if ftype == "integer":
            value = int(value)
        elif ftype == "boolean":
            value = value.lower() in ("true", "1", "yes")
        elif ftype == "array":
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                # Treat as comma-separated strings
                value = [v.strip() for v in value.split(",")]
        elif ftype == "object":
            value = json.loads(value)

        params[field_name] = value

    return params


def execute_workflow(definition_id, params, depth=1):
    """
    Execute a workflow. Returns (success, execution_id, response_body).
    """
    try:
        client = get_client()
        resp = client.execute(
            definition_id=[definition_id],
            depth=depth,
            body=params,
        )
        body = resp["body"]
        errors = body.get("errors", [])
        if errors:
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            return False, None, msg

        resources = body.get("resources", [])
        # The execute endpoint returns resources as a list of bare execution-ID
        # strings, not objects. Handle both shapes defensively.
        exec_id = None
        if resources:
            first = resources[0]
            exec_id = first if isinstance(first, str) else first.get("id")
        return True, exec_id, body
    except (ConnectionError, RuntimeError, OSError) as exc:
        return False, None, str(exc)


def poll_results(execution_id, timeout=120, interval=5):
    """
    Poll for execution results until a terminal state or timeout.

    Delegates the single-fetch + envelope parsing to fetch_results so this
    script and get_execution_results.py stay in sync on the API response shape.
    Returns the result dict or None on timeout.
    """
    start = time.time()
    print(f"\n  Polling for results (timeout: {timeout}s)...")
    while time.time() - start < timeout:
        ok, msg, result = fetch_results(execution_id)
        if ok and result:
            status = result.get("status", "")
            if status.lower() in TERMINAL_STATUSES:
                return result
            print(f"    Status: {status} ({int(time.time() - start)}s elapsed)")
        else:
            print(f"    Poll error: {msg}")
        time.sleep(interval)

    print(f"  Timeout after {timeout}s — execution may still be running.")
    return None


def main():
    """CLI entry point for workflow execution."""
    parser = argparse.ArgumentParser(description="Trigger a Fusion workflow")
    parser.add_argument("--id", required=True, metavar="DEF_ID", help="Workflow definition ID")
    parser.add_argument("--params", metavar="JSON", help="Execution parameters as JSON string")
    parser.add_argument("--autofill", action="store_true",
                        help="Fill any required trigger params missing from --params "
                             "using name/type heuristics (non-interactive verification)")
    parser.add_argument("--email", metavar="ADDR",
                        help="Override value for email-type required params when --autofill is set")
    parser.add_argument("--wait", action="store_true", help="Poll for execution results")
    parser.add_argument("--timeout", type=int, default=120, help="Poll timeout in seconds (default: 120)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    # Get parameters
    if args.params:
        params = json.loads(args.params)
    elif args.autofill:
        # --autofill is non-interactive: start empty and let autofill (below)
        # populate the required inputs. Do NOT drop into the interactive prompt.
        params = {}
    else:
        # Interactive mode
        schema = get_workflow_params_schema(args.id)
        params = prompt_for_params(schema)

    # Autofill required params the caller did not supply. Used by the verify
    # harness so an On-demand workflow with required inputs (e.g. ip,
    # notify_email) is not rejected at input validation before any action runs.
    if args.autofill:
        properties, required = get_trigger_parameters(args.id)
        overrides = {}
        if args.email:
            overrides = {n: args.email for n in required
                         if "email" in n.lower()}
        params = autofill_params(params, properties, required, overrides)

    print(f"\n  Executing workflow {args.id}")
    print(f"  Parameters: {json.dumps(params, indent=2)}")

    ok, exec_id, resp = execute_workflow(args.id, params)

    if not ok:
        print(f"\n  Execution FAILED: {resp}")
        sys.exit(1)

    print(f"  Execution ID: {exec_id}")

    if args.json and not args.wait:
        print(json.dumps(resp, indent=2))
        return

    # Poll for results
    if args.wait and exec_id:
        result = poll_results(exec_id, timeout=args.timeout)
        if result:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                status = result.get("status", "?")
                print(f"\n  Execution {status}")
                output = result.get("output", {})
                if output:
                    print(f"  Output:\n{json.dumps(output, indent=4)}")
        else:
            print("  No results returned within timeout.")
            sys.exit(1)
    elif not args.wait:
        print("\n  Execution submitted. Use --wait to poll for results.")


if __name__ == "__main__":
    main()
