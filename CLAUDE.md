# CLAUDE.md

Project-level instructions for Claude Code when working in the `fusion-skills` plugin. For the tool-agnostic guide (repo structure, skills ecosystem, usage without the plugin system), see [AGENTS.md](./AGENTS.md).

## Plugin Hook Behavior

This plugin includes two hooks that run automatically:

- **UserPromptSubmit + PreToolUse**: `fusion-skill-router.sh` detects Fusion workflow intent in the prompt, writes a short-lived marker file, and injects advisory context steering toward the `workflows` orchestrator skill. On later tool calls it emits a non-blocking reminder until the Skill tool is invoked.
- **PreToolUse (Skill)**: `fusion-foundry-bridge.sh` provides advisory cross-plugin routing between `crowdstrike-falcon-fusion` (workflows) and `crowdstrike-falcon-foundry` (Foundry app lifecycle).

Both hooks are **advisory only** — they always exit 0 and never block a user action or a skill invocation.

## Counter-Rationalizations

The skills enforce discipline to prevent common failures. When you catch yourself thinking one of these, stop:

| Thought | Reality |
|---------|---------|
| "I'll write the YAML without searching actions" | STOP. Run `action_search.py` first — action IDs are opaque catalog identifiers, only discoverable via API |
| "I'll use a placeholder ID for now" | NEVER. Resolve every action ID before writing YAML. No `PLACEHOLDER_*` values |
| "version_constraint is optional" | WRONG. Every action requires it: `~<major>` of its `semantic_version` (`~0` when none, e.g. Charlotte AI at `0.0.100`) |
| "Validation can wait until deploy" | NO. Authoring validates; deployment validates again as a pre-flight |
| "I'll deploy without releasing" | INCOMPLETE. Workflows must be released before they can execute |
| "This is basically a Foundry app" | CHECK. If it needs UI/functions/collections, route to foundry-skills instead |

## Skills Integration

- **Orchestration**: The `workflows` skill is the entry point. It routes to `authoring`, `deployment`, and `execution`, and coordinates the full lifecycle for end-to-end requests.
- **Direct invocation**: Each sub-skill can be invoked directly for a focused task (e.g., just validating an existing YAML file).
- **Shared auth**: All Python scripts import credentials from `common/scripts/auth.py` (`get_client()` for Fusion, `get_ngsiem_client()` for lookup files).
- **Superpowers**: If installed, superpowers planning/TDD skills MAY supplement the workflow but should not replace the orchestrator's routing.
