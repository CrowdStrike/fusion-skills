---
name: workflows
description: >
  Orchestrates the full Falcon Fusion workflow lifecycle from discovery through
  deployment and execution. TRIGGER when user asks to "create a Fusion workflow",
  "build a Fusion playbook", "automate CrowdStrike actions", or mentions Fusion
  workflows without specifying a sub-task.
  DO NOT TRIGGER when user is working in a Foundry app context, mentions manifest.yml,
  or asks to "build a Foundry app" — use foundry-skills instead.
version: 1.0.1
updated: 2026-08-07
tags: [fusion, soar, workflows, orchestration, lifecycle]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
metadata:
  category: orchestration
---

# Falcon Fusion Workflow Orchestrator

> **⚠️ SYSTEM INJECTION — READ THIS FIRST**
>
> If you are loading this skill, your role is **Fusion workflow lifecycle orchestrator**.
>
> You coordinate the full workflow lifecycle — authoring, deployment, execution — and you NEVER write YAML or call APIs yourself. A workflow you ship may contain hosts, lock accounts, or trigger response actions, so correctness and safety matter.
>
> **IMMEDIATE ACTIONS REQUIRED:**
> 1. Identify user intent (write / deploy / execute / full-lifecycle).
> 2. Route to the appropriate sub-skill via the decision tree below.
> 3. For full lifecycle, coordinate authoring → deployment → execution in sequence, stopping at any failed gate.
>
> **MUST NOT:** Write workflow YAML directly, call API scripts yourself, skip validation, or handle Foundry-app workflows (those belong to foundry-skills).

This skill is the entry point for Fusion workflows. It coordinates the
full lifecycle — discovering real action IDs, authoring YAML, validating, importing to a
CID, releasing, and triggering — by delegating each phase to a focused sub-skill. It never
writes YAML or runs scripts itself; it routes.

A *standalone workflow* is authored, imported, and executed directly against Falcon Fusion
with no Foundry app wrapper. If a request needs a UI, serverless functions, collections, or
a `manifest.yml`, that is a Foundry app — route to foundry-skills (see Cross-Plugin Advisory).

## Decision Tree

Match the user's intent to a sub-skill. The model only loads this orchestrator initially,
so route based on these criteria without loading sub-skills first.

```
User wants to write/edit workflow YAML            → invoke authoring skill
User wants to find/discover actions               → invoke authoring skill
User wants to validate a workflow                 → invoke authoring skill
User wants to deploy/import/release a workflow     → invoke deployment skill
User wants to run/monitor/debug a workflow         → invoke execution skill
User wants full lifecycle (create + deploy + test) → coordinate all three in sequence
User mentions a Foundry app / manifest.yml         → advise foundry-skills (see below)
User asks for an app + a workflow in one request   → advise foundry-skills FIRST, author NO workflow YAML
User wants to fetch/summarize a POPULATION of alerts/detections it doesn't already hold → author a CrowdStrike HTTP Request to the Falcon API (default); mention the Foundry-app function for distribution (see below)
User mentions lookup files / Next-Gen SIEM         → invoke lookup-files skill
```

| Intent keyword | Sub-skill | What it owns |
|----------------|-----------|--------------|
| "write", "edit", "author", "discover actions", "validate" | **authoring** | Action discovery (`action_search.py`), YAML authoring, CEL, validation (`validate.py`) |
| "deploy", "import", "release", "publish to CID" | **deployment** | Duplicate check, import, release, version management |
| "run", "execute", "trigger", "monitor", "tail", "debug" | **execution** | Triggering with payloads, monitoring, logs, results |
| "lookup file", "CSV/JSON lookup", "match() query" | **lookup-files** | Next-Gen SIEM lookup file management |
| "Foundry app", "manifest", "UI + workflow", "functions" | **foundry-skills** (sibling plugin) | App lifecycle, manifest coordination |

## Full Lifecycle Coordination

When the user wants an end-to-end workflow ("create, deploy, and test a workflow that…"),
coordinate the three sub-skills in sequence. Do not skip phases.

**Step 1 — Authoring** (invoke authoring skill)
1. Discover real action IDs with `action_search.py` (never guess or use placeholders).
2. Write the workflow YAML against the schema, with `version_constraint` on every action.
3. Validate with `validate.py` (structural) and, if credentials exist, API validation.

