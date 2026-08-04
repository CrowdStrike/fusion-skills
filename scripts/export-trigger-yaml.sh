#!/usr/bin/env bash
#
# export-trigger-yaml.sh — Discover the ground-truth shape of a Fusion trigger
# by building it in the Falcon console and exporting the YAML.
#
# Why this exists: Fusion has no API that reveals what fields a Signal trigger
# actually requires. Our seed examples import with a validation error
# ("unknown trigger event named ") because the trigger id + display name are
# not enough — the console writes an additional field on export that we cannot
# discover any other way. This script drives the console with a nested `claude`
# agent using the Playwright MCP tools (no pip Playwright dependency, mirroring
# verify-workflows.sh Phase 2), builds ONE minimal workflow with the requested
# trigger, exports it, and saves the exported YAML for inspection.
#
# You run this; it needs a Falcon console login (SSO), which only a human can
# complete. The agent polls for the login to finish, then proceeds.
#
# Usage:
#   bin/export-trigger-yaml.sh                       # NG-SIEM Detection trigger
#   bin/export-trigger-yaml.sh --trigger "Detection > NG-SIEM Detection"
#   bin/export-trigger-yaml.sh --out /tmp/trigger.yaml --cloud us-2
#
# Flags:
#   --trigger LABEL   Trigger event source to pick in the console picker
#                     (default: "Detection > NG-SIEM Detection")
#   --out PATH        Where to save the exported YAML
#                     (default: /tmp/fusion-skill-test/exported-trigger.yaml)
#   --cloud NAME      Falcon cloud: us-1 | us-2 | eu-1 (default: us-2)
#
set -uo pipefail

if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'
  BLUE=$'\033[0;34m'; RESET=$'\033[0m'
else
  GREEN=""; RED=""; BLUE=""; RESET=""
fi

TRIGGER_LABEL="Detection > NG-SIEM Detection"
OUT_PATH="/tmp/fusion-skill-test/exported-trigger.yaml"
CLOUD="${FALCON_CLEANUP_CLOUD:-us-2}"

usage() {
  cat <<'EOF'
Usage: export-trigger-yaml.sh [--trigger LABEL] [--out PATH] [--cloud NAME]

  --trigger LABEL   Trigger event source to pick (default: "Detection > NG-SIEM Detection")
  --out PATH        Where to save exported YAML (default: /tmp/fusion-skill-test/exported-trigger.yaml)
  --cloud NAME      Falcon cloud: us-1 | us-2 | eu-1 (default: us-2)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --trigger) TRIGGER_LABEL="$2"; shift 2 ;;
    --out)     OUT_PATH="$2"; shift 2 ;;
    --cloud)   CLOUD="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)         echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if ! command -v claude >/dev/null 2>&1; then
  printf "%bERROR: 'claude' CLI not found in PATH.%b\n" "$RED" "$RESET" >&2
  exit 2
fi

case "$CLOUD" in
  us-1) FALCON_HOST="falcon.crowdstrike.com" ;;
  eu-1) FALCON_HOST="falcon.eu-1.crowdstrike.com" ;;
  *)    FALCON_HOST="falcon.us-2.crowdstrike.com" ;;
esac
FALCON_URL="${FALCON_URL:-https://$FALCON_HOST}"

OUT_DIR="$(dirname "$OUT_PATH")"
mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/export-trigger.log"

printf "%b\n" "${BLUE}==========================================${RESET}"
printf "%b\n" "${BLUE}  EXPORT-TRIGGER-YAML${RESET}"
printf "%b\n" "${BLUE}==========================================${RESET}"
printf "  Trigger:  %s\n" "$TRIGGER_LABEL"
printf "  Console:  %s\n" "$FALCON_URL"
printf "  Output:   %s\n" "$OUT_PATH"
printf "  Log:      %s\n" "$LOG_FILE"
printf "  A browser opens at the Falcon console — log in if prompted.\n\n"

# The agent must print the exported YAML between explicit fences so we can
# recover it deterministically from the streamed log even if the surrounding
# narration is chatty.
PROMPT="You are discovering the exact YAML shape of a Falcon Fusion workflow trigger by building it in the CrowdStrike Falcon console and exporting it.

## Login
- Navigate to ${FALCON_URL}/workflow/fusion
- If a login/SSO page appears, DO NOT give up — the user logs in manually. Poll every 15s (up to 3 min) with browser_snapshot until the 'All workflows' page shows.

## Browser guidelines
- Use browser_snapshot (not screenshots) for element discovery. Wait for page loads between steps.

## Build one minimal workflow
1. Click 'Create workflow' (or 'Create automation'). Choose to start from a blank workflow.
2. When prompted to choose how the workflow starts, pick the trigger whose label is '${TRIGGER_LABEL}'. If that exact label is not present, pick the closest NG-SIEM / EDR detection event trigger and note the exact label you chose.
3. Add exactly ONE simple action after the trigger so the workflow can be saved — the simplest available (for example a 'Create variable' action, or any no-config action). Configure only what is strictly required to save.
4. Give the workflow the name 'trigger-shape-probe'.
5. Save the workflow as a draft. Do NOT publish and do NOT turn it On.

## Export and report
6. Use the workflow's 'Export' option (often under a '...' / actions menu) to download or display the workflow YAML/JSON definition.
7. If the export downloads a file, open/read it. If it displays inline, read it from the page.
8. Print the COMPLETE exported definition VERBATIM between these exact fences, with nothing else between them:
---BEGIN-EXPORT---
<the full exported YAML or JSON here>
---END-EXPORT---

## Cleanup
9. Delete the 'trigger-shape-probe' workflow you created (it was only a probe).

## Final line
After the fenced export, print one line of valid JSON:
{\"trigger_label_used\":\"...\",\"exported\":true|false,\"deleted\":true|false,\"notes\":\"...\"}"

env -u CLAUDECODE claude -p "$PROMPT" \
  --dangerously-skip-permissions \
  --verbose --output-format stream-json \
  > "$LOG_FILE" 2>&1 || true

# Recover the exported definition from between the fences. The streamed log is
# JSON lines, so the fenced block may be embedded inside a JSON string with
# escaped newlines; try a raw extraction first, then a de-escaped fallback.
EXPORT_YAML="$(awk '/---BEGIN-EXPORT---/{f=1;next} /---END-EXPORT---/{f=0} f' "$LOG_FILE" 2>/dev/null)"

if [ -z "$EXPORT_YAML" ]; then
  # Fallback: pull assistant text out of the stream-json log, de-escape it,
  # then slice between the fences.
  EXPORT_YAML="$(jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text' "$LOG_FILE" 2>/dev/null \
    | awk '/---BEGIN-EXPORT---/{f=1;next} /---END-EXPORT---/{f=0} f')"
fi

if [ -n "$EXPORT_YAML" ]; then
  printf '%s\n' "$EXPORT_YAML" > "$OUT_PATH"
  printf "%bExported definition saved:%b %s\n" "$GREEN" "$RESET" "$OUT_PATH"
  printf "  (full agent log: %s)\n" "$LOG_FILE"
  exit 0
fi

printf "%bNo fenced export found in the agent output.%b\n" "$RED" "$RESET" >&2
printf "  Read the full log to see where it stopped: %s\n" "$LOG_FILE" >&2
exit 1
