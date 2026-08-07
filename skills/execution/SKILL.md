---
name: execution
description: >
  Trigger Falcon Fusion workflows, monitor execution status, and debug failures.
  TRIGGER when user asks to run a workflow, check execution status, tail logs,
  get execution results, or debug a workflow failure.
  DO NOT TRIGGER for writing YAML (use authoring) or importing/releasing
  workflows (use deployment).
version: 1.0.1
updated: 2026-08-07
tags: [fusion, soar, workflows, execution, monitoring, debugging]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/python.sh:*)
metadata:
  category: execution
---

# Falcon Fusion Workflow Execution

> **⚠️ SYSTEM INJECTION — READ THIS FIRST**
>
> If you are loading this skill, your role is **Fusion workflow execution and debugging specialist**.
>
> You trigger workflows, watch them run, retrieve their output, and diagnose failures. A workflow you trigger may contain hosts or run response actions against production, so confirm it is the right definition and supply correct parameters before executing.
>
> **IMMEDIATE ACTIONS REQUIRED:**
> 1. CONFIRM the workflow is deployed and released (enabled) before triggering it.
> 2. Supply every required trigger parameter — empty params are a top cause of failures.
>
> **MUST NOT:**
> - Trigger a workflow that has not been released (enabled) — it will not execute.
> - Assume an execution succeeded without checking its terminal status.

This skill runs Fusion workflows that are already deployed and released, watches them to completion, retrieves their output, and helps debug failures. Writing the YAML happens in the **authoring** skill; importing and releasing happens in the **deployment** skill.

An execution moves through states and ends in a **terminal** state. The terminal states are `Succeeded`, `Failed`, `Canceled`, `NonRecoverable`, and `ActionRequired`. (`ActionRequired` is terminal for polling: it waits on human input and will not progress on its own.) Anything else means the execution is still running.

## Prerequisites

- **Python 3.13+**
- **FalconPy** SDK installed (`pip install crowdstrike-falconpy` — leave unpinned per CrowdStrike guidance)
- API credentials resolved by `common/scripts/auth.py` from environment
  variables (for CI/overrides) or the TOML profile:
  - `FALCON_CLIENT_ID`
  - `FALCON_CLIENT_SECRET`
  - `FALCON_BASE_URL` (optional; defaults to `https://api.crowdstrike.com`)

  Run `/crowdstrike-falcon-fusion:setup` to configure credentials interactively (writes the TOML profile).