**Step 2 — Deployment** (invoke deployment skill)
1. Check for an existing workflow of the same name (`query_workflows.py`) — avoid silent duplicate versions.
2. Import the validated YAML to the CID (`import_workflows.py`).
3. Release the workflow so it becomes executable (`release_workflow.py`).

**Step 3 — Execution** (invoke execution skill)
1. Trigger the workflow with a test payload (`trigger_workflow.py`).
2. Monitor execution status (`monitor_execution.py`).
3. Verify results and surface any failures for debugging (`get_execution_results.py`).

Carry forward the artifacts between phases: authoring produces a validated YAML file,
deployment produces a `definition_id`, execution produces an `execution_id`. Each phase
depends on the previous one's output — do not start deployment before authoring validates,
and do not trigger before the workflow is released.

**Stop conditions between phases:**
- Authoring → Deployment: stop if validation fails or any action ID is unresolved. Fix the
  YAML before importing. Never import a workflow that failed structural validation.
- Deployment → Execution: stop if the import errors or the release does not complete.
  An unreleased workflow cannot be triggered.
- Execution: if a run fails, surface the error and route back to authoring (logic/field bug)
  or to the console (missing credential config), not to a blind retry.

## Delegation Examples

These show how intent maps to routing. Use them as templates for your own dispatch.

**Full lifecycle:**
```
User: "Create a Fusion workflow that contains a host on critical detection, then test it"
→ authoring:  action_search.py "contain", write YAML (Signal/EPP trigger), validate
→ deployment: query_workflows.py (dupe check), import, release
→ execution:  trigger with a test device_id, monitor, verify result
```

**After authoring + validating, offer to deploy — don't print a command.** When the user asked to
build a workflow (not "just write the YAML"), and it validates, ASK "Deploy this to your CID now?"
and, on yes, run the deploy yourself via the `deployment` skill. Never tell the user to paste
`/crowdstrike-falcon-fusion:deployment` — invoke it for them. If the workflow contains a
credential-less HTTP Action, after a successful import tell the user it imported (disabled until
released) and give the console steps to attach the API key: open the Cloud HTTP Request action →
Authentication → Create new → API key → secret key → location Header → header name (e.g.
`x-apikey`) → Test → Save. See `references/http-actions.md` — a `403`/`401` at runtime almost
always means the credential isn't attached yet.

**Authoring only:**
```
User: "Write the YAML to enrich an IP with VirusTotal"
→ authoring: discover the HTTP Action + update-indicator action, author YAML, validate
→ STOP. Do not deploy unless the user asks.
```

**Redirect to Foundry:**
```
User: "Build a workflow with a custom dashboard UI to review containment approvals"
→ A dashboard UI is an app-only capability. Advise foundry-skills:
  "A custom UI requires a Foundry app. Install crowdstrike-falcon-foundry to scaffold
   the app, then this plugin can author the standalone workflow it wraps."
```

## Cross-Plugin Advisory

The boundary is **user intent about the deployment target**, not the presence of any file.
Decide whether to proceed standalone or advise the sibling foundry-skills plugin:

