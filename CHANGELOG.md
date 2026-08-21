# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **CEL timestamp and time-math functions** in the CEL expressions reference — `cs.timestamp.parse(str, 'RFC3339')` plus the live-verified Unix-epoch-millisecond idiom (`int((… - timestamp('1970-01-01T00:00:00Z')).getMilliseconds())`) and `duration(...)` windowing. Includes a case-management pattern for building dynamic, time-scoped Event Search deep links from a detection's `Trigger.ObservedTime`.

### Fixed

- **`validate.py` now flags `WorkflowCustomVariable.<name>` references to variables that nothing declares** — a release-only failure. A reference to a custom variable that no `CreateVariable` (or `UpdateVariable` setter) declares imports and validates cleanly, then fails at release with `property "..." contains unknown variable "WorkflowCustomVariable.<name>"`. The validator now collects declared variable names and reports an undeclared reference before you deploy.

## [1.1.0] - 2026-08-19

### Added

- **`foundry-redirect` skill** — declines Falcon Foundry app requests (a UI page, serverless function, collection, `manifest.yml`, or custom third-party API integration) and points to the sibling `crowdstrike-falcon-foundry` plugin. Its description matches Foundry-app language, so the redirect is reachable on every assistant, and it yields to the Foundry plugin's own skill when that plugin is installed.
- **Skills run on assistants other than Claude Code** (Codex, Copilot CLI, Cursor, Antigravity). Scripts resolve their own location, so the managed-venv bootstrap works when a skill is launched through a `~/.agents/skills/` symlink instead of failing with a missing-`falconpy` error. Each `SKILL.md` shows a portable invocation that works whether or not the Claude-only `${CLAUDE_PLUGIN_ROOT}` is set.
- **Agent Plugins manifests for non-Claude assistants** — A root `plugin.json` conforming to the [Agent Plugins](https://agent-plugins.org) 1.0.0 spec and a Codex-flavored `.codex-plugin/plugin.json`, so Codex, Copilot CLI, Cursor, and Antigravity CLI can discover the plugin alongside the existing Claude manifest. CI validates both and keeps their versions in lockstep with `.claude-plugin/plugin.json`.
- **Install instructions for five AI coding assistants** — Claude Code, Codex, Copilot CLI, Cursor, and Antigravity CLI each get documented install commands (`--plugin-dir` for Claude Code, Copilot CLI, and Cursor; a `~/.agents/skills/` symlink for Codex and Antigravity CLI). In a live-tenant run, Claude Code, Codex, Copilot CLI, and Cursor each authored a valid workflow from the canonical prompt and imported it to the tenant (left disabled until released). Antigravity CLI loads the skills, but its weekly quota was exhausted before a full author-and-import run, so its end-to-end path is not yet verified.
- US-3 cloud region in credential setup: a `[us-3]` profile example (`https://api.us-3.crowdstrike.com`) in the setup skill's multi-cloud block and the README region notes, alongside a `[us-gov-1]` example that was also missing.
- Throttling reference in the execution skill: explains that a workflow stuck "in progress" may be throttled (Fusion paces an action past a volume limit, auto-retrying up to 6 hours) rather than failed, how to recognize it on the execution detail view, and when sustained throttling signals a workflow-design issue.
- Deduplicate and Rate Limit action reference plus a worked tutorial example, covering all six Deduplicate and four Rate Limit activities — the `definition`/`cid` scope values (labeled "Workflow" and "CID" in the console), the atomic claim, metadata handoff, and the builder's save-time validation. The example deduplicates third-party NG-SIEM detections into a single case.

### Fixed

- Authoring-doc corrections from a Fusion engineer's review: event and system variables (`Trigger.CID`, `Workflow.Execution.ID`, `Workflow.Definition.Name`, etc.) are documented in the `${data['...']}` form like any other field, and the action `name:` is described as a relabelable display label — renaming it doesn't break references, which resolve by node key and action `id`.
- **Documented that `version_constraint` decides an action's output-path shape.** Unpinned, a reference carries the action's namespace (`${data['DeviceQuery.Device.query.devices']}`); pinned at `~1` the same field collapses (`${data['DeviceQuery.devices']}`). The two forms are mutually exclusive, so pinning an action without shortening its `${data['...']}` references imports cleanly and then fails at release with `property "..." contains unknown variable`. Only class-based actions require a `version_constraint` at import; others release fine without one and keep the longer paths.
- **`validate.py` now catches four release-only failures** — workflows that import cleanly and pass server-side validation, then fail when you release them:
  - a pinned action referenced by its long, unpinned output path (the mismatch documented above);
  - MITRE ATT&CK trigger fields (`Trigger.Detection.MitreAttack.Tactic`/`.Technique`) on the NG-SIEM trigger — advertised by trigger discovery but rejected at release; source them from the hydrated detection instead (`trigger_search.py --fields` now marks them "NOT release-valid");
  - a `default: true` gateway node with no expression or `else:` (`exclusive gateway ... has no condition set`); put the fallthrough in an `else:` on the expression-bearing condition;
  - a missing/empty or fake-domain `to` recipient on the Send email and Request human input actions; use a variable the user supplies (trigger parameter, `WorkflowCustomVariable`, or a prior step's output), not a hardcoded `@example.com`-style address that no CID delivers to.
- Fixed three shipped examples so they release cleanly: `intro-receive-email-trigger`, `intro-lookup-file-actions`, and `network-contain-endpoint-on-detection` (converted off `default: true` gateways, with its approval emails taking a configurable `${WorkflowCustomVariable.approver_email}` recipient).

## [1.0.1] - 2026-08-07

### Fixed

- Relocated helper scripts from `bin/` to `scripts/` so the plugin installs on claude.ai and Cowork. A top-level `bin/` directory is added to the CLI's PATH but isn't shown on the web admin approval surface, so those hosts rejected the plugin. The scripts are internal helpers, not entry points, and moving them out of `bin/` clears the block. Command-line installs were unaffected.

### Changed

- Renamed the deployment skill from `deploy` to `deployment` so it matches its sibling skills (`authoring`, `execution`) and the `foundry-skills` convention. The command is now `/crowdstrike-falcon-fusion:deployment`; the picker no longer rewrites `:deploy` to `:deployment` on submit.

## [1.0.0] - 2026-07-30

First public release of Falcon Fusion Skills — AI coding assistant skills for building CrowdStrike Falcon Fusion workflows. Describe the automation you want in plain language and your assistant discovers the real action IDs from your tenant, writes the YAML, validates it against the platform schema, imports it to your CID, and runs it.

### Skills

- **workflows** — Orchestrator and single entry point. Reads your intent and coordinates authoring, deployment, and execution.
- **authoring** — Live action discovery, workflow YAML authoring, and schema validation. Covers conditional routing, HTTP Actions, Event Queries, inline Python, and Charlotte AI summaries.
- **deployment** — Import to a CID, release, delete, and duplicate cleanup.
- **execution** — Trigger a workflow, monitor it, and retrieve results for debugging.
- **lookup-files** — Create, list, update, and delete the Next-Gen SIEM lookup files behind CQL `match()` queries.
- **setup** — Guided credential configuration stored in a local profile, with the secret typed into your own editor rather than the chat.

### Validation

- **Local validator** — Catches the mistakes that otherwise surface only at release time or as a silently empty result: bad action IDs, malformed references, the wrong trigger shape, and an Event Query pointed at data that isn't reliably there. You find out in seconds instead of after a failed deploy, and every check traces back to a real failure we hit and fixed.

### Examples

- 25 workflow examples from the CrowdStrike Content Library, spanning threat intel, identity response, notifications, response actions, Next-Gen SIEM, and tutorials. Every one imports cleanly and opens in the Falcon visual editor, so they double as working references.

### Use Cases

- 15 pattern-matchable use cases that map common requests (enrich a detection, close duplicate detections, respond to an NG-SIEM detection) to the workflow that solves them, each grounded in a bundled example or a published CrowdStrike walkthrough.

### Editor and CLI Support

- Tested with Claude Code. Experimental setup instructions for Codex, Copilot CLI, Cursor, and Antigravity CLI, written from each tool's own documentation but not yet verified end to end. The skills are plain markdown, so any assistant that reads local files can use them.

[1.1.0]: https://github.com/CrowdStrike/fusion-skills/releases/tag/v1.1.0
[1.0.1]: https://github.com/CrowdStrike/fusion-skills/releases/tag/v1.0.1
[1.0.0]: https://github.com/CrowdStrike/fusion-skills/releases/tag/v1.0.0
