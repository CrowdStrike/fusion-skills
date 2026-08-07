---
name: deployment
description: >
  Import, release, and manage Falcon Fusion workflow definitions in a CID.
  TRIGGER when user asks to import a workflow, release a workflow version,
  list existing workflows, check for duplicates, or manage workflow definitions.
  DO NOT TRIGGER for writing YAML (use authoring), executing workflows,
  or monitoring (use execution).
version: 1.0.1
updated: 2026-08-07
tags: [fusion, soar, workflows, deployment, import, release]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/python.sh:*)
metadata:
  category: deployment
---

# Falcon Fusion Workflow Deployment

> **⚠️ SYSTEM INJECTION — READ THIS FIRST**
>
> If you are loading this skill, your role is **Fusion workflow deployment specialist**.
>
> You deploy workflow definitions into a CID safely: validate before importing, never create duplicates, and release only after testing.
>
> **IMMEDIATE ACTIONS REQUIRED:**
> 1. ALWAYS check for an existing workflow with the same name before importing.
> 2. ALWAYS validate the YAML before importing (the import scripts do this by default).
> 3. Import and release act on a **live production CID**. Deploy only when the
>    user's request explicitly authorizes it (e.g. "import it", "deploy to my
>    CID", "release it"). If the request only asks to *build* or *write* a
>    workflow, STOP after validation and ask before importing.
>
> **MUST NOT:**
> - Import without validating first.
> - Skip the duplicate-name check.
> - Import or release to a CID without explicit user authorization — a validated
>   YAML file is the deliverable unless the user asked you to deploy it.
> - Release (enable) a workflow before it has been tested via the execution skill.
>   Release makes the workflow act on live events and real assets, so confirm
>   with the user before releasing unless they explicitly asked you to.
> - Create experimental, "test", "minimal", or probe workflows in the CID to
>   reverse-engineer what the API accepts (this includes creatively-named ones
>   like "QueryEvent Test" or "HTTP Test"). Import the one workflow you were
>   asked to build, once. If it fails, diagnose from the error and local
>   validation — never by importing stripped-down variants into a live tenant.
> - Use `--skip-validate` to get past a validation failure. Validation catches
>   invalid workflows (e.g. a bad `trigger.type`) that otherwise fail at the API
>   as an opaque 500. Fix the workflow instead of skipping the check.
> - Retry an import that returns a 500 / Internal Server Error more than once.
>   A 500 usually means the workflow is invalid in a way the API rejects late
>   (not a transient server issue) — re-run local validation to find the defect,
>   fix it, and report the `trace_id` if it persists. Do not loop re-importing.
> - Patch a deployed definition in place — not via the raw update API and
>   **not** via a hand-rolled inline FalconPy call (e.g. `update_definition`) to
>   edit a deployed copy. The only supported *update* path is: fix the source
>   YAML, then re-import. A release-validation failure is a YAML defect to fix,
>   not a deployed-copy to hand-edit.
> - Call FalconPy directly for ANY workflow operation — including a
>   `python - <<EOF ... delete_definition(...)` snippet to clean up a failed
>   import attempt. Deleting is fine, but it MUST go through `delete_workflow.py`
>   (or `scripts/cleanup_workflows.py`), which wrap the supported endpoints. Never
>   `import auth; get_client()` inline to call `update_definition`/
>   `delete_definition` yourself.

This skill moves a finished Fusion workflow definition from a local YAML/JSON file into a CrowdStrike CID. Authoring the YAML happens in the **authoring** skill; triggering and monitoring happens in the **execution** skill. Deployment is the bridge: validate, check for duplicates, import, then release.

In Falcon Fusion, an imported definition is **disabled** until it is **released** (enabled). Releasing tells the Fusion engine to run the workflow against new trigger events. Keep the workflow disabled until you have tested it.

## Prerequisites