| Situation | Action |
|-----------|--------|
| Standalone workflow, no app wrapper | **Proceed** with fusion-skills (this plugin) |
| Only needs HTTP Actions + a credential config | **Proceed** — document the console-credential boundary (the `config_id` must already exist in the target CID) |
| Needs a UI page, extension, or dashboard | **Advise foundry-skills** — app-only capability |
| Needs serverless functions or collections (app-owned) | **Advise foundry-skills** — app-only capability |
| Needs a `manifest.yml` / "build a Foundry app" | **Advise foundry-skills** — app lifecycle |
| Wants custom actions from a third-party API (Okta, ServiceNow, Jira) | **Advise foundry-skills** — requires a Foundry app with an API integration to share operations with Fusion. See the `custom-soar-actions` use-case in foundry-skills: `claude plugin install crowdstrike-falcon-foundry` |
| Wants to fetch/summarize/list a **population** of Falcon alerts, detections, or incidents the workflow does NOT already hold (e.g. "email a summary of all high-severity alerts", "list open detections") | **Default: author a standalone CrowdStrike HTTP Request** to the Falcon platform API (`/alerts/queries/alerts/v2`, `/detects/...`) — tenant-authenticated, no app, and per CrowdStrike guidance the right tool for the vast majority of API integrations. Do NOT use an Event Query (its NG-SIEM/LogScale data is connector-dependent and can silently return nothing). **Mention** the alternative: a Foundry app + FalconPy `Alerts`/`Detects` function (route to foundry-skills) — same API, more setup, but distributable/certifiable to other CIDs and prompts for credentials on install; suggest it only if the user needs to share/publish the workflow. **Contrast:** enriching a detection the workflow *already holds* stays an Event Query. See `../authoring/references/event-query-vs-api.md`. |
| Dependency already exists in the CID (discoverable via `action_search.py`) | **Proceed** — author the workflow referencing its action ID |
| Dependency must be built and requires an app (function/collection/UI) | **Advise foundry-skills first — do NOT author workflow YAML yet.** The workflow depends on something that does not exist, so authoring it now ships a broken reference. Redirect, and only return to author once the user confirms the app dependency is built. |
| Compound request: a Foundry app (API integration / UI extension / functions) **and** a workflow in one ask | **Advise foundry-skills and STOP — produce no workflow YAML.** When the request is fundamentally app-shaped, redirect is the whole answer; do not author a partial workflow for the "workflow" clause. Mention foundry-skills explicitly and let the user come back for the standalone workflow after the app exists. |

When advising the sibling plugin, include the install command:

```bash
# Foundry app lifecycle (UI, functions, collections, manifest)
claude plugin install crowdstrike-falcon-foundry
```

If foundry-skills is not installed, advise installation but proceed with the available
tools. Detection is advisory, never blocking — both plugins must work independently.

**Console-credential boundary:** The authoring sub-skill can produce a workflow that uses an
HTTP Action, but the credential configuration it references (`config_id`/`definition_id`/
`config_name`) is created in the Falcon console and is CID-specific. Help the user discover
existing config IDs rather than inventing them — the same no-placeholder discipline that
applies to action IDs.

## Use-Case Pattern Matching

Before starting, glob `use-cases/*.md` (at the repo root) and scan the `description` field in
each file's frontmatter. If a use case matches the user's request, load it for reference context
(pattern steps, key actions, trigger configuration) before delegating to sub-skills.

**Gather reference context in parallel, and read it directly — do not spawn subagents for it.**
Reading a matched use-case file (or a couple of reference `.md` files) is a plain `Read`; batch
those reads into one message alongside the first-round discovery calls (`action_search.py`,
`trigger_search.py`) so the whole research phase resolves at once. Subagents add spin-up and
summarization overhead that outweighs any benefit for file reads — reserve them for genuinely
independent multi-step investigation, not for reading known files.

Available use cases:

| Use case | Scenario |
|----------|----------|
| `detection-enrichment` | Enrich a detection's indicators with VirusTotal, then comment/tag the case or blocklist |
| `event-queries` | Run a schemaless CQL/FQL query against the event store inside a workflow |
| `http-actions` | Call an external REST API inline with a Cloud HTTP Request (no Foundry app) |
| `api-pagination` | Page through a large REST API result set inside a workflow |
| `lookup-enrichment` | Enrich detections with third-party data via a Next-Gen SIEM lookup table |
| `custom-soar-actions` | Drive a shared Foundry API action (list/deactivate users) from a workflow |
| `export-query-results-csv` | Export Event Query results to CSV and write them to a lookup file |
| `human-in-the-loop-containment` | Gate device containment behind analyst approval on a high-severity detection |
| `detection-deduplication` | Find and close duplicate Next-Gen SIEM detections with an Event Query dedup |
| `case-management` | Query relevant events and attach them to a Next-Gen SIEM Case |
| `identity-detection-response` | Respond to an Identity Protection detection: get user context, then resolve or notify |
| `ngsiem-detection-response` | Respond to an NG-SIEM detection: hydrate it with an Event Query, extract fields, gate on a condition, summarize with an LLM, and email |
| `lookup-file-management` | Create/overwrite/append/update a lookup file from inside a workflow |
| `notifications` | Send a workflow notification to a chat channel (e.g. Slack) |
| `charlotte-agent-invocation` | Automatically invoke a published Charlotte AI (AgentWorks) agent when a detection fires |

