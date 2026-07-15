# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - TBD

Initial release of Falcon Fusion Skills — AI coding assistant skills for building CrowdStrike Falcon Fusion workflows.

### Skills

- **workflows** — Orchestrator skill and single entry point. A decision tree routes intent to the right sub-skill and coordinates the full workflow lifecycle.
- **authoring** — Live action discovery, workflow YAML authoring, CEL expressions (including `else`/`else_if` conditional routing), and structural schema validation. Documents HTTP Actions, Event Query (`Inline.QueryEvent`), Python Script (`Inline.Python`), and Charlotte AI LLM Completion actions. Signal triggers are documented with the required `event:` field (the trigger category, e.g. `Investigatable/NGSIEM`), which `trigger_search.py --events` discovers from the trigger catalog.
- **deployment** — Duplicate checking, import to a CID, release, delete, and version management.
- **execution** — Workflow triggering with payloads, execution monitoring, and result retrieval for debugging.
- **lookup-files** — Create, list, get, update, and delete Falcon Next-Gen SIEM lookup files for CQL `match()` queries.
- **setup** — Guided credential configuration that writes a multi-cloud TOML profile to `~/.cache/crowdstrike-falcon-fusion/credentials.toml`, with the secret entered through the user's own editor (never the chat).

### Infrastructure

- **Shared authentication** (`common/scripts/auth.py`) — Exposes both a Fusion Workflows client and a Next-Gen SIEM client, with credentials resolved from environment variables or a multi-CID TOML profile file.
- **Structural validator** (`skills/authoring/scripts/validate.py`) — Pre-flight, structural, and API dry-run validation of workflow YAML without deploying, catching action-ID, trigger (including invalid trigger types like `On_demand` at pre-flight), reference, schema, off-schema top-level shape, scalar-`next` (the import API requires list form), disjoint-node (unreachable-from-trigger), per-action required-property errors (HTTP Request actions missing `request_http_method`/`request_content_type`, Charlotte AI LLM actions missing `user_prompt`/`model_name`, and Signal triggers carrying an unsupported `parameters` schema), and wrong-form data references (bare `$token`, shell-style `$(data[...])`) locally before a failed release.
- **Cross-plugin hooks** — Advisory routing to and from the `crowdstrike-falcon-foundry` plugin, plus an intent router for the workflows skill.
- **Test suite** — 507 unit tests across all 19 scripts (93% coverage, enforced at 90% in CI), plus `test-hooks.sh` and `test-validate.sh` for hook and structural checks.
- **Lookup `match()` verification** (`skills/lookup-files/scripts/verify_lookup.py`) — a maintainer tool that uploads a lookup file, resolves a known row through a live CQL `match()` query, and deletes it, confirming the file is visible to `match()` end to end. Also wired into `verify-workflows.sh --lookup-dir`.

### Examples

25 Content Library workflow examples in YAML across six categories (threat intel, identity response, notifications, response actions, Next-Gen SIEM, and tutorials), each with resolved action IDs and every one verified to import cleanly via server-side validation. Examples are generated deterministically from Content Library catalog records by `bin/convert_catalog_to_yaml.py` (which flattens the BPMN-style graph into `trigger`/`actions`/`conditions`/`loops` and resolves the Signal `event:` value), so they are reproducible rather than hand-converted. The converter emits the console-renderable graph shape — parallel branches fan out via multi-target `next:`, exclusive gateways become a single `cel_expression` condition with an `else:` branch — so imported workflows also open cleanly in the Falcon visual editor, not just the API. Coverage includes a real parallel fan-out enrichment playbook (AbuseIPDB) and an `Inline.Python` script action (building a lookup file from an external feed).

### Use Cases

14 pattern-matchable use cases in `use-cases/`, each with frontmatter naming its source and the sub-skills it needs. Some are drawn from published CrowdStrike Tech Hub posts; others are grounded directly in the bundled example workflows (detection enrichment, detection deduplication, human-in-the-loop containment, identity detection response, case management, lookup file management, and notifications) and corroborated by the community Workflow Wednesday series. Every action ID and `version_constraint` cited in a use case is traceable to its source example or flagged as a variation to resolve with `action_search.py`.

### Multi-Tool Support

- **`AGENTS.md`** — Canonical, tool-agnostic Fusion workflow development guide.
- **`CLAUDE.md`** — Claude Code-specific plugin additions.
- **`GEMINI.md`** — Redirect for Gemini CLI.
- **`.github/copilot-instructions.md`** — Redirect for GitHub Copilot.

[1.0.0]: https://github.com/CrowdStrike/fusion-skills/releases/tag/v1.0.0