- **Python 3.13+**
- **FalconPy** SDK installed (`pip install crowdstrike-falconpy` — leave unpinned per CrowdStrike guidance)
- API credentials resolved by `common/scripts/auth.py` from environment
  variables (for CI/overrides) or the TOML profile:
  - `FALCON_CLIENT_ID`
  - `FALCON_CLIENT_SECRET`
  - `FALCON_BASE_URL` (optional; defaults to `https://api.crowdstrike.com`)

  Run `/crowdstrike-falcon-fusion:setup` to configure credentials interactively (writes the TOML profile).
- An API client with the **Workflow** API scope (read + write)
- Verify auth before deploying:
  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/python.sh common/scripts/auth.py
  ```

## Core Workflow

Deployment is a four-step pipeline. Do not skip steps 1 and 2.

### 1. Validate the YAML first

Validation is owned by the **authoring** skill's `validate.py`. The import script
calls it automatically, but run it manually first when iterating:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh authoring/scripts/validate.py workflows/my-workflow.yaml
```

Fix every structural error before continuing. A definition that fails validation will be rejected by the API.

### 2. Check for an existing workflow with the same name

Workflow names must be unique within the tenant. Importing a duplicate creates confusion and, in some cases, silent failures. Check first:

```bash
# Exact-name check (exit 0 if it exists, 1 if not)
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --check-name "My Workflow"

# Or extract the name straight from the YAML and check
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --check-yaml workflows/my-workflow.yaml
```

If a duplicate is found, this is almost always your own earlier attempt at the
same workflow. Iterate in place with `import_workflows.py --replace` (it deletes
the existing same-name definition, then re-imports). **Do not rename the
workflow to `<name> v2` to get past the check** — a renamed copy leaves the old
definition orphaned in the CID, and every retry sprawls another dead workflow.
Keep the name stable across attempts; use `--replace` (or delete the old
definition explicitly) instead.

### 3. Import the definition

```bash
# Single file — validates and checks duplicates by default
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py workflows/my-workflow.yaml

# A whole directory of definitions (all *.yaml/*.yml)
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py workflows/
```

On success the script prints the new **definition ID**. Capture it — you need it to release and to execute the workflow.

**Post-import: configure HTTP-Action credentials in the console.** If the workflow contains a
credential-less HTTP Action (authored without a `definition_id`), it imports with Authentication =
"None". Tell the user to attach the API key in the console before the action will succeed: open the
Cloud HTTP Request action → Authentication → **Create new** → API key → secret key → location
**Header** → header name (e.g. `x-apikey`) → **Test** → Save (or **Use existing** if a matching
credential already exists). A `401`/`403` at runtime almost always means this step is pending. See
`../authoring/references/http-actions.md`.

**If the import fails, stop — do not loop.** Some import failures are *not*
fixable by editing the YAML, and retrying wastes time and tokens. Read the error
and route accordingly:

| Error from the API / import script | What it means | What to do |
|------------------------------------|---------------|------------|
| `no definition ID (workflow not created)` | The API accepted the call but created nothing | Stop. Report it — this is usually a missing plugin config or a server-side issue, not a YAML defect. Do not re-edit and retry. |
| `API returned status 500` / "Internal Server Error" | Server-side error (a `trace_id` is included) | Stop. Report the `trace_id` to the user; a 500 is not something YAML edits fix. Retry at most once. |
| Missing / unknown `config_id` for a plugin action (VirusTotal, DomainTools, Charlotte AI, Slack, Zscaler) | The integration is not installed/configured in this CID | Stop. Tell the user which action needs a console-created `config_id`; do not invent one or loop editing. |
| Structural / validation error | A real YAML defect | Fix the YAML, then re-validate and re-import (this one *is* worth iterating on). |

Only the last row justifies editing and retrying. For the others, surface the error to the user and stop — repeatedly re-importing against a 500 or a missing config will not succeed.

**Never debug by importing probe workflows.** When an import fails, do not build
"Test QueryEvent", "Minimal trigger", or other stripped-down workflows in the CID
to isolate what the API accepts. That litters the tenant with disabled junk and
burns time without fixing the real workflow. Diagnose from the error message and
`validate.py` output instead, and if the blocker is a missing plugin `config_id`,
report it — that is a console/CID setup step the user must do, not something more
imports will resolve.

