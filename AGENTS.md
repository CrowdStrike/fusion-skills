# AGENTS.md

A tool-agnostic guide to the `fusion-skills` plugin for AI coding assistants that are **not** Claude Code (Codex, GitHub Copilot, Antigravity, and others). Claude Code users get the same content via the plugin system and [CLAUDE.md](./CLAUDE.md); this file lets any agent use the skills directly.

## What This Is

`fusion-skills` automates **Falcon Fusion workflow** creation: discover real action IDs from the live API, author workflow YAML, validate it against the platform schema, import it to a CID, and trigger and monitor its execution. It also manages Falcon Next-Gen SIEM lookup files.

A *standalone workflow* runs directly against Fusion with no Falcon Foundry app wrapper. If you need a `manifest.yml`, custom UI, serverless functions, or collections, that is a Foundry app — use the `foundry-skills` plugin instead.

## Prerequisites

- **Python** 3.13+
- **crowdstrike-falconpy** (`pip install crowdstrike-falconpy`)
- **CrowdStrike API credentials** with the **Workflow** scope (plus **NGSIEM Lookup Files** for the lookup-files skill)

Configure credentials with the setup command (writes the TOML profile at `~/.cache/crowdstrike-falcon-fusion/credentials.toml`):

```
/crowdstrike-falcon-fusion:setup
```

For CI or a one-off override, set environment variables instead:

```bash
export FALCON_CLIENT_ID=your_client_id_here
export FALCON_CLIENT_SECRET=your_client_secret_here
# export FALCON_BASE_URL=https://api.crowdstrike.com  # US-1 (default)
```

Verify credentials:

```bash
python common/scripts/auth.py
```

## Repository Structure

```
skills/
  workflows/      Orchestrator skill — SKILL.md decision tree + references/
  authoring/      action_search.py, validate.py, trigger_search.py, examples/
  deployment/     import_workflows.py, release_workflow.py, query_workflows.py, delete_workflow.py
  execution/      trigger_workflow.py, monitor_execution.py, get_execution_results.py
  lookup-files/   list/get/create/update/delete_lookup.py, references/, assets/
  setup/          SKILL.md — interactive credential setup
common/scripts/   auth.py — shared API auth (single source of truth)
use-cases/        Pattern-matchable workflow scenarios (frontmatter + markdown)
hooks/            Claude Code hooks (intent routing, cross-plugin advisory)
```

Each skill's `SKILL.md` is plain markdown with YAML frontmatter — read it directly for the workflow, script reference, and pitfalls. Reference docs live under each skill's `references/` directory.

## Skills Ecosystem

| Skill | Use it to |
|-------|-----------|
| `workflows` | Understand the full lifecycle and routing (read this first) |
| `authoring` | Discover actions, write workflow YAML, validate against the schema |
| `deployment` | Check for duplicates, import to CID, release |
| `execution` | Trigger workflows, monitor runs, debug failures |
| `lookup-files` | Manage Next-Gen SIEM lookup files for CQL `match()` queries |

## Using Without the Claude Code Plugin System

The skills are markdown instructions plus Python scripts — no plugin runtime is required.

1. **Read the SKILL.md** for the task you are doing (e.g., `skills/authoring/SKILL.md` to write a workflow).
2. **Run the scripts directly** with `python <skill>/scripts/<script>.py --help` to see flags. Any absolute or `~/.agents/skills/...` symlink path works — each script resolves its own location and bootstraps its managed Python venv, so `${CLAUDE_PLUGIN_ROOT}` (set only by Claude Code) is not required.
3. **Follow the discipline rules**, which are not optional:
   - Always discover real action IDs with `action_search.py` — never guess. IDs are 32-char hex.
   - Never write `PLACEHOLDER_*` values. Resolve every ID before authoring.
   - Every action needs a `version_constraint`: `~<major>` of its `semantic_version` (`~0` when it declares none).
   - Validate after authoring (`validate.py`) and again as a deploy pre-flight.
   - A workflow must be released before it can execute.
4. **Check `use-cases/`** for a matching pattern before starting — each file documents the trigger, key actions, and steps for a common scenario.

## Lifecycle at a Glance

```
discover actions  →  author YAML  →  validate  →  import  →  release  →  trigger  →  monitor
   (authoring)       (authoring)    (authoring)  (deployment)        (execution)
```

Carry the artifacts forward: authoring produces a validated YAML file, deployment produces a `definition_id`, execution produces an `execution_id`. Do not start a phase before the previous one succeeds.

## Core Principles

These rules apply to every task and are not optional:

- **Discover real action IDs — never invent them.** Run `skills/authoring/scripts/action_search.py` against the live activities catalog to resolve a real 32-char hex ID for every action. Guessing an ID, or shipping a `PLACEHOLDER_*` value, produces a workflow that fails to import or wires the wrong action into a response.
- **Validate before deploy.** Run `skills/authoring/scripts/validate.py` after authoring, and again as a deploy pre-flight (`skills/deployment/scripts/import_workflows.py` does this by default). Catch schema, action-ID, and `version_constraint` errors locally instead of at the API.
- **Standalone-workflow-first.** Produce a standalone Fusion workflow that runs directly against a CID. Reach for a Foundry app (`manifest.yml`, UI, functions, collections) only when the request genuinely needs one — then route to `foundry-skills`.
- **Resolve credential config IDs; do not invent them.** HTTP Actions and plugin actions reference a `config_id` created in the Falcon console and specific to the CID. Discover an existing one or ask the user where to find it — a fabricated config ID fails at runtime.

