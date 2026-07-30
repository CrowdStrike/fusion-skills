#!/usr/bin/env bash
#
# verify-workflows.sh — Verify that Fusion workflow YAML files actually work.
#
# Two-phase verification, mirroring foundry-skills verify-apps.sh.
#
# Phase 1 (script-based) runs each workflow through up to five steps, each
# calling a fusion-skills Python script:
#
#   1. validate  — skills/authoring/scripts/validate.py --preflight-only   (no creds)
#   2. deploy    — skills/deployment/scripts/import_workflows.py            (creds)
#   3. execute   — skills/execution/scripts/trigger_workflow.py --wait      (creds)
#   4. results   — skills/execution/scripts/get_execution_results.py        (creds)
#   5. cleanup   — bin/cleanup_workflows.py --names (API delete, optional)  (creds)
#
# Phase 2 (browser) runs by DEFAULT, like foundry-skills. It drives the Falcon
# console via a nested `claude` + Playwright MCP and routes each workflow by
# trigger type: Signal/Scheduled/SubModel workflows are RENDER-TESTED (opened in
# the console editor to confirm the graph draws with zero console errors — they
# fire on real events and can't be triggered here), while On-demand workflows are
# EXECUTED after configuring the VirusTotal credential (the part the API cannot
# do). Opt out with --skip-browser (or SKIP_BROWSER=1); it is skipped
# automatically when nothing is deployed (e.g. --skip-deploy), so CI stays
# credential-free. The VirusTotal key is prompted for only when an On-demand
# workflow will execute.
#
# Cleanup deletes through the Workflows delete API (bin/cleanup_workflows.py,
# FalconPy delete_definitions) — no browser needed, just API credentials.
#
# A duplicate-name check (skills/deployment/scripts/query_workflows.py --check-yaml)
# runs before deploy and is recorded in the per-workflow notes.
#
# Each step captures its exit code and output. If a step fails, it is marked
# FAIL and dependent steps are marked SKIP (e.g. deploy fails -> execute and
# results are skipped). The validate-only path (--skip-deploy) needs no
# credentials, so it is safe to run in CI.
#
# Output: a structured JSON report (verify-result-schema.json shape) plus a
# colorized console table.
#
# Usage:
#   ./verify-workflows.sh --dir ./wf                          # full run: script + browser
#   ./verify-workflows.sh --dir ./wf --skip-browser           # script phase only
#   ./verify-workflows.sh --dir ./wf --skip-deploy            # validate only, no creds
#   ./verify-workflows.sh --dir ./wf --cleanup --timeout 90
#   ./verify-workflows.sh --dir ./wf --json | jq .
#
# Flags:
#   --dir PATH       Directory of workflow YAML files to verify
#   --skip-execute   Validate + deploy only (no execution)
#   --skip-deploy    Validate only (no API calls — implies --skip-execute)
#   --skip-browser   Skip Phase 2 browser verification (also: SKIP_BROWSER=1)
#   --cleanup        Delete deployed workflows after verification
#   --json           Emit the JSON report only (progress goes to stderr)
#   --timeout N      Execution poll timeout in seconds (default 60)
#
# Environment variables:
#   VERIFY_MODEL     Model driving Phase 2 browser verification (default: opus, the latest Opus alias; set to sonnet to test with a weaker model)
#
set -uo pipefail

# ── Colors (disabled when stdout is not a TTY or in --json mode) ────────────
if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[1;33m'
  BLUE=$'\033[0;34m'; CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  GREEN=""; RED=""; YELLOW=""; BLUE=""; CYAN=""; BOLD=""; RESET=""
fi

# ── Defaults ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="${FUSION_WF_DIR:-/tmp/fusion-skill-test}"
SKIP_EXECUTE=0
SKIP_DEPLOY=0
DO_CLEANUP=0
JSON_ONLY=0
# Browser verification (Phase 2) runs by default, like foundry-skills. Opt out
# with --skip-browser or SKIP_BROWSER=1.
BROWSER_VERIFY=$( [ "${SKIP_BROWSER:-0}" = "1" ] && echo 0 || echo 1 )
TIMEOUT=60
PYTHON="${PYTHON:-python3}"
# Optional directory of lookup CSVs to verify end to end via CQL match().
LOOKUP_DIR="${FUSION_LOOKUP_DIR:-}"

VALIDATE_PY="$SCRIPT_DIR/skills/authoring/scripts/validate.py"
QUERY_PY="$SCRIPT_DIR/skills/deployment/scripts/query_workflows.py"
IMPORT_PY="$SCRIPT_DIR/skills/deployment/scripts/import_workflows.py"
TRIGGER_PY="$SCRIPT_DIR/skills/execution/scripts/trigger_workflow.py"
RESULTS_PY="$SCRIPT_DIR/skills/execution/scripts/get_execution_results.py"
CLEANUP_PY="$SCRIPT_DIR/bin/cleanup_workflows.py"
VERIFY_LOOKUP_PY="$SCRIPT_DIR/skills/lookup-files/scripts/verify_lookup.py"
# Falcon cloud used to build the console URL for the Phase 2 browser step.
CLEANUP_CLOUD="${FALCON_CLEANUP_CLOUD:-us-2}"

usage() {
  cat <<'EOF'
Usage: verify-workflows.sh [--dir PATH] [--skip-execute] [--skip-deploy]
                           [--skip-browser] [--lookup-dir PATH]
                           [--cleanup] [--json] [--timeout N]

  --dir PATH        Directory of workflow YAML files to verify
  --skip-execute    Validate + deploy only (no execution)
  --skip-deploy     Validate only (no API calls — implies --skip-execute)
  --skip-browser    Skip Phase 2 browser verification (also: SKIP_BROWSER=1).
                    Phase 2 drives the Falcon console (nested `claude` +
                    Playwright MCP), routing by trigger type: Signal/Scheduled/
                    SubModel workflows are render-tested (graph draws with zero
                    console errors), On-demand workflows are executed after
                    configuring the VirusTotal credential. Prompts for
                    VIRUSTOTAL_API_KEY only when an On-demand workflow will
                    execute; needs a console login. Skipped when nothing deployed.
  --lookup-dir PATH Directory of lookup CSVs to verify end to end. Each is
                    uploaded, resolved via a CQL match() round-trip (a known
                    row must come back), then deleted. Needs NGSIEM read+write
                    scope (also: FUSION_LOOKUP_DIR). Skipped if unset.
  --cleanup         Delete deployed workflows after verification
  --json            Emit the JSON report only (progress goes to stderr)
  --timeout N       Execution poll timeout in seconds (default 60)
EOF
}

# ── Argument parsing ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)         BASE_DIR="$2"; shift 2 ;;
    --skip-execute) SKIP_EXECUTE=1; shift ;;
    --skip-deploy) SKIP_DEPLOY=1; SKIP_EXECUTE=1; shift ;;
    --cleanup)     DO_CLEANUP=1; shift ;;
    --json)        JSON_ONLY=1; shift ;;
    --skip-browser) BROWSER_VERIFY=0; shift ;;
    --lookup-dir)  LOOKUP_DIR="$2"; shift 2 ;;
    --timeout)     TIMEOUT="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    *)             echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

