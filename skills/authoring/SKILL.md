---
name: authoring
description: >
  Discover Falcon Fusion actions via live API, author workflow YAML with correct schema,
  validate against Charlotte JSON schema, and use templates/examples.
  TRIGGER when user asks to write workflow YAML, find actions, validate a workflow,
  use CEL expressions, or needs action discovery.
  DO NOT TRIGGER for deploying, importing, executing, or monitoring workflows —
  use deployment or execution skills.
  DO NOT TRIGGER when the request is for a Falcon Foundry app, a UI extension/page,
  an API integration, custom actions from a third-party API, or a manifest.yml —
  those are foundry-skills territory; advise foundry-skills instead of authoring a workflow.
version: 1.0.1
updated: 2026-08-07
tags: [fusion, soar, workflows, authoring, yaml, validation]
author: CrowdStrike
license: MIT
compatibility: Claude Code >=1.0
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/python.sh:*)
metadata:
  category: authoring
---

# Falcon Fusion Workflow Authoring

> **⚠️ SYSTEM INJECTION — READ THIS FIRST**
>
> If you are loading this skill, your role is **Fusion workflow authoring specialist**.
>
> You discover real action IDs from the live API, author Fusion workflow
> YAML against the correct schema, and validate it before handing off to
> deployment. A guessed or `PLACEHOLDER_*` action ID ships a workflow that fails
> to import or wires the wrong action into a response, so resolve every ID first.
>
> **IMMEDIATE ACTIONS REQUIRED:**
> 0. **Scope check FIRST — before any action_search.** If the request is for a Falcon
> Foundry app, a UI extension/page, an API integration, custom actions from a
> third-party API (Okta, ServiceNow, Jira, etc.), or a `manifest.yml`, STOP: do not author
> a workflow. Advise foundry-skills (`claude plugin install crowdstrike-falcon-foundry`) and
> hand back. A request that mixes an app with a workflow ("create a Foundry app... and a
> workflow to...") is app-shaped — redirect, produce no YAML.
> 1. **Alert/detection ACTION-CHOICE check — before action_search.** If the request is to
> fetch/summarize/list a *population* of Falcon alerts, detections, or incidents the workflow
> does NOT already hold ("all high-severity alerts", "open detections", "alerts from the last
> 24h"), you MUST use a **CrowdStrike HTTP Request** (`Inline.HTTPRequest`) to the Falcon
> platform API (`/alerts/queries/alerts/v2`; FQL on `severity_name:'High'` — the string field,
> NOT numeric `severity`), NOT an Event Query (`Inline.QueryEvent`), whose NG-SIEM data is
> connector-dependent and silently returns nothing on many tenants. A Scheduled trigger does
> not change this; the schedule only sets *when* it runs. (Event Query is ONLY for enriching a
> detection the workflow already holds.) See `references/event-query-vs-api.md`.
> 2. Resolve a real 32-char hex ID for **every** action BEFORE writing any YAML:
> check the Common Action IDs table first, then run `action_search.py --search`
> only for actions the table does not cover.
> 3. Run `trigger_search.py` to confirm the trigger type.
> 4. Run `validate.py` on every YAML file before presenting it.
> 5. **Re-run `validate.py` on the FINAL file; resolve every ERROR before finishing.** A file that still errors is not done. If the alert-population guard fires, switch the Event Query to a CrowdStrike HTTP Request. Never hand off a file that fails validation.
>
> **MUST NOT:**
> - Author a workflow for a Foundry-app-shaped request (see action 0) — redirect to foundry-skills.
> - Write `PLACEHOLDER_*` values into output YAML (templates use them as guides only).
> - Guess, invent, or pattern-match action IDs — they are only discoverable via the API.
> - Invent a `config_id` or emit a stand-in — an all-zeros UUID (`0000...`) is still a placeholder. Discover or ask (AskUserQuestion).
> - Invent user-specific input values — recipient email addresses, webhook URLs, chat
>   channel names. **Ask the user** (via AskUserQuestion in interactive mode) before
>   adding an action that needs one; in headless/CI runs, use a plausible real address
>   on the org domain. (Send email only delivers to Falcon users and approved domains,
>   so `user@example.com` fails at runtime.)
> - Skip validation, or defer it to deploy time.

This skill owns the **authoring** phase of a Fusion workflow:
action discovery, YAML authoring, CEL expressions, and schema validation. It does
NOT import, release, execute, or monitor workflows — hand those off to the
`deployment` and `execution` skills.

---

## Prerequisites

- **Python 3.13+** with the `falconpy` SDK and `pyyaml` installed.
- **CrowdStrike API credentials** (never hardcoded). The shared auth module
  `common/scripts/auth.py` resolves them from the first source that supplies
  both an ID and a secret:
  1. Environment variables: `FALCON_CLIENT_ID`, `FALCON_CLIENT_SECRET`, and the
     optional `FALCON_BASE_URL` (for CI and overrides).
  2. TOML profile file `~/.cache/crowdstrike-falcon-fusion/credentials.toml`
     (profile chosen by `FALCON_PROFILE` or the file's `default` key).

  > Run `/crowdstrike-falcon-fusion:setup` to configure credentials interactively (writes the TOML profile).
- **Workflow** API scope on the API client, with read access to
  the activities catalog and import (validate) permission.
- Fusion access in the target CID.

Test credentials before authoring:
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh ../../common/scripts/auth.py     # self-test from authoring/scripts/
```

---

## Core Workflow

Follow these steps in order. Do not skip discovery (steps 1–2).

### 1. Resolve action IDs (MANDATORY)

Every action needs a real 32-char hex `id` before you write any YAML. Resolve
them **table-first**: check the Common Action IDs table below, and only run
`action_search.py` for the actions it does not cover. Guessing an ID or shipping
a `PLACEHOLDER_*` is never acceptable — but a verified ID from the table is
already resolved, so searching for it again just wastes a round-trip.

#### Common Action IDs — check here first

These actions show up in almost every workflow, with IDs verified against the
live catalog. If an action is in this table, use the row directly and do **not**
search for it.

| Action | `id` | `class` | `version_constraint` |
|--------|------|---------|----------------------|
| Event Query | `cdf5c3e0d69f156eaaf56c1f5d3f1b66` | `Inline.QueryEvent` | `~1` |
| HTTP Request | `1ba474f407d9228fc8fa02cdce8ae8ef` | `Inline.HTTPRequest` | `~1` |
| Python Script | `7fb9eb10b23943efaf1e6082b0ac0338` | `Inline.Python` | `~1` |
| Send email | `07413ef9ba7c47bf5a242799f59902cc` | — | `~1` |
| Charlotte AI - LLM Completion | `bdfecafafdb44919a458fcf51d6b93a7_98dec86072334d24b37dd798098cfd63` | — | `~0` |
| Contain device | `bec9fbeb4999d207937854fd56088107` | — | `~0` |
| VirusTotal File Hash Lookup | `668bf0d0b832510e21d7c00386d277ea` | — | `~1` |
| VirusTotal IP Request | `ce0386aacfb64bc5a3a6a4a85c07217b` | — | `~0` |
| DomainTools Iris Investigate | `b2087ff84aa1471ea209076fd4852c25` | — | `~0` |

These IDs are stable across the commercial clouds (us-1/us-2/eu-1); GovCloud may
differ. If an import ever rejects one of these `version_constraint` values,
confirm the current value with `action_search.py --search "<name>"` — the
platform occasionally bumps an action's major version.

#### Search for anything not in the table

For the remaining actions, resolve them in one batch — list every action the
workflow needs and run one `--search` per distinct name. **Fire the independent
lookups concurrently: put every `action_search.py --search` and the
`trigger_search.py` call in one message so they run in parallel.** Never
re-search an ID you already have — rediscovering an action mid-pass is the
biggest time sink.

```bash
# Search by name across all vendors (note the --search flag; a bare term errors)
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/action_search.py --search "contain"

# Search within a specific vendor
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/action_search.py --vendor "Okta" --search "revoke"

# Full schema for one action (input fields, class, plugin info)
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/action_search.py --details <action_id>
```

For each action you discover, record: `id` (32-char hex), `name`, input
fields/types, whether it has a `class` (needs `version_constraint`), and whether
it is a plugin action (needs a `config_id`).

> If a long-lived local cache might be hiding newly shipped actions, refresh it:
> `${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/action_search.py --clear-cache`. The cache also auto-refreshes
> once it is older than 1 hour.

### 2. Choose a trigger type

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/trigger_search.py --list
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/trigger_search.py --type "On demand"
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/trigger_search.py --events detection   # Signal event: values
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/trigger_search.py --fields Investigatable/EPP   # payload field paths
```

Valid trigger types: **On demand**, **Signal**, **Scheduled**, **SubModel**.
For most automation, use **On demand** (callable via API and the Falcon UI).

A **Signal** trigger MUST carry an `event:` field (the trigger category, e.g.
`Investigatable/NGSIEM`) — without it, import fails with `code 2003: "unknown
trigger event named "`. Find the value with `trigger_search.py --events` and do
NOT add a hex `id` to the trigger. For a Signal trigger, discover the exact
payload field paths (the `${data['Trigger....']}` references you can read
downstream) with `trigger_search.py --fields <category>` — do NOT guess them.
See `references/trigger-types.md`.

### 3. Author the YAML from a template

Pick the template matching the pattern, then substitute real values:

| Pattern | Template |
|---------|----------|
| Single action | `assets/single-action.yaml` |
| Loop over a list | `assets/loop.yaml` |
| Conditional branching | `assets/conditional.yaml` |
| Loop + conditional | `assets/loop-conditional.yaml` |

Add the header comment on line 1, then write `name`, `trigger`, and `actions`
using the resolved action IDs. Templates contain `PLACEHOLDER_*` markers — they show
the YAML shape, never the values. Substitute every one with a real value.

### 4. Add CEL expressions

Reference trigger inputs and prior outputs with `${data['...']}` expressions:

| Syntax | Meaning |
|--------|---------|
| `${data['param_name']}` | On-demand trigger parameter (no prefix) |
| `${data['ActionLabel.OutputField']}` | A prior action's output |
| `${data['array_param.#']}` | Current loop item |
| `${data[?'key'].orValue("default")}` | Null-safe optional access (preferred) |

See `references/cel-expressions.md` for operators, CrowdStrike extensions
(`cs.json.decode()`, `cs.ip.valid()`, `cs.timestamp.now()`), and YAML quoting.

### 5. Validate

```bash
# Pre-flight + structural + API dry-run
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/validate.py workflow.yaml

# Pre-flight + structural only (no API call)
${CLAUDE_PLUGIN_ROOT}/scripts/python.sh scripts/validate.py --preflight-only workflow.yaml
```

Fix every error before handing the file to the `deployment` skill.

---

## Script Reference

All scripts live in `scripts/` and import auth from `common/scripts/auth.py`
via the shared `sys.path` pattern.

| Script | Purpose | Key flags |
|--------|---------|-----------|
| `action_search.py` | Discover actions and vendors | `--search`, `--details`, `--list`, `--vendors`, `--vendor`, `--use-case`, `--limit`, `--offset`, `--json`, `--clear-cache` |
| `trigger_search.py` | List/describe trigger types; list Signal `event:` values; list a trigger's payload field paths | `--list`, `--type`, `--events`, `--fields`, `--json` |
| `validate.py` | Validate workflow YAML | `--preflight-only`, multiple files |

**`action_search.py` cache:** Full-catalog scans (`--vendors`, `--use-case`) are
cached locally in `.action_cache.json` (gitignored, per-user). The cache uses a
**1-hour TTL based on file mtime**: a cache at or past 1 hour is treated as stale
and auto-refreshes from the API (with a printed notice). Use `--clear-cache` to
force an immediate refresh so newly shipped action types are never hidden.

---

## Common Pitfalls / Counter-Rationalizations

| Thought | Reality |
|---------|---------|
| "I'll write the YAML, then fill in action IDs later." | STOP. Resolve every ID first — from the Common Action IDs table, or `action_search.py`. "Later" never happens — placeholders ship. |
| "I'll search for the Event Query / HTTP / Send email / Charlotte AI action." | DON'T. Those are in the Common Action IDs table — use the row directly. |
| "I'll run `action_search.py \"event query\"` to search." | WRONG FLAG. A bare term prints usage and finds nothing. Use `action_search.py --search \"event query\"`. |
| "I can guess the action ID format." | WRONG. IDs are 32-char hex, only discoverable via the table or the live API. |
| "The template has `PLACEHOLDER_RAN_006`, I'll copy it." | NEVER. Templates are structural guides. Substitute a real value before saving. |
| "Validation can wait until deploy." | NO. Validate after authoring — `validate.py` catches PLACEHOLDERs, bad IDs, and schema errors locally. |
| "This action has a `class`, version_constraint is optional." | WRONG. Class-based actions REQUIRE `version_constraint`. Missing it fails import. |
| "I'll use `~1` everywhere for version_constraint." | NO. The value is `~<major>` of the action's `semantic_version` (`~0` when it declares none): `1.0.4` → `~1`, `0.0.100` (Charlotte AI, most Store plugins) → `~0`. Read it from `--details`. |
| "I'll make up a `config_id` for this Okta action." | NEVER. It's CID-specific (exists only once configured in the console). Ask the user (AskUserQuestion) — even non-interactively. Sequential/all-zeros/repeated-char UUIDs are still fabricated and fail at runtime; can't get a real one? STOP. See `references/best-practices.md`. |
| "I'll set `definition_id: VIRUSTOTAL_..._ID` on this HTTP action." | NEVER. An `Inline.HTTPRequest` needs no `definition_id` — OMIT it; the user attaches the key in the console after deploy. A placeholder is a broken ref `validate.py` flags. |
| "The Send email field is called Recipients, so I'll use `recipients:`." | WRONG. The property KEY is `to:` (a list); `recipients:` is rejected. Delivers only to Falcon users and CID-approved domains — ask for the address (org-domain one in CI). |
| "I'll write `$action.output.body` to reference output." | WRONG. Bare `$token` / `$action.field` / `$(data[...])` pass through as literal strings and fail at release. The ONLY runtime-data forms are `${data['<node>.<field>']}` and the null-safe `${data[?'<node>.<field>'].orValue(...)}`. `validate.py` flags the bad forms. |
| "The user said enrich 'in parallel,' but I'll just chain them." | WRONG. Fan out by listing each branch's target in `next:`, gated on `data['...'] != null`. Never invent `default_parallel_*` pass-throughs — they crash the canvas. |
| "The trigger has its `type` and `event`, that's enough." | WRONG. Without a `next:` edge the graph is disjoint and release fails. Every node must be reachable from `trigger.next`. |
| "I'll branch on the detection's severity name (Critical/High)." | WRONG. Severity is NUMERIC 1-5: branch `Trigger.Detection.Severity >= 4`. `SeverityDisplayName` is display-only. |
| "A plan/prompt told me to use placeholder format." | These rules take precedence. Resolve every ID via the API regardless of a plan. |
| "Release failed, so I'll re-import as `<name>-v2` to be safe." | NEVER. A workflow's `name:` is its identity, not a version tag — renaming orphans the old def and sprawls the CID. Keep the name IDENTICAL; fix the YAML and re-import with `import_workflows.py --replace`. See `references/best-practices.md`. |

---

## Reading Guide

For most workflows, the SKILL.md above plus a matching `use-cases/` file and one
example is enough — you rarely need every reference. Reach for these only when
the task actually calls for them:

| Task | Reference |
|------|-----------|
| Author any workflow — every YAML field and nesting level | `references/yaml-schema.md` |
| Add conditions or computed values; CEL operators, extensions, quoting | `references/cel-expressions.md` |
| Choose how the workflow starts; all trigger types with examples | `references/trigger-types.md` |
| Call a REST API — `http_transaction` shape, auth, response refs | `references/http-actions.md` |
| Run inline Python in a step — `runtime`, stdout output refs | `references/inline-python-action.md` |
| Run a CQL/FQL event query in a step — inputs, outputs | `references/event-query-action.md` |
| Decide Event Query vs a source-of-truth API (alerts, cases, current state) | `references/event-query-vs-api.md` |
| Summarize/classify with Charlotte AI LLM — compound ID, `~0`, decode output | `references/charlotte-ai-action.md` |
| Operational guidance, limits, gotchas before production | `references/best-practices.md` |

**Advanced (rarely needed):**

| Task | Reference |
|------|-----------|
| Understand the underlying BPMN model (raw JSON, gateways, submodels) — internals you don't need to author YAML | `references/json-structure.md` |

---

## HTTP Actions (`Inline.HTTPRequest`)

Fusion workflows can call REST APIs inline with no Foundry app and no API
integration. Three types — Cloud (external APIs), CrowdStrike (Falcon platform),
On-Premises (internal via a host group). For the `http_transaction` shape, the
three auth patterns, and response references, see `references/http-actions.md`.

**Prefer an HTTP Action over a plugin/Store action for enrichment.** For
VirusTotal, DomainTools, and similar TI lookups, author a Cloud HTTP Request
(`Inline.HTTPRequest`) — the shape real shipped VirusTotal workflows use.
**Author it credential-less: omit `definition_id`, leave authentication unset.**
The imported action shows Authentication = "None"; the user attaches the API key
in the console after deploy (Create new → API key → Header → `x-apikey`), then it
runs. Never fabricate a `definition_id`; only set a real 32-char hex id the user
supplies. Store *plugin* actions (compound IDs `<hex>~<hex>`) need a CID-specific
`config_id` that must already exist; emitting one blind fails at import/release.
Use a plugin action only when the user supplies its `config_id`. Reserve a Foundry
API integration for reused/UI-paired operations — that path belongs to `foundry-skills`.
See `references/http-actions.md` for the auth shapes and the console credential steps.

---

## Inline actions (`Inline.Python`, `Inline.QueryEvent`)

Fusion has native CrowdStrike actions that run inline in a workflow step (no
Foundry app, no `config_id`), each with `class:` set and `version_constraint: ~1`:

- **Python Script** (`Inline.Python`) — run user Python; `runtime: py0313general`
  required, read output as `${data['<node>.output_stdout']}`. See
  `references/inline-python-action.md`.
- **Event Query** (`Inline.QueryEvent`) — run a CQL/FQL query against the event
  store; inputs `query`/`time_range`/`repo`. See
  `references/event-query-action.md`. **NOT for querying a population of Falcon
  alerts/detections you don't already hold** (e.g. "summarize all high-severity
  alerts") — that's connector-dependent NG-SIEM data; use a CrowdStrike HTTP
  Request to `/alerts/queries/alerts/v2` instead. Event Query is for data that
  lives in NG-SIEM or for enriching a detection the workflow already holds.

## Charlotte AI — LLM Completion

`Charlotte AI - LLM Completion` runs a prompt through an LLM to summarize,
classify, or extract fields. It is a **plugin action**: no `class:`,
`version_constraint: ~0`, and its `completion` output is a JSON string you must
decode with `cs.json.decode()`. Discover the ID with
`action_search.py --search "llm"`. Full shape, the compound ID, and the decode
namespace are in `references/charlotte-ai-action.md`.

---

## Console-Credential Boundary

This is the key thing authoring can and cannot change. The authoring skill lets
you write workflow YAML **outside** the Falcon console. But an HTTP Action (and
some plugin actions) references a credential configuration — `config_id`,
`definition_id`, or `config_name` — that is **created in the console and is
CID-specific**. This skill can author the workflow that *uses* the action, but
the credential config it points to must already exist in the CID. Apply
the same discipline as with action IDs:

- **Discover existing config IDs** where possible (via `action_search.py
  --details` on the plugin action, or ask the user where to find it: Falcon
  console → CrowdStrike Store → [App] → Integration settings).
- **Never invent a `config_id`, or substitute a placeholder for one.** A
  fabricated ID fails at runtime — a fake UUID, `YOUR_*`, `TODO`/`FIXME`, or an
  all-zeros UUID is NOT a valid stand-in. When you can't discover it, **ask the
  user** via AskUserQuestion; asking is required even in non-interactive/CI runs
  (the caller supplies the value there).
- If the config does not yet exist, **document the dependency** and pause — the
  user must create it in the console before the workflow will run.

**HTTP Actions are the exception — do not block on a credential.** An
`Inline.HTTPRequest` can be authored credential-less (no `definition_id`) and
deployed; it imports with Authentication = "None" and the user attaches the API
key in the console afterward (proven end-to-end). So for HTTP actions, prefer
credential-less authoring over pausing — see `references/http-actions.md`. The
"must already exist" rule applies to *plugin* actions gated on a `config_id`.

Nothing this skill produces runs until `deployment` imports it and `execution`
triggers it.
