#!/usr/bin/env bash
#
# fusion-foundry-bridge.sh
#
# PreToolUse hook on the Skill tool. Provides advisory cross-plugin routing
# between crowdstrike-falcon-fusion (standalone Fusion workflows) and
# crowdstrike-falcon-foundry (Foundry app lifecycle). Advisory only — never
# blocks a skill invocation.
#
# Two directions:
# 1. A Foundry skill is invoked while Fusion (standalone) intent is active
#    -> remind that standalone Fusion workflows belong to the workflows skill.
# 2. A Fusion skill is invoked but the work needs Foundry app capabilities
#    (UI, functions, collections, manifest) -> advise installing foundry-skills.
#
# Receives JSON on stdin with tool_input.skill (the skill being invoked).
# Outputs JSON with additionalContext. Always exits 0.

set -euo pipefail

INPUT=$(cat)

SKILL_NAME=$(echo "$INPUT" | jq -r '.tool_input.skill // empty')
MARKER="/tmp/.fusion-skill-router-active"

# Detect whether the sibling foundry plugin is installed. Best-effort: the file
# may not exist, in which case we simply skip the "already installed" wording.
INSTALLED_PLUGINS="$HOME/.claude/plugins/installed_plugins.json"
FOUNDRY_INSTALLED=false
if [ -f "$INSTALLED_PLUGINS" ]; then
  if grep -q "crowdstrike-falcon-foundry" "$INSTALLED_PLUGINS" 2>/dev/null; then
    FOUNDRY_INSTALLED=true
  fi
fi

case "$SKILL_NAME" in
  # A Foundry development skill is being invoked. If the user's intent this turn
  # was a standalone Fusion workflow (marker present), advise the Fusion path.
  crowdstrike-falcon-foundry:*|*development-workflow|*workflows-development)
    if [ -f "$MARKER" ]; then
      jq -n '{
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          additionalContext: "Cross-plugin note: For a STANDALONE Fusion workflow (no Foundry app, no manifest.yml), use the workflows skill from crowdstrike-falcon-fusion instead. Only route to Foundry if the user needs an app wrapper — UI pages, serverless functions, or collections."
        }
      }'
      exit 0
    fi
    ;;

  # A Fusion skill is being invoked. Advise the Foundry path when the work needs
  # app-only capabilities that this plugin cannot provide on its own.
  workflows|authoring|deploy|deployment|execution|lookup-files)
    if [ "$FOUNDRY_INSTALLED" = true ]; then
      MSG="Cross-plugin note: If this workflow needs a Foundry app wrapper (UI, functions, collections, or manifest.yml), the foundry-skills plugin is installed — route to crowdstrike-falcon-foundry:development-workflow for the app lifecycle, then return here to author the workflow."
    else
      MSG="Cross-plugin note: This plugin builds STANDALONE Fusion workflows. If the user needs a Foundry app wrapper (UI, functions, collections, or manifest.yml), install foundry-skills: claude plugin install crowdstrike-falcon-foundry"
    fi
    jq -n --arg msg "$MSG" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        additionalContext: $msg
      }
    }'
    exit 0
    ;;
esac

exit 0