## Skills Usage Patterns

### Starting a New Workflow

Follow the lifecycle in order, carrying each artifact to the next phase:

```bash
# 1. Discover real action IDs (authoring)
python skills/authoring/scripts/action_search.py --search "contain"
python skills/authoring/scripts/action_search.py --details <action_id>   # inputs, class, version_constraint

# 2. Author the YAML, then validate it (authoring)
python skills/authoring/scripts/validate.py workflow.yaml
python skills/authoring/scripts/validate.py --preflight-only workflow.yaml  # no API call

# 3. Import to the CID (deployment) — validates + checks duplicates by default
python skills/deployment/scripts/import_workflows.py workflow.yaml          # prints the definition_id

# 4. Release (enable) the definition once tested (deployment)
python skills/deployment/scripts/release_workflow.py --id <definition_id>

# 5. Trigger and monitor (execution)
python skills/execution/scripts/trigger_workflow.py --id <definition_id> --params '{"device_id":"abc123"}' --wait
```

Pick the trigger type with `skills/authoring/scripts/trigger_search.py --list` (valid types: On demand, Signal, Scheduled, SubModel).

### Working with an Existing Workflow

There is **no export script** — the Workflows API (via FalconPy) exposes no workflow-download endpoint. Treat the local YAML as the source of truth:

```bash
# Find the deployed definition and confirm its state
python skills/deployment/scripts/query_workflows.py --search "contain"
python skills/deployment/scripts/query_workflows.py --check-name "Contain Host on Detection"

# Edit the local YAML, re-validate, and re-import
python skills/authoring/scripts/validate.py workflow.yaml
python skills/deployment/scripts/import_workflows.py workflow.yaml
```

Re-importing a name that already exists is flagged by the duplicate check; rename in the YAML or delete the existing definition in the console first. A re-imported definition is disabled until you release it again. To stop a running execution, use `skills/execution/scripts/monitor_execution.py` and `get_execution_results.py` to inspect status.

### Common Scenarios

- **HTTP Action enrichment** — call an external REST API inline (no Foundry app). See [use-cases/http-actions.md](./use-cases/http-actions.md) and, for paging large results, [use-cases/api-pagination.md](./use-cases/api-pagination.md).
- **Scheduled Event Query** — run a schemaless CQL/FQL query against the event store on a schedule. See [use-cases/event-queries.md](./use-cases/event-queries.md).
- **Lookup-file enrichment** — enrich detections with third-party data via a Next-Gen SIEM lookup table and CQL `match()`. See [use-cases/lookup-enrichment.md](./use-cases/lookup-enrichment.md) and the `lookup-files` skill.

## Quality and Thoroughness

Quality matters more than speed. Specifically:

- **Validate everything.** Run `validate.py` after authoring and rely on the import pre-flight; do not push unvalidated YAML to the API.
- **No placeholders.** Every action `id` must be a real 32-char hex value resolved via `action_search.py`. A `PLACEHOLDER_*` string in output YAML means a step was skipped — go back and resolve it.
- **Test before declaring done.** A returned `definition_id` means imported, not working. Release it, trigger it with real parameters, and confirm the execution reached a terminal `Succeeded` state with `monitor_execution.py` before calling the task complete.
- **Read each skill's Common Pitfalls section** before working in that phase.

## Security Considerations

- **Never log credentials.** Credentials load from environment variables or the TOML profile (see Prerequisites) — never hardcode them, echo them, or commit them. The setup script reads the secret with masked input and writes a `600`-permission file.
- **The console-credential boundary.** A workflow can be authored outside the console, but any `config_id` / `definition_id` / `config_name` it references must already exist in the target CID (created in the Falcon console). Discover existing config IDs; do not invent them. Document the dependency when the config does not yet exist.
- **Input validation.** Workflows that trigger response actions (contain host, disable account) act on real assets. Validate trigger parameters and guard against empty/missing required params, which are a top cause of failed or misdirected executions.
- **`version_constraint` discipline.** Every action needs a `version_constraint`: the tilde range for the major component of its `semantic_version` (`~1` for `1.x.y`, `~0` for `0.x.y` or when no version is declared, `~2` for `2.x.y`). Confirm with `action_search.py --details` rather than assuming `~1` — many actions sit at major version 0.

## Cross-Plugin Boundary

Route to `foundry-skills` (the `crowdstrike-falcon-foundry` plugin) when the work needs a Foundry app: UI pages, serverless functions, collections, or a `manifest.yml`. Stay in `fusion-skills` for standalone workflows, HTTP Actions backed by a console credential config, and live action discovery.