REPORT_FILE="$BASE_DIR/verify-workflows.json"

# ── Progress output ─────────────────────────────────────────────────────────
# In --json mode, progress goes to stderr so stdout stays pure JSON for piping.
say() {
  if [ "$JSON_ONLY" = "1" ]; then
    printf "%b\n" "$1" >&2
  else
    printf "%b\n" "$1"
  fi
}

# ── Dependency checks ─────────────────────────────────────────────────────
if ! command -v jq >/dev/null 2>&1; then
  printf "%bERROR: jq is required but not found in PATH.%b\n" "$RED" "$RESET" >&2
  exit 1
fi

# Fail loudly (not per-workflow) if a helper script is missing — otherwise a
# bad path would surface as every workflow "failing" validation, hiding the
# real cause. Only the scripts a given run actually uses are required.
REQUIRED_SCRIPTS=("$VALIDATE_PY")
if [ "$SKIP_DEPLOY" != "1" ]; then
  REQUIRED_SCRIPTS+=("$QUERY_PY" "$IMPORT_PY")
fi
if [ "$SKIP_EXECUTE" != "1" ]; then
  REQUIRED_SCRIPTS+=("$TRIGGER_PY" "$RESULTS_PY")
fi
if [ "$DO_CLEANUP" = "1" ]; then
  REQUIRED_SCRIPTS+=("$CLEANUP_PY")
fi
for script in "${REQUIRED_SCRIPTS[@]}"; do
  if [ ! -f "$script" ]; then
    printf "%bERROR: required script not found: %s%b\n" "$RED" "$script" "$RESET" >&2
    printf "  This is a bug in verify-workflows.sh (wrong path), not a workflow failure.\n" >&2
    exit 1
  fi
done

# ── Discover workflow YAML files ──────────────────────────────────────────
if [ ! -d "$BASE_DIR" ]; then
  printf "%bERROR: directory %s does not exist. Pass --dir PATH.%b\n" "$RED" "$BASE_DIR" "$RESET" >&2
  exit 1
fi

YAML_FILES=()
while IFS= read -r f; do
  [ -n "$f" ] && YAML_FILES+=("$f")
done < <(find "$BASE_DIR" -type f \( -name '*.yaml' -o -name '*.yml' \) 2>/dev/null | sort)

