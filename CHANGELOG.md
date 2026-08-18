# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] - TBD

### Added

- **`foundry-redirect` skill** — declines Falcon Foundry app requests (a UI page, serverless function, collection, `manifest.yml`, or custom third-party API integration) and points to the sibling `crowdstrike-falcon-foundry` plugin. The `workflows` orchestrator already declines these, but its description matches Fusion-workflow language, so a "build a Foundry app" prompt never loads it; a Claude Code hook covered that gap, but hooks do not fire in Codex, Copilot CLI, Cursor, or the Agent SDK. This skill's description matches Foundry-app language directly, so the redirect is reachable on every assistant. It yields to the Foundry plugin's own skill when that plugin is installed.
- **Skill scripts run on assistants other than Claude Code.** The scripts now resolve their own location with `os.path.realpath` instead of `os.path.abspath`, so an assistant that launches a script through a `~/.agents/skills/<skill>` symlink (Codex, Copilot CLI, Cursor, Antigravity) correctly finds the shared `common/scripts` modules and the managed-venv wrapper — the cold-start dependency bootstrap fires as it does for Claude Code, instead of failing with a missing-`falconpy` error. Each `SKILL.md` now shows a portable invocation — `cd` into the skill's folder, then run `../../scripts/python.sh <script>` on one line — that works whether or not `${CLAUDE_PLUGIN_ROOT}` is set, since that variable is defined only by Claude Code and no equivalent exists on the other assistants.
- **Agent Plugins manifests for non-Claude assistants** — A root `plugin.json` conforming to the [Agent Plugins](https://agent-plugins.org) 1.0.0 spec and a Codex-flavored `.codex-plugin/plugin.json`, so Codex, Copilot CLI, Cursor, and Antigravity CLI can discover the plugin alongside the existing Claude manifest. CI validates both and keeps their versions in lockstep with `.claude-plugin/plugin.json`.
- **Install instructions for five AI coding assistants** — Claude Code, Codex, Copilot CLI, Cursor, and Antigravity CLI each get documented install commands (`--plugin-dir` for Claude Code, Copilot CLI, and Cursor; a `~/.agents/skills/` symlink for Codex and Antigravity CLI). Claude Code is verified end to end; the others load the skills and are documented, with broader end-to-end verification tracked separately.
- US-3 cloud region to credential setup: a `[us-3]` profile example (`https://api.us-3.crowdstrike.com`) in the setup skill's multi-cloud block and in the README region notes, alongside a `[us-gov-1]` example that was also missing. The auth module already accepts any `base_url`, so this documents the host rather than changing behavior.
- Throttling reference in the execution skill: explains that a workflow stuck "in progress" may be throttled (Fusion paces an action past a volume limit, auto-retrying up to 6 hours) rather than failed, how to recognize it on the execution detail view, and when sustained throttling signals a workflow-design issue.
- Deduplicate and Rate Limit action reference plus a worked tutorial example. Covers all six Deduplicate activities and all four Rate Limit activities: the `definition`/`cid` scope values (which the console labels "Workflow" and "CID"), the atomic claim, metadata handoff, and the save-time validation the builder runs. The example deduplicates third-party NG-SIEM detections into a single case. Action IDs were confirmed against a live tenant; the example passes validation at all tiers.

### Fixed

- Three authoring-doc corrections from a Fusion engineer's tech review. `version_constraint` is no longer framed as class-specific — nearly every action carries one whether or not it declares a `class`, so include it on every action node. The event-trigger and system-level variables (`Trigger.CID`, `Workflow.Execution.ID`, `Workflow.Definition.Name`, etc.) are now shown in the `${data['...']}` form and are documented as living in the `data` namespace like any other field rather than as an exception. And the action `name:` field is described as a relabelable display label — renaming it does not break references, which resolve by node key and action `id`.
- **`version_constraint` decides the shape of an action's output paths, and that was undocumented.** Unpinned, a reference carries the action's namespace (`${data['DeviceQuery.Device.query.devices']}`); pinned at `~1` the same field is `${data['DeviceQuery.devices']}`. The two forms are mutually exclusive, so adding a `version_constraint` to an existing workflow without shortening its `${data['...']}` references produces YAML that imports cleanly and then fails at release with `property "..." contains unknown variable`. The schema and best-practices references now document this as a single two-part edit. Verified against a live tenant for Device Query, Get device details, and Event Query. The `workflows` copies of both references were also brought in line with the `authoring` copies, which had drifted: they still said to "always use `~1`" and claimed all CrowdStrike actions sit at major version 1, which is wrong for the 0.x actions such as Charlotte AI.
- Clarified that only class-based actions strictly *require* a `version_constraint` at import. A non-class action such as Device Query imports and releases without one; it just keeps the older, longer output paths.
- **`validate.py` now catches the pinned/long-path mismatch before release.** The structural tier flags a `${data['...']}` reference that keeps an action's output namespace (`device.query`, `device.get_details`, `logscale.query_event`) while that action is pinned with a `version_constraint` — the exact shape that imports cleanly and passes server-side `validate_only`, then fails at release as an unknown variable. The error names the collapsed replacement (`${data['<node>.<field>']}`). An unpinned action legitimately keeps the long path, so the check fires only when the referenced node is pinned. This turns the documentation above into an enforced check; a release-gated eval had produced a workflow that imported clean and failed release on precisely this.
- **MITRE ATT&CK trigger fields that release rejects on the NG-SIEM trigger are now flagged.** `Trigger.Detection.MitreAttack.Tactic` and `.Technique` are advertised by trigger discovery — including `trigger_search.py --fields` — on the `Investigatable/NGSIEM` trigger, but the release validator rejects them as unknown variables; they are not on the NG-SIEM trigger payload at release time. The structural validator now flags a workflow that references them on that trigger, and `trigger_search.py --fields` marks them "NOT release-valid" so the tool stops steering authors toward a field that fails at release. Source MITRE tactics/techniques from the hydrated detection instead. Surfaced by a release-gated eval; confirmed live.

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