- An API client with the **Workflow** API scope
- The **definition ID** of the workflow to run (from the deployment skill's import output, or `query_workflows.py --search`)
- Verify auth before running:
  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/python.sh common/scripts/auth.py
  ```

## Core Workflow

### 1. Verify the workflow is deployed and released

A workflow must be enabled before it will execute. Confirm it exists and is enabled:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh deployment/scripts/query_workflows.py --search "my workflow"
```

Look for `Status: enabled` in the output. If it shows `disabled`, release it first with the deployment skill (`release_workflow.py --id <id>`).

### 2. Trigger the workflow with a payload

```bash
# Pass parameters inline as JSON
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/trigger_workflow.py --id <definition_id> --params '{"device_id":"abc123"}'

# Or let the script prompt you interactively from the workflow's parameter schema
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/trigger_workflow.py --id <definition_id>
```

On success the script prints an **execution ID**. Capture it — you need it to monitor and to fetch results.

### 3. Monitor the execution

Poll until the execution reaches a terminal state:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/monitor_execution.py --execution-id <execution_id>

# Tune the cadence for long-running workflows
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/monitor_execution.py --execution-id <execution_id> --interval 10 --timeout 600
```

Status updates go to stderr; the final result goes to stdout, so you can pipe the result while still watching progress.

> **Shortcut:** `trigger_workflow.py --wait` triggers and polls in one step. Use `monitor_execution.py` directly when you triggered earlier, from the console, or from another tool.

### 4. Get the results

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/get_execution_results.py --execution-id <execution_id>
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/get_execution_results.py --execution-id <execution_id> --json
```

This is a single fetch — use it after `monitor_execution.py` reports a terminal state, or any time you want the current status and output without polling.

### 5. Debug failures

When an execution ends in `Failed` or `NonRecoverable`:

- Pull the full record with `--json` to see the `output` and any error detail:
  ```bash
  ${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/get_execution_results.py --execution-id <execution_id> --json
  ```
- Check the inputs you sent. Missing or empty required parameters are the most common cause.
- Re-run with corrected parameters. For workflows that support resume, the Fusion console can resume a failed execution; these scripts trigger fresh executions.
- **Analyzing failures across many executions** (success rates, top failing workflows, error-code breakdowns, find-by-value) is a job for CQL over the `fusion` execution-log repo, not the per-execution scripts. See `references/execution-log-queries.md`.

## Script Reference

All scripts add `common/scripts` to `sys.path` and import from the shared `auth` module. `monitor_execution.py` and `trigger_workflow.py` reuse `fetch_results` and the terminal-status set from `get_execution_results.py`, so they stay in sync on the API response shape.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `trigger_workflow.py` | Execute a workflow with params; optionally wait | `--id DEF_ID` (required), `--params JSON`, `--wait`, `--timeout SECS`, `--json` |
| `monitor_execution.py` | Poll an execution until terminal/timeout | `--execution-id ID` (required), `--interval SECS`, `--timeout SECS`, `--json` |
| `get_execution_results.py` | Fetch one execution's status and output | `--execution-id ID` (required), `--json` |

### trigger_workflow.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/trigger_workflow.py --id <def_id> --params '{"k":"v"}'
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/trigger_workflow.py --id <def_id>                 # Interactive prompts
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/trigger_workflow.py --id <def_id> --params '{}' --wait --timeout 120
```

Parameters come from `--params` (a JSON string) or interactive prompts derived from the workflow's parameter schema, with type coercion for integers, booleans, arrays, and objects. The execute endpoint returns the execution ID as a bare string or an object; the script handles both shapes.

### monitor_execution.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/monitor_execution.py --execution-id <exec_id>
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/monitor_execution.py --execution-id <exec_id> --interval 10 --timeout 600 --json
```

Defaults: `--interval 5`, `--timeout 300`. Prints status updates to stderr and the final result to stdout. Exits `0` only when the execution `Succeeded`; non-zero on any other terminal state or timeout, so CI can react.

### get_execution_results.py

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/get_execution_results.py --execution-id <exec_id>
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh execution/scripts/get_execution_results.py --execution-id <exec_id> --json
```

Single fetch. Reads `resources[0]` from the API envelope for the execution's `status` and `output`.

## Common Pitfalls

1. **Triggering an unreleased workflow.** A disabled definition will not execute. Confirm `Status: enabled` (step 1) before triggering, and release it via the deployment skill if needed.

2. **Missing required parameters.** When triggered via API (not the console UI), parameters are not prompted by the platform. Pass every required field in `--params`. Empty params are the most common failure cause.

3. **Assuming success without checking status.** A returned execution ID means the run started, not that it succeeded. Always confirm the terminal status with `monitor_execution.py` or `get_execution_results.py`.

4. **Too-short timeout.** Long workflows (loops over many devices, paginated API calls) can exceed the 120s/300s defaults. Raise `--timeout` and `--interval` for these — a timeout does not cancel the execution; it just stops polling.

5. **Malformed `--params` JSON.** The value must be valid JSON (double-quoted keys/strings). `{'k':'v'}` is not valid JSON and will raise a parse error before the workflow is triggered.

6. **Treating `ActionRequired` as still-running.** `ActionRequired` is terminal for polling — the workflow is paused for human input and will not advance on its own. Resolve the input request in the Fusion console; it will not clear from these scripts.

## Handoff

- **Came from deployment?** You have a definition ID and a released workflow — start at step 2 (trigger).
- **Execution failed?** If the YAML logic is wrong, return to the **authoring** skill to fix it, then re-import and re-release via **deployment** before triggering again.