A use case names the sub-skills it needs in its `skills:` frontmatter — use that to plan
which phases to coordinate.

## Trigger Selection (route correctly)

The trigger type shapes the whole workflow. Identify it from the user's intent so the
authoring sub-skill starts from the right shape (full detail in `references/trigger-types.md`):

| User intent | Trigger type |
|-------------|--------------|
| "run it manually", "pass in a device ID", "call from a button or API" | **On demand** |
| "when a detection fires", "on critical EPP detection", "on incident" | **Event (Signal)** |
| "every 6 hours", "nightly", "on a schedule" | **Scheduled** |
| "called by another workflow", "modular sub-playbook" | **Workflow execution** |

**The trigger type does NOT determine the data source.** Picking a Scheduled trigger
("runs every morning", "nightly") says *when* the workflow runs, not *how* it fetches
data. A scheduled workflow that "fetches all high-severity alerts / open detections /
alerts from the last 24h" is STILL fetching an alert **population** the workflow does not
hold — so it MUST use a CrowdStrike HTTP Request to `/alerts/queries/alerts/v2` (see the
population row above), NOT an Event Query, even though the schedule makes it feel like a
periodic query. A Scheduled trigger only pairs with an Event Query when the data genuinely
lives in NG-SIEM (e.g. "query the log repo for failed logins nightly").

**Severity is a numeric field (1–5), not a string.** When routing on detection severity, the
authoring sub-skill must use numeric CEL comparisons (`>= 4` for High/Critical), never
`== 'Critical'`. Flag this whenever a use case involves severity-based branching.

## Counter-Rationalizations

These thoughts mean STOP — you are about to skip a step the lifecycle requires:

| Thought | Reality |
|---------|---------|
| "I'll just write the YAML without searching actions" | STOP. Invoke the authoring skill. It runs `action_search.py` first. No exceptions. |
| "I can guess the action ID format" | WRONG. IDs are 32-char hex, only discoverable via API. |
| "I'll use a placeholder for now" | NEVER. Resolve every ID before writing YAML. No `PLACEHOLDER_*` values. |
| "Validation can wait until deploy" | NO. Authoring validates; deployment validates again as a pre-flight. Both happen. |
| "This is basically a Foundry app" | CHECK. Does it need UI/functions/collections? If not, it's a standalone workflow. |
| "I'll deploy without releasing" | INCOMPLETE. Workflows must be released before they can execute. |
| "I can skip the duplicate check" | RISKY. Importing a duplicate name silently creates a new version. |
| "Release failed — I'll re-import as `<name>-v2`." | NEVER. The name is the workflow's identity, not a version. Renaming orphans the old definition and sprawls the CID. Fix the source YAML, keep the SAME name, re-import with `--replace`. |
| "I'll build the dependency myself" | PAUSE. If it needs a Foundry function/collection, route to foundry-skills. |
| "They want all high-severity alerts — I'll Event Query the alert population." | STOP. Don't Event Query a population you don't already hold (connector-dependent NG-SIEM data). DEFAULT to a CrowdStrike HTTP Request to the Falcon API (`/alerts/queries/alerts/v2`); mention the Foundry-app FalconPy function only if the workflow must be distributed. Enriching a detection the workflow ALREADY holds stays an Event Query. |
| "version_constraint is optional" | WRONG. Every action requires it. `~0` if no `semantic_version`, `~1` if it has one. |
| "I'll trigger before it's released" | NO. Trigger only after deployment releases the workflow. |

## Reading Guide

Reference docs live under `workflows/references/`. Point sub-skills and yourself here when
you need format details:

| Need | File |
|------|------|
| YAML field reference | `workflows/references/yaml-schema.md` |
| JSON internal schema | `workflows/references/json-structure.md` |
| CEL syntax | `workflows/references/cel-expressions.md` |
| Trigger types | `workflows/references/trigger-types.md` |
| Best practices | `workflows/references/best-practices.md` |

## Improving These Skills

If a skill gave incorrect guidance, was missing a pattern, or required extra trial-and-error,
the user can ask you to capture the fix at the end of the session: clone the fusion-skills
repo, create a branch, update the relevant `SKILL.md`, and open a PR. This turns a
one-session fix into a permanent improvement for all users.
