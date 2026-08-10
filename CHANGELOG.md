# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] - TBD

### Added

- US-3 cloud region to credential setup: a `[us-3]` profile example (`https://api.us-3.crowdstrike.com`) in the setup skill's multi-cloud block and in the README region notes, alongside a `[us-gov-1]` example that was also missing. The auth module already accepts any `base_url`, so this documents the host rather than changing behavior.
- Throttling reference in the execution skill: explains that a workflow stuck "in progress" may be throttled (Fusion paces an action past a volume limit, auto-retrying up to 6 hours) rather than failed, how to recognize it on the execution detail view, and when sustained throttling signals a workflow-design issue.
- Deduplicate and Rate Limit action reference plus a worked tutorial example. Covers all six Deduplicate activities and all four Rate Limit activities: the `definition`/`cid` scope values (which the console labels "Workflow" and "CID"), the atomic claim, metadata handoff, and the save-time validation the builder runs. The example deduplicates third-party NG-SIEM detections into a single case. Action IDs were confirmed against a live tenant; the example passes validation at all tiers.

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