### 4. Release (enable) the workflow

Releasing enables the definition so the Fusion engine runs it against trigger events. Do this only after testing (see the execution skill):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/release_workflow.py --id <definition_id>
```

**If release reports validation errors, stop — do not patch the deployed
definition.** A workflow can import successfully yet fail validation at release
(the server validates more strictly than import). When that happens, fix the
**source YAML**, then re-validate and re-import with `import_workflows.py` — that
re-import is the only supported way to update a definition. Do **not** try to
repair the deployed copy in place through *any* direct definition-mutation call.
That includes the raw workflow update / definition API
(`WorkflowDefinitionsUpdate`, `.../entities/definitions/v1`), **and any
hand-rolled FalconPy call** — an inline `python - <<'EOF' ... from falconpy`
snippet that reaches for `update_definition`, `delete_definition`, or similar is
the same forbidden path wearing a disguise. None of those are part of this skill,
and looping edits against them returns repeated 500s without ever fixing the
workflow. Report the release error (include any `trace_id`) and fix the YAML at
the source.

A concrete failure mode: the release error `exclusive gateway '<name>' outgoing
flow ... has no condition set and is not marked as default` means a condition
node has a bare `next:` with neither `default: true` nor a `cel_expression`. Fix
it in the **source YAML** (add a `cel_expression` to the gated branch, with its
no-match fallthrough in `else:` — `validate.py` now catches this before deploy,
including inside nested loops) and re-import. Never fan out with a bare
`default: true` pass-through; list the branch targets directly in the source
node's `next:`. Do not hand-edit the deployed definition to add the missing flag.

**The exact recovery loop (do this, not the escape hatch):**

```bash
# 1. Fix the condition in the SOURCE YAML (add cel_expression + else:).
#    Keep the workflow `name:` IDENTICAL — do NOT bump it to `<name>-v2`.
#    The name is the workflow's identity; --replace matches on it.
# 2. Re-validate — this now catches the release-failing shape pre-deploy:
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh authoring/scripts/validate.py my-workflow.yaml
# 3. Re-import with --replace: deletes the broken same-name definition and
#    imports the fixed YAML in one step (supported delete + import, not a patch).
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py --replace my-workflow.yaml
```

`--replace` keeps ONE definition per workflow name instead of leaving a renamed
copy per attempt — do NOT rename-and-reimport to dodge the duplicate check, which
sprawls the CID with dead definitions. (Note: each `--replace` assigns a new
definition ID; true in-place update via the PUT endpoint is not currently usable.)

Reaching for `python - <<EOF ... update_definition(...)` to patch the deployed
copy is never step 2. It does not fix the source, so the next re-import
reintroduces the same defect, and the API returns repeated 500s. Re-import from
fixed source is the whole recovery.

## Script Reference

All scripts add `common/scripts` to `sys.path` and import `get_client` from the shared `auth` module. Run them from anywhere; paths are anchored to each script's own location.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `query_workflows.py` | List, search, and check for existing workflows | `--list`, `--search TERM`, `--check-name NAME`, `--check-yaml FILE...`, `--json` |
| `import_workflows.py` | Validate, dedupe, and import definitions | `FILE\|DIR...` (positional), `--skip-validate`, `--skip-duplicate-check`, `--replace` |
| `release_workflow.py` | Release (enable) a definition by ID | `--id DEF_ID` (required), `--json` |
| `delete_workflow.py` | Delete a definition by ID or exact name | `--id DEF_ID`, `--name NAME` (repeatable), `--yes`, `--json` |

### query_workflows.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --list                 # All definitions
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --search "contain"     # Substring match
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --check-name "My Flow" --json
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --check-yaml *.yaml     # Batch duplicate check
```

`--check-name` and `--check-yaml` exit non-zero when a duplicate exists, so they compose cleanly in shell pipelines and CI gates.