if [ ${#YAML_FILES[@]} -eq 0 ]; then
  printf "%bERROR: no *.yaml/*.yml files found in %s%b\n" "$RED" "$BASE_DIR" "$RESET" >&2
  exit 1
fi

say "${BLUE}==========================================${RESET}"
say "${BLUE}  VERIFY-WORKFLOWS${RESET}"
say "${BLUE}==========================================${RESET}"
say "  Directory:  $BASE_DIR"
say "  Workflows:  ${#YAML_FILES[@]}"
say "  Deploy:     $( [ "$SKIP_DEPLOY" = 1 ] && echo "skipped (--skip-deploy)" || echo "yes" )"
say "  Execute:    $( [ "$SKIP_EXECUTE" = 1 ] && echo "skipped" || echo "yes (timeout ${TIMEOUT}s)" )"
say "  Browser:    $( [ "$BROWSER_VERIFY" = 1 ] && echo "yes (Phase 2)" || echo "skipped (--skip-browser)" )"
say "  Cleanup:    $( [ "$DO_CLEANUP" = 1 ] && echo "yes" || echo "no" )"
say ""

# ── Helpers ───────────────────────────────────────────────────────────────

# Extract the workflow name from a YAML file's top-level `name:` key.
extract_name() {
  sed -n -E "s/^name:[[:space:]]*['\"]?(.+)/\1/p" "$1" 2>/dev/null \
    | head -1 | sed -E "s/['\"][[:space:]]*$//"
}

# Extract the trigger type from a workflow YAML's trigger block. Returns the
# value of the trigger's `type:` field (e.g. "Signal", "On demand", "Scheduled",
# "SubModel"), or empty if none is found.
extract_trigger_type() {
  sed -n -E "s/^[[:space:]]+type:[[:space:]]*['\"]?([A-Za-z ]+).*/\1/p" "$1" 2>/dev/null \
    | grep -m1 -E 'Signal|On demand|Scheduled|SubModel' \
    | sed -E "s/[[:space:]]+$//"
}

# Return 0 if a workflow YAML uses an action that needs a console-configured
# credential (an HTTP request action or an explicit config_id). Such an
# On-demand workflow cannot be API-executed until Phase 2 configures the
# credential in the console; a credential-free one can be executed immediately.
workflow_needs_credential() {
  grep -qiE 'Inline\.HTTPRequest|Cloud HTTP|http_transaction|config_id' "$1" 2>/dev/null
}

# Delete a deployed workflow by NAME via the Workflows delete API.
#
# Fusion exposes a workflow-delete API (FalconPy delete_definitions);
# bin/cleanup_workflows.py resolves the name to its definition ID and deletes it.
# Returns 0 on success; output is the script's report/error.
delete_workflow() {
  local wf_name="$1"
  "$PYTHON" "$CLEANUP_PY" --names "$wf_name" 2>&1
}

# JSON accumulator for per-workflow results.
WORKFLOWS_JSON="[]"
RUN=0

# On-demand workflows whose execution is deferred until Phase 2 configures their
# credential. Each entry is "RUN|WF_NAME|DEF_ID"; the post-browser step executes
# them via the API and folds the verdict back into WORKFLOWS_JSON.
DEFERRED_EXEC=()

# ── Per-workflow verification loop ──────────────────────────────────────────
for wf_file in "${YAML_FILES[@]}"; do
  RUN=$((RUN + 1))
  WF_BASENAME="$(basename "$wf_file")"
  WF_NAME="$(extract_name "$wf_file")"
  [ -z "$WF_NAME" ] && WF_NAME="${WF_BASENAME%.*}"
  WF_TRIGGER_TYPE="$(extract_trigger_type "$wf_file")"

  # Default every step to N/A, then set as we go.
  V_STATUS="N/A"; D_STATUS="N/A"; E_STATUS="N/A"; R_STATUS="N/A"; C_STATUS="N/A"
  NOTES=""
  DEF_ID=""
  EXEC_ID=""

  say "${CYAN}── Run $RUN: $WF_NAME ($WF_BASENAME) ──${RESET}"

  # ── Step 1: Validate (preflight + structural; no credentials needed) ──
  if VALIDATE_OUT="$("$PYTHON" "$VALIDATE_PY" --preflight-only "$wf_file" 2>&1)"; then
    V_STATUS="PASS"
    say "  validate:  ${GREEN}PASS${RESET}"
  else
    V_STATUS="FAIL"
    say "  validate:  ${RED}FAIL${RESET}"
    # Capture the first ERROR line for the notes field.
    FIRST_ERR="$(printf '%s\n' "$VALIDATE_OUT" | grep -m1 -E 'ERROR|FAILED' | sed 's/^[[:space:]]*//')"
    NOTES="validate: ${FIRST_ERR:-validation failed}"
  fi

  # ── Step 2: Deploy ──
  if [ "$SKIP_DEPLOY" = "1" ]; then
    D_STATUS="SKIP"
    say "  deploy:    ${YELLOW}SKIP${RESET} (--skip-deploy)"
  elif [ "$V_STATUS" != "PASS" ]; then
    D_STATUS="SKIP"
    say "  deploy:    ${YELLOW}SKIP${RESET} (validate failed)"
  else
    # Duplicate check. A workflow deployed by a previous verify run stays in the
    # CID unless explicitly deleted, and re-importing the same name fails. That
    # is not a verify failure: the definition IS deployed. If the check reports
    # an existing_id, treat deploy as PASS and reuse that id for the
    # execute/browser phases instead of attempting a doomed import.
    EXISTING_ID=""
    if DUP_OUT="$("$PYTHON" "$QUERY_PY" --check-yaml "$wf_file" --json 2>/dev/null)"; then
      DUP_COUNT="$(printf '%s' "$DUP_OUT" | jq '.duplicates | length' 2>/dev/null || echo 0)"
      if [ "${DUP_COUNT:-0}" -gt 0 ]; then
        EXISTING_ID="$(printf '%s' "$DUP_OUT" | jq -r '.duplicates[0].existing_id // empty' 2>/dev/null)"
        say "  (dup-check: ${YELLOW}already exists${RESET}${EXISTING_ID:+ — reusing ID $EXISTING_ID})"
      fi
    fi

    if [ -n "$EXISTING_ID" ]; then
      # Already deployed on a prior run — reuse it, don't re-import.
      DEF_ID="$EXISTING_ID"
      D_STATUS="PASS"
      NOTES="${NOTES:+$NOTES; }already deployed (reused existing ID)"
      say "  deploy:    ${GREEN}PASS${RESET} (already deployed, ID: $DEF_ID)"
    # Import. validate + duplicate checks are already separate concerns here,
    # so skip them inside the importer to keep each scorecard column distinct.
    elif DEPLOY_OUT="$("$PYTHON" "$IMPORT_PY" --skip-validate --skip-duplicate-check "$wf_file" 2>&1)"; then
      DEF_ID="$(printf '%s\n' "$DEPLOY_OUT" | grep -oE 'ID: [0-9a-f]{32}' | head -1 | sed 's/ID: //')"
      D_STATUS="PASS"
      say "  deploy:    ${GREEN}PASS${RESET} (ID: ${DEF_ID:-unknown})"
    else
      D_STATUS="FAIL"
      FIRST_ERR="$(printf '%s\n' "$DEPLOY_OUT" | grep -m1 -iE 'FAILED|error' | sed 's/^[[:space:]]*//')"
      NOTES="${NOTES:+$NOTES; }deploy: ${FIRST_ERR:-import failed}"
      say "  deploy:    ${RED}FAIL${RESET}"
    fi
  fi

  # ── Step 3: Execute ──
  if [ "$SKIP_EXECUTE" = "1" ]; then
    E_STATUS="SKIP"
    say "  execute:   ${YELLOW}SKIP${RESET}"
  elif [ "$D_STATUS" != "PASS" ]; then
    E_STATUS="SKIP"
    say "  execute:   ${YELLOW}SKIP${RESET} (deploy not successful)"
  elif [ -n "$WF_TRIGGER_TYPE" ] && [ "$WF_TRIGGER_TYPE" != "On demand" ]; then
    # Only On demand workflows can be run via the execute API with empty params.
    # Signal triggers fire on real CrowdStrike events, Scheduled run on a cron,
    # SubModel are invoked by a parent workflow — none can be triggered here
    # without a real/mock event, so this is a SKIP, not a FAIL.
    E_STATUS="SKIP"
    NOTES="${NOTES:+$NOTES; }execute: ${WF_TRIGGER_TYPE} trigger — not API-executable without a real event"
    say "  execute:   ${YELLOW}SKIP${RESET} (${WF_TRIGGER_TYPE} trigger — needs a real event)"
  elif [ -z "$DEF_ID" ]; then
    E_STATUS="SKIP"
    NOTES="${NOTES:+$NOTES; }execute: no definition ID captured"
    say "  execute:   ${YELLOW}SKIP${RESET} (no definition ID)"
  elif workflow_needs_credential "$wf_file"; then
    # On-demand but uses a credentialed action (HTTP / config_id). It can't be
    # executed until Phase 2 configures the credential in the console, so defer
    # the API execute+results to the post-browser step. Recorded PENDING here.
    E_STATUS="PENDING"
    DEFERRED_EXEC+=("${RUN}|${WF_NAME}|${DEF_ID}")
    say "  execute:   ${YELLOW}DEFERRED${RESET} (needs a credential — will execute after Phase 2)"
  else
    EXEC_START="$(date +%s)"
    # Pass empty params so the trigger never drops into interactive mode.
    if EXEC_OUT="$("$PYTHON" "$TRIGGER_PY" --id "$DEF_ID" --params '{}' --wait --timeout "$TIMEOUT" 2>&1)"; then
      EXEC_END="$(date +%s)"
      EXEC_ID="$(printf '%s\n' "$EXEC_OUT" | grep -oE 'Execution ID: .+' | head -1 | sed 's/Execution ID: //' | tr -d '[:space:]')"
      E_STATUS="PASS"
      ELAPSED=$((EXEC_END - EXEC_START))
      NOTES="${NOTES:+$NOTES; }execution completed in ${ELAPSED}s"
      say "  execute:   ${GREEN}PASS${RESET} (${ELAPSED}s, exec: ${EXEC_ID:-unknown})"
    else
      E_STATUS="FAIL"
      FIRST_ERR="$(printf '%s\n' "$EXEC_OUT" | grep -m1 -iE 'FAILED|error|Timeout' | sed 's/^[[:space:]]*//')"
      NOTES="${NOTES:+$NOTES; }execute: ${FIRST_ERR:-execution did not complete}"
      say "  execute:   ${RED}FAIL${RESET}"
    fi
  fi

  # ── Step 4: Results ──
  if [ "$SKIP_EXECUTE" = "1" ]; then
    R_STATUS="SKIP"
    say "  results:   ${YELLOW}SKIP${RESET}"
  elif [ "$E_STATUS" != "PASS" ] || [ -z "$EXEC_ID" ]; then
    R_STATUS="SKIP"
    say "  results:   ${YELLOW}SKIP${RESET} (no successful execution)"
  else
    if RESULT_OUT="$("$PYTHON" "$RESULTS_PY" --execution-id "$EXEC_ID" --json 2>&1)"; then
      STATUS_VAL="$(printf '%s' "$RESULT_OUT" | jq -r '.status // empty' 2>/dev/null | tr '[:upper:]' '[:lower:]')"
      if [ "$STATUS_VAL" = "succeeded" ]; then
        R_STATUS="PASS"
        say "  results:   ${GREEN}PASS${RESET} (succeeded)"
      else
        R_STATUS="FAIL"
        NOTES="${NOTES:+$NOTES; }results: status=${STATUS_VAL:-unknown}"
        say "  results:   ${RED}FAIL${RESET} (status: ${STATUS_VAL:-unknown})"
      fi
    else
      R_STATUS="FAIL"
      NOTES="${NOTES:+$NOTES; }results: could not fetch"
      say "  results:   ${RED}FAIL${RESET} (fetch error)"
    fi
  fi

  # ── Step 5: Cleanup ──
  if [ "$DO_CLEANUP" != "1" ]; then
    # Nothing to clean if we never deployed; otherwise it was left intentionally.
    if [ -n "$DEF_ID" ]; then
      C_STATUS="SKIP"
      say "  cleanup:   ${YELLOW}SKIP${RESET} (--cleanup not set; left deployed)"
    else
      C_STATUS="N/A"
    fi
  elif [ -z "$DEF_ID" ]; then
    C_STATUS="N/A"
    say "  cleanup:   ${YELLOW}N/A${RESET} (nothing deployed)"
  elif [ "$E_STATUS" = "PENDING" ]; then
    # Credential-gated On-demand workflow: its API execute is deferred until
    # after Phase 2 configures the credential. Deleting it now — before that
    # deferred run — would make the execute fail with "definition ... not
    # found". Defer the delete to the post-Phase-2 cleanup, which runs after
    # the deferred execute.
    C_STATUS="PENDING"
    say "  cleanup:   ${YELLOW}DEFERRED${RESET} (will delete after Phase 2 execute)"
  else
    # Delete by NAME via the Workflows delete API (delete_definitions).
    if DEL_OUT="$(delete_workflow "$WF_NAME" 2>&1)"; then
      C_STATUS="PASS"
      say "  cleanup:   ${GREEN}PASS${RESET} (deleted '$WF_NAME' via API)"
    else
      C_STATUS="FAIL"
      FIRST_ERR="$(printf '%s\n' "$DEL_OUT" | grep -m1 -iE 'ERROR|FAIL' | sed 's/^[[:space:]]*//')"
      NOTES="${NOTES:+$NOTES; }cleanup: ${FIRST_ERR:-API delete failed}"
      say "  cleanup:   ${RED}FAIL${RESET}"
    fi
  fi

  say ""

  # ── Build JSON entry ──
  ENTRY="$(jq -n \
    --arg name "$WF_NAME" \
    --argjson run "$RUN" \
    --arg trigger "$WF_TRIGGER_TYPE" \
    --arg validate "$V_STATUS" \
    --arg deploy "$D_STATUS" \
    --arg execute "$E_STATUS" \
    --arg results "$R_STATUS" \
    --arg cleanup "$C_STATUS" \
    --arg notes "$NOTES" \
    '{
      workflow_name: $name,
      run: $run,
      trigger_type: $trigger,
      validate: $validate,
      deploy: $deploy,
      execute: $execute,
      results: $results,
      cleanup: $cleanup
    } + (if $notes == "" then {} else {notes: $notes} end)')"
  WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq --argjson e "$ENTRY" '. + [$e]')"
