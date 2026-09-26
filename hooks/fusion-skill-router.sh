#!/usr/bin/env bash
#
# fusion-skill-router.sh
#
# Two-hook system for Falcon Fusion skill routing:
# 1. UserPromptSubmit: Detects Fusion workflow keywords -> writes a marker file
#    + injects advisory context steering toward the workflows orchestrator skill.
# 2. PreToolUse (all tools): Reads the marker -> injects a non-blocking advisory
#    reminder to use the fusion workflows skill, once per detected prompt.
#
# The marker file bridges the two hooks since they run at different times. It is
# scoped to the session and reset on every prompt, so a detection never carries
# into a later prompt or another session. fusion-foundry-bridge.sh reads it too,
# so it stays until the next prompt or a Skill call; a sidecar file records that
# the reminder was already given.
#
# Receives JSON on stdin with hook_event_name and event-specific fields.
# Outputs JSON with additionalContext. Always exits 0 — never blocks the user.

set -euo pipefail

INPUT=$(cat)

HOOK_EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
MARKER="/tmp/.fusion-skill-router-active${SESSION_ID:+-$SESSION_ID}"
NUDGED="$MARKER.nudged"

case "$HOOK_EVENT" in
  UserPromptSubmit)
    # Each prompt is classified on its own; never carry a detection forward.
    rm -f "$MARKER" "$NUDGED"
    USER_PROMPT=$(echo "$INPUT" | jq -r '.prompt // .user_prompt // .query // empty')
    PROMPT_LOWER=$(echo "$USER_PROMPT" | tr '[:upper:]' '[:lower:]')

    FUSION_MATCH=false

    # Direct Fusion phrases always trigger.
    PHRASES="fusion workflow|fusion playbook|fusion soar|soar workflow|create workflow|build a workflow|build a playbook|action discovery|action_search|deploy to cid"
    if echo "$PROMPT_LOWER" | grep -qE "(${PHRASES})"; then
      FUSION_MATCH=true
    fi

    # Verb + Fusion noun (e.g. "automate crowdstrike actions").
    VERBS="create|build|author|write|deploy|import|release|run|execute|automate|trigger|monitor"
    NOUNS="fusion|playbook|soar|workflow yaml|crowdstrike action"
    if echo "$PROMPT_LOWER" | grep -qE "\b(${VERBS})\b.*(${NOUNS})"; then
      FUSION_MATCH=true
    fi

    # Explicit skill request always triggers.
    if echo "$PROMPT_LOWER" | grep -qE "(use|invoke|run) (fusion|workflows) (skill|plugin)"; then
      FUSION_MATCH=true
    fi

    if [ "$FUSION_MATCH" = true ]; then
      echo "$$" > "$MARKER"

      jq -n '{
        hookSpecificOutput: {
          hookEventName: "UserPromptSubmit",
          additionalContext: "FUSION PLUGIN DETECTED: This prompt involves Falcon Fusion workflow automation. Invoke the crowdstrike-falcon-fusion workflows orchestrator skill via the Skill tool. It routes to authoring (discover actions, write/validate YAML), deployment (import/release to CID), and execution (trigger/monitor). Do NOT hand-write workflow YAML or guess action IDs."
        }
      }'
      exit 0
    fi
    ;;

  PreToolUse)
    TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

    # Only intercept when the current prompt was detected as Fusion intent.
    if [ -f "$MARKER" ]; then
      # Allow the Skill tool through — that's the goal. Clean up the marker.
      if [ "$TOOL_NAME" = "Skill" ]; then
        rm -f "$MARKER" "$NUDGED"
        exit 0
      fi

      # Advisory nudge, once per detected prompt — never block tools.
      [ -f "$NUDGED" ] && exit 0
      touch "$NUDGED"
      jq -n '{
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          additionalContext: "Fusion plugin reminder: Consider invoking the crowdstrike-falcon-fusion workflows skill for Fusion workflow tasks. It coordinates action discovery, YAML authoring/validation, deployment, and execution."
        }
      }'
      exit 0
    fi
    ;;
esac

exit 0