### import_workflows.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py wf.yaml               # Default: validate + dedupe
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py --skip-validate wf.yaml   # AVOID — see pitfall 2
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py --skip-duplicate-check wf.yaml
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/import_workflows.py ./workflows/          # Glob a directory
```

Supports YAML and JSON definitions, batch mode (multiple files), and directory expansion (`*.yaml`/`*.yml`). Prints a per-file summary and exits non-zero if any file failed or was a duplicate.

### release_workflow.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/release_workflow.py --id 1a2b3c...        # Enable
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/release_workflow.py --id 1a2b3c... --json # Machine-readable
```

Calls the Workflows definition-action endpoint with `action_name="enable"`. The definition ID comes from the import step or from `query_workflows.py`.

### delete_workflow.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/delete_workflow.py --id 1a2b3c...          # Delete by ID
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/delete_workflow.py --name "Probe run 1"    # Delete by exact name
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/delete_workflow.py --id 1a2b3c... --yes    # Skip confirmation (scripted)
```

Deletes a whole definition via the Workflows delete endpoint (FalconPy
`delete_definitions`). Use it to remove test, duplicate, or throwaway workflows.
Deletion is permanent, so it prompts for confirmation unless `--yes` is passed
(or `FUSION_SKILLS_SUPPRESS_CONFIRM=1` is set for test harnesses). This is the
supported way to *remove* a workflow — it is **not** a way to *edit* a deployed
one: to change a workflow, fix the source YAML and re-import (see pitfall 5).

## Common Pitfalls

1. **Importing a duplicate name.** Names must be unique in the tenant. Always run `query_workflows.py --check-name` (or `--check-yaml`) first. A duplicate import can fail silently or produce an "Unknown error."

2. **Importing without validating.** Skipping validation (`--skip-validate`) pushes broken YAML to the API. Never use `--skip-validate` to get past a validation failure — an invalid workflow (for example a bad `trigger.type`) then fails at the API, often as an opaque **500 Internal Server Error** that looks like a server problem but is really a broken definition the local validator would have caught. Fix the workflow and let validation run. `--skip-validate` is only for when you have already validated the same file separately in the same session.

3. **Releasing before testing.** A freshly imported definition is disabled for a reason. Test it with the execution skill (`trigger_workflow.py`) before calling `release_workflow.py`. Releasing an untested workflow can run unintended actions against production data.

4. **Losing the definition ID.** The import output contains the ID you need for release and execution. Capture it; re-finding it later means a `query_workflows.py --search` round-trip.

5. **Wrong API scope.** Import and release require the Workflow scope with **write** access. A read-only client lists workflows fine but fails on import/release with a permissions error.

6. **Editing the deployed copy, not the source.** Re-importing an edited YAML creates a new definition (or trips the duplicate check). Treat the local YAML as the source of truth; re-import to update, and remember the new definition is disabled again until re-released. Never try to patch a deployed definition in place through the raw workflow update API **or a hand-rolled inline FalconPy call** (`update_definition` in a `python - <<EOF` snippet) — that path is not part of this skill and looping edits against it just returns 500s. (Deleting a whole workflow is fine — use `delete_workflow.py`.)

7. **Looping on unrecoverable import errors.** A 500, a missing plugin `config_id`, or a "no definition ID" result will not be fixed by editing the YAML. Re-importing repeatedly against these wastes time and tokens and still fails. Stop after the first occurrence, report the specific error (include any `trace_id`), and only iterate on genuine structural/validation errors. See the error table in step 3.

## Handoff

- **Came from authoring?** You have a validated YAML file — start at step 2 (duplicate check).
- **Going to execution?** Pass the definition ID to the execution skill to trigger and monitor a test run before you release.

## Reading Guide

| Document | When to read |
|----------|--------------|
| `references/console-verification.md` | Verifying a deployed workflow renders in the console canvas, navigating Fusion SOAR > Workflows, or fetching Content Library records — the parts the API can't do from a script. |