done

# ── Optional: lookup-file match() verification ──────────────────────────────
# For each CSV in --lookup-dir, prove the lookup works end to end: upload it,
# run a CQL match() query that must return a known row, then delete it. This is
# credential-only (NGSIEM read+write) and needs no browser. A file that uploads
# but can't be resolved by match() FAILS — that is exactly the class of bug the
# skill's old default-domain behavior caused.
LOOKUPS_JSON="[]"
if [ -n "$LOOKUP_DIR" ]; then
  if [ ! -d "$LOOKUP_DIR" ]; then
    say "${YELLOW}Lookup verification skipped: directory not found: $LOOKUP_DIR${RESET}"
  elif [ ! -f "$VERIFY_LOOKUP_PY" ]; then
    say "${YELLOW}Lookup verification skipped: verify_lookup.py not found${RESET}"
  else
    CSV_FILES=()
    while IFS= read -r f; do
      [ -n "$f" ] && CSV_FILES+=("$f")
    done < <(find "$LOOKUP_DIR" -type f -name '*.csv' 2>/dev/null | sort)

    if [ ${#CSV_FILES[@]} -eq 0 ]; then
      say "${YELLOW}Lookup verification skipped: no *.csv in $LOOKUP_DIR${RESET}"
    else
      say ""
      say "${BLUE}  Lookup verification (match() round-trip): ${#CSV_FILES[@]} file(s)${RESET}"
      for csv_file in "${CSV_FILES[@]}"; do
        csv_name="$(basename "$csv_file")"
        LK_OUT="$("$PYTHON" "$VERIFY_LOOKUP_PY" --file "$csv_file" --json 2>&1)"
        LK_OK="$(printf '%s' "$LK_OUT" | jq -r '.success' 2>/dev/null)"
        LK_MSG="$(printf '%s' "$LK_OUT" | jq -r '.message // .error // "no result"' 2>/dev/null)"
        if [ "$LK_OK" = "true" ]; then
          say "  lookup: ${GREEN}PASS${RESET} $csv_name"
          LK_STATUS="PASS"
        else
          say "  lookup: ${RED}FAIL${RESET} $csv_name — $LK_MSG"
          LK_STATUS="FAIL"
        fi
        LK_ENTRY="$(jq -n --arg f "$csv_name" --arg s "$LK_STATUS" --arg m "$LK_MSG" \
          '{lookup: $f, status: $s, notes: $m}')"
        LOOKUPS_JSON="$(printf '%s' "$LOOKUPS_JSON" | jq --argjson e "$LK_ENTRY" '. + [$e]')"
      done
    fi
  fi
fi

# ── Optional Phase 2: browser verification (render-test + execute) ──────────
# Mirrors foundry-skills' verify-apps.sh browser phase. Routes each deployed
# workflow by trigger type, because Fusion has no API to create an HTTP-action
# credential and Signal workflows cannot be triggered without a real event:
#
#   * Signal / Scheduled / SubModel  -> RENDER-TEST: open the workflow in the
#     console editor and confirm the graph draws on the canvas with zero console
#     errors (the #62 "Can not create edge ... nonexistant source" failure). No
#     credential, no execution — the render is the check, exactly as verify-apps
#     render-tests Foundry workflows that need credentialed integrations.
#   * On demand  -> EXECUTE: configure the VirusTotal credential (browser-only),
#     publish, run the workflow, and confirm it succeeds.
#
# Driven by a nested `claude` agent using Playwright MCP (no pip dependency).
# See skills/deployment/references/console-verification.md for the procedure.
if [ "$BROWSER_VERIFY" = "1" ] && [ "$JSON_ONLY" != "1" ]; then
  if ! command -v claude >/dev/null 2>&1; then
    say "${YELLOW}Phase 2 (browser) skipped: 'claude' CLI not found.${RESET}"
  else
    # Split deployed workflows by trigger type. On-demand ones execute (and need
    # the VT credential); everything else is render-tested.
    DEPLOYED_ONDEMAND="$(printf '%s' "$WORKFLOWS_JSON" \
      | jq -r '.[] | select(.deploy == "PASS" and .trigger_type == "On demand") | .workflow_name')"
    DEPLOYED_RENDER="$(printf '%s' "$WORKFLOWS_JSON" \
      | jq -r '.[] | select(.deploy == "PASS" and .trigger_type != "On demand") | .workflow_name')"
    if [ -z "$DEPLOYED_ONDEMAND" ] && [ -z "$DEPLOYED_RENDER" ]; then
      say "${YELLOW}Phase 2 (browser) skipped: no workflows deployed successfully.${RESET}"
    else
      # Prompt for the VirusTotal key ONLY when an On-demand workflow will
      # execute — Signal-only runs just render-test and never use a key.
      if [ -n "$DEPLOYED_ONDEMAND" ] && [ -z "${VIRUSTOTAL_API_KEY:-}" ]; then
        printf "%bVirusTotal API key not set (needed to execute On-demand workflows).%b\n" "$YELLOW" "$RESET" >&2
        read -rsp "  Enter VirusTotal API key (input hidden): " VIRUSTOTAL_API_KEY
        printf "\n" >&2
      fi
      # The browser opens a FRESH context with no saved SSO session, so the
      # login email field is EMPTY. Offer the agent the email to type so it can
      # sign in on its own; otherwise it waits for the human. Set FALCON_LOGIN_EMAIL
      # to skip this prompt.
      LOGIN_EMAIL="${FALCON_LOGIN_EMAIL:-${GITLAB_USER_EMAIL:-}}"
      if [ -z "$LOGIN_EMAIL" ]; then
        printf "%bFalcon login email (the browser opens a fresh session and must sign in).%b\n" "$YELLOW" "$RESET" >&2
        read -rp "  Enter Falcon login email (or leave blank to log in manually): " LOGIN_EMAIL
      fi
      FALCON_URL="${FALCON_URL:-https://$( { [ "$CLEANUP_CLOUD" = us-1 ] && echo falcon.crowdstrike.com; } || { [ "$CLEANUP_CLOUD" = eu-1 ] && echo falcon.eu-1.crowdstrike.com; } || echo falcon.us-2.crowdstrike.com )}"
      BROWSER_LOG="$BASE_DIR/verify-browser.log"

      # Build the per-workflow task list the agent works through.
      RENDER_LIST="$(printf '%s' "$DEPLOYED_RENDER" | sed 's/^/  - /')"
      ONDEMAND_LIST="$(printf '%s' "$DEPLOYED_ONDEMAND" | sed 's/^/  - /')"

      say "${BLUE}  Phase 2 (browser): render-testing Signal workflows, executing On-demand…${RESET}"
      [ -n "$DEPLOYED_RENDER" ]   && say "  Render-test: $(printf '%s' "$DEPLOYED_RENDER" | tr '\n' ' ')"
      [ -n "$DEPLOYED_ONDEMAND" ] && say "  Execute:     $(printf '%s' "$DEPLOYED_ONDEMAND" | tr '\n' ' ')"
      say "  A browser opens at the Falcon console — log in if prompted."
      [ -z "$LOGIN_EMAIL" ] && say "${YELLOW}  No login email given — sign in manually in the browser window when it opens (you have 5 minutes).${RESET}"
      say "  Log: $BROWSER_LOG"

      BROWSER_PROMPT="You are verifying Falcon Fusion workflows in the CrowdStrike Falcon console.

## Login
- Navigate to ${FALCON_URL}/workflow/fusion
- This is a FRESH browser with no saved session, so an Okta/SSO login page will
  appear and the email field will be EMPTY (nothing is pre-filled). Do NOT press
  Enter on an empty field — that does nothing and wastes attempts.
$( [ -n "$LOGIN_EMAIL" ] && printf -- '- Sign in yourself: take a browser_snapshot, click the email textbox, TYPE\n  '\''%s'\'', then submit by pressing Enter (preferred) or clicking Continue/Log-In.\n  An Ember glow overlay can swallow a synthetic button click, so Enter on the\n  focused field is more reliable. After submitting, wait 2-3s and snapshot again;\n  SSO redirects through Okta to the '\''All workflows'\'' page on its own. If a second\n  email/confirm step appears, repeat.' "$LOGIN_EMAIL" || printf -- '- No email was provided, so a HUMAN will sign in manually in the browser window.\n  Do NOT type, click, or press Enter on the login page. Just WAIT and poll: take a\n  browser_snapshot every 15s for UP TO 5 MINUTES until the URL leaves the login/\n  Okta host and the '\''All workflows'\'' page is visible. The human needs time to type\n  their email and complete SSO — do not give up, do not close the browser, do not\n  report failure while still on a login page before the 5 minutes elapse.' )
- Once you reach 'All workflows', proceed. If after login you are NOT on the
  workflows page, navigate to ${FALCON_URL}/workflow/fusion again.

## Browser guidelines
- Use browser_snapshot (not screenshots) for element discovery. Wait for page loads between steps.

## RENDER-TEST these workflows (Signal/Scheduled/SubModel — fire on real events, so NOT executable here):
${RENDER_LIST:-  (none)}

For EACH render-test workflow:
1. Open it from the All workflows list and click 'Edit' to open the editor canvas.
2. Watch the browser console messages while the canvas draws. A RENDER FAILURE is
   a console error like 'Can not create edge X with nonexistant source Y' followed
   by a blank or partial canvas (0 nodes, Test/Save/Publish locked).
3. A PASS = the full workflow graph draws with ZERO console errors and the action
   nodes are visible on the canvas. Record render=PASS or render=FAIL.
4. Do NOT configure credentials or execute these. The render IS the check.

## CONFIGURE + PUBLISH these workflows (On demand — the API executes them after you finish):
${ONDEMAND_LIST:-  (none)}

These workflows will be EXECUTED by the harness over the API once you have
configured their credential and published them — you do NOT execute them here.
Your job is only the part the API cannot do: create the credential and publish.
For EACH one:
1. Open it, click 'Edit', click its 'Cloud HTTP Request' action node to open the Configure panel.
2. In Authentication, open the dropdown ('Create new' / 'Use existing' / 'None').
   - If a 'VirusTotal' configuration already exists under 'Use existing', reuse it — do NOT duplicate.
   - Otherwise 'Create new': name 'VirusTotal', type 'API key', API secret key = the key below, location 'Header', header name 'x-apikey'.
   - SAVING (Ember UI): a synthetic JS click on Save often fires no request. After filling the last field, click into it and press Tab to blur/validate; then focus the Save button and press Enter or Space (a real keyboard event). Confirm success by the panel closing OR a network POST OR the dropdown now showing 'VirusTotal' under 'Use existing'. If Save will not commit after 2 attempts, record configure=FAIL with a note; do not loop.
3. 'Save draft', then 'Publish', then set workflow Status to On. Record configure=PASS once published.
4. Do NOT execute the workflow — the harness runs it over the API and checks the result. Do NOT set Status back to Off (the API needs it On to run).
$( [ -n "$DEPLOYED_ONDEMAND" ] && printf '\n## Credential\n- VirusTotal API key: %s  (Header '\''x-apikey'\'')\n' "${VIRUSTOTAL_API_KEY:-<none provided>}" )

## Output
Respond with valid JSON. Each result reports the check that applied to that workflow:
{\"results\":[{\"workflow\":\"NAME\",\"mode\":\"render|configure\",\"render\":\"PASS|FAIL|NA\",\"configure\":\"PASS|FAIL|NA\",\"verified\":true|false,\"notes\":\"...\"}]}
A render-test workflow is verified=true iff render==PASS. A configure workflow is verified=true iff configure==PASS (credential saved and published)."
      env -u CLAUDECODE claude -p "$BROWSER_PROMPT" \
        --dangerously-skip-permissions \
        --model "${VERIFY_MODEL:-opus}" \
        --verbose --output-format stream-json \
        > "$BROWSER_LOG" 2>&1 || true

      # Merge the agent's verdicts. It emits a JSON object with a "results"
      # array, embedded in the stream-json log with escaped newlines; reconstruct
      # the assistant text first, then find the results blob, falling back to a
      # raw grep. If nothing parseable comes back, treat every attempted workflow
      # as FAIL — "could not verify" is not a pass.
      BROWSER_JSON="$(jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text' "$BROWSER_LOG" 2>/dev/null \
        | grep -oE '\{"results":\[.*\]\}' | tail -1)"
      if [ -z "$BROWSER_JSON" ]; then
        BROWSER_JSON="$(grep -oE '\{"results":\[.*\]\}' "$BROWSER_LOG" 2>/dev/null | tail -1)"
      fi

      # Score every deployed workflow (both lists) from the verdict.
      ALL_VERIFIED="$(printf '%s\n%s' "$DEPLOYED_RENDER" "$DEPLOYED_ONDEMAND")"
      while IFS= read -r wf; do
        [ -z "$wf" ] && continue
        verdict="FAIL"; vnote="browser verify produced no parseable result"
        if [ -n "$BROWSER_JSON" ]; then
          v="$(printf '%s' "$BROWSER_JSON" | jq -r --arg w "$wf" \
               '.results[]? | select(.workflow == $w) | (if .verified == true then "PASS" else "FAIL" end)' 2>/dev/null | head -1)"
          n="$(printf '%s' "$BROWSER_JSON" | jq -r --arg w "$wf" \
               '.results[]? | select(.workflow == $w) | .notes // ""' 2>/dev/null | head -1)"
          [ -n "$v" ] && verdict="$v"
          [ -n "$n" ] && vnote="$n"
        fi
        WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq \
          --arg w "$wf" --arg v "$verdict" --arg n "$vnote" \
          'map(if .workflow_name == $w
               then . + {browser_verify: $v}
                    + (if $n == "" then {} else {notes: ((.notes // "") + (if (.notes // "") == "" then "" else "; " end) + "browser: " + $n)} end)
               else . end)')"
        case "$verdict" in
          PASS) say "  browser:   ${GREEN}PASS${RESET} ($wf)" ;;
          *)    say "  browser:   ${RED}FAIL${RESET} ($wf — $vnote)" ;;
        esac
      done <<< "$ALL_VERIFIED"
      say "${BLUE}  Phase 2 complete — full log at $BROWSER_LOG${RESET}"
    fi
  fi
fi

# ── Post-Phase-2: execute the deferred credential-gated On-demand workflows ──
# These are On-demand workflows that use an HTTP/config_id action; their
# credential now exists (configured in Phase 2), so the API can execute them
# and determine success deterministically — no browser needed for the run
# itself. Skipped when the browser phase did not run (the credential wouldn't
# exist) or nothing was deferred.
if [ "${#DEFERRED_EXEC[@]}" -gt 0 ]; then
  if [ "$BROWSER_VERIFY" != "1" ] || [ "$JSON_ONLY" = "1" ]; then
    say "${YELLOW}  ${#DEFERRED_EXEC[@]} credential-gated workflow(s) not executed (browser phase did not run to configure the credential).${RESET}"
    for entry in "${DEFERRED_EXEC[@]}"; do
      D_NAME="${entry#*|}"; D_NAME="${D_NAME%|*}"
      WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq --arg w "$D_NAME" \
        'map(if .workflow_name == $w then . + {execute: "SKIP", results: "SKIP"} else . end)')"
      # Step 5 deferred this workflow's cleanup so the (now-skipped) execute
      # could have found it. The browser phase did not run, so nothing executed,
      # but --cleanup still means delete it — otherwise it leaks.
      if [ "$DO_CLEANUP" = "1" ]; then
        if DEL_OUT="$(delete_workflow "$D_NAME" 2>&1)"; then
          say "  cleanup:   ${GREEN}PASS${RESET} (deleted '$D_NAME' via API)"
          WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq --arg w "$D_NAME" \
            'map(if .workflow_name == $w then . + {cleanup: "PASS"} else . end)')"
        else
          DC_ERR="$(printf '%s\n' "$DEL_OUT" | grep -m1 -iE 'ERROR|FAIL' | sed 's/^[[:space:]]*//')"
          say "  cleanup:   ${RED}FAIL${RESET} ($D_NAME — ${DC_ERR:-API delete failed})"
          WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq --arg w "$D_NAME" --arg n "${DC_ERR:-}" \
            'map(if .workflow_name == $w
                 then . + {cleanup: "FAIL"}
                      + (if $n == "" then {} else {notes: ((.notes // "") + (if (.notes // "") == "" then "" else "; " end) + "cleanup: " + $n)} end)
                 else . end)')"
        fi
      fi
    done
  else
    say "${BLUE}  Executing ${#DEFERRED_EXEC[@]} credential-gated On-demand workflow(s) via API…${RESET}"
    for entry in "${DEFERRED_EXEC[@]}"; do
      D_NAME="${entry#*|}"; D_NAME="${D_NAME%|*}"
      D_ID="${entry##*|}"
      D_EXEC="FAIL"; D_RESULTS="FAIL"; D_NOTE=""
      # On-demand workflows can declare required trigger inputs (e.g. ip,
      # notify_email). Executing with an empty payload would be rejected at
      # input validation before any action runs, so --autofill fills every
      # required param the workflow declares (email-type fields get LOGIN_EMAIL;
      # ip/domain/hash/etc. get schema-valid indicator values).
      if EXEC_OUT="$("$PYTHON" "$TRIGGER_PY" --id "$D_ID" --autofill ${LOGIN_EMAIL:+--email "$LOGIN_EMAIL"} --wait --timeout "$TIMEOUT" 2>&1)"; then
        EXEC_ID="$(printf '%s\n' "$EXEC_OUT" | grep -oE 'Execution ID: .+' | head -1 | sed 's/Execution ID: //' | tr -d '[:space:]')"
        D_EXEC="PASS"
        if [ -n "$EXEC_ID" ] && RESULT_OUT="$("$PYTHON" "$RESULTS_PY" --execution-id "$EXEC_ID" --json 2>&1)"; then
          STATUS_VAL="$(printf '%s' "$RESULT_OUT" | jq -r '.status // empty' 2>/dev/null | tr '[:upper:]' '[:lower:]')"
          if [ "$STATUS_VAL" = "succeeded" ]; then
            D_RESULTS="PASS"
            say "  execute:   ${GREEN}PASS${RESET} ($D_NAME — succeeded via API after credential config)"
          else
            D_NOTE="results: status=${STATUS_VAL:-unknown}"
            say "  execute:   ${RED}FAIL${RESET} ($D_NAME — status ${STATUS_VAL:-unknown})"
          fi
        else
          D_NOTE="results: could not fetch"
          say "  execute:   ${RED}FAIL${RESET} ($D_NAME — triggered but no results)"
        fi
      else
        D_NOTE="execute: $(printf '%s\n' "$EXEC_OUT" | grep -m1 -iE 'FAILED|error|Timeout' | sed 's/^[[:space:]]*//')"
        say "  execute:   ${RED}FAIL${RESET} ($D_NAME — trigger did not complete)"
      fi
      WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq \
        --arg w "$D_NAME" --arg e "$D_EXEC" --arg r "$D_RESULTS" --arg n "$D_NOTE" \
        'map(if .workflow_name == $w
             then . + {execute: $e, results: $r}
                  + (if $n == "" then {} else {notes: ((.notes // "") + (if (.notes // "") == "" then "" else "; " end) + $n)} end)
             else . end)')"

      # Deferred cleanup: Step 5 left this workflow deployed (C_STATUS=PENDING)
      # so the execute above could find its definition. Now that the run is done,
      # honor --cleanup by deleting it via the delete API.
      if [ "$DO_CLEANUP" = "1" ]; then
        DC_ERR=""
        if DEL_OUT="$(delete_workflow "$D_NAME" 2>&1)"; then
          DC_STATUS="PASS"
          say "  cleanup:   ${GREEN}PASS${RESET} (deleted '$D_NAME' via API)"
        else
          DC_STATUS="FAIL"
          DC_ERR="$(printf '%s\n' "$DEL_OUT" | grep -m1 -iE 'ERROR|FAIL' | sed 's/^[[:space:]]*//')"
          say "  cleanup:   ${RED}FAIL${RESET} ($D_NAME — ${DC_ERR:-API delete failed})"
        fi
        WORKFLOWS_JSON="$(printf '%s' "$WORKFLOWS_JSON" | jq \
          --arg w "$D_NAME" --arg c "$DC_STATUS" --arg n "${DC_ERR:-}" \
          'map(if .workflow_name == $w
               then . + {cleanup: $c}
                    + (if $n == "" then {} else {notes: ((.notes // "") + (if (.notes // "") == "" then "" else "; " end) + "cleanup: " + $n)} end)
               else . end)')"
      fi
    done
  fi
fi

# ── Summary string ───────────────────────────────────────────────────────
# A workflow "passed" if none of its run steps are FAIL (SKIP/N/A are allowed,
# e.g. validate-only runs). PASS in every executed step counts as a pass.
TOTAL=${#YAML_FILES[@]}
PASSED="$(printf '%s' "$WORKFLOWS_JSON" | jq '[.[] | select(
    (.validate != "FAIL") and (.deploy != "FAIL") and
    (.execute != "FAIL") and (.execute != "PENDING") and
    (.results != "FAIL") and (.cleanup != "FAIL") and
    (.browser_verify != "FAIL")
  )] | length')"
SUMMARY="$PASSED/$TOTAL workflows passed all checks"

# ── Write JSON report ─────────────────────────────────────────────────────
REPORT="$(jq -n --argjson workflows "$WORKFLOWS_JSON" --argjson lookups "$LOOKUPS_JSON" \
  --arg summary "$SUMMARY" \
  '{workflows: $workflows, summary: $summary}
   + (if ($lookups | length) > 0 then {lookups: $lookups} else {} end)')"
mkdir -p "$BASE_DIR"
printf '%s\n' "$REPORT" > "$REPORT_FILE"

# ── JSON-only mode: emit report to stdout and stop ──
if [ "$JSON_ONLY" = "1" ]; then
  printf '%s\n' "$REPORT"
  exit 0
fi

# ── Colorized scorecard table ───────────────────────────────────────────────
# Pad plain text to a fixed width, then wrap color so borders stay aligned.
colorize() {
  local status="$1" width="$2" cell color
  cell="$(printf "%-${width}s" "$status")"
  case "$status" in
    PASS) color="$GREEN" ;;
    FAIL) color="$RED" ;;
    SKIP) color="$YELLOW" ;;
    *)    color="$RESET" ;;
  esac
  printf "%s%s%s" "$color" "$cell" "$RESET"
}

WF_W=29
ST_W=8
hr() {  # horizontal rule: $1=left $2=mid $3=right
  printf "%s" "$1"
  printf '─%.0s' $(seq 1 $((WF_W + 2)))
  for _ in 1 2 3 4 5; do printf "%s" "$2"; printf '─%.0s' $(seq 1 $((ST_W + 2))); done
  printf "%s\n" "$3"
}

printf "${BLUE}  VERIFY-WORKFLOWS SCORECARD${RESET}\n"
hr "┌" "┬" "┐"
printf "│ ${BOLD}%-${WF_W}s${RESET} │ ${BOLD}%-${ST_W}s${RESET} │ ${BOLD}%-${ST_W}s${RESET} │ ${BOLD}%-${ST_W}s${RESET} │ ${BOLD}%-${ST_W}s${RESET} │ ${BOLD}%-${ST_W}s${RESET} │\n" \
  "Workflow" "Validate" "Deploy" "Execute" "Results" "Cleanup"
hr "├" "┼" "┤"

printf '%s' "$WORKFLOWS_JSON" | jq -r \
  '.[] | [.workflow_name, .validate, .deploy, .execute, .results, .cleanup] | @tsv' | \
while IFS=$'\t' read -r name v d e r c; do
  # Truncate long workflow names to keep the table aligned.
  disp="$name"
  [ ${#disp} -gt $WF_W ] && disp="${disp:0:$((WF_W - 1))}…"
  printf "│ %-${WF_W}s │ %s │ %s │ %s │ %s │ %s │\n" \
    "$disp" \
    "$(colorize "$v" "$ST_W")" \
    "$(colorize "$d" "$ST_W")" \
    "$(colorize "$e" "$ST_W")" \
    "$(colorize "$r" "$ST_W")" \
    "$(colorize "$c" "$ST_W")"
done
hr "└" "┴" "┘"

printf "\n  ${BOLD}%s${RESET}\n" "$SUMMARY"
printf "  JSON report: %s\n\n" "$REPORT_FILE"

# Non-zero exit if any workflow had a FAIL, so CI can gate on it.
if [ "$PASSED" -lt "$TOTAL" ]; then
  exit 1
fi
exit 0
