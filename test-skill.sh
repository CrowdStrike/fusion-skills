#!/usr/bin/env bash
#
# test-skill.sh — Run the fusion-skills plugin N times unattended, generate +
# validate (and optionally deploy) a Fusion workflow each time, and
# collect structured JSON results.
#
# Pipeline exercised per run: authoring (skill generates workflow YAML) →
# validation (skills/authoring/scripts/validate.py) → deployment
# (skills/deployment/scripts/import_workflows.py) → optional execution check.
#
# Usage:
#   ./test-skill.sh                                    # 5 trials, local plugin
#   ./test-skill.sh --runs 3                           # Run 3 trials
#   ./test-skill.sh --plugin-dir /path/to/plugin       # Use a different plugin dir
#   ./test-skill.sh --no-plugin                        # Run without any plugin
#   ./test-skill.sh --skip-deploy                      # Author + validate only (no live API)
#   ./test-skill.sh --save results.json                # Save results to JSON
#   ./test-skill.sh --baseline prev.json               # Compare against a baseline
#   ./test-skill.sh --save new.json --baseline old.json --runs 5  # Full A/B
# Environment variables:
#   EVAL_MODEL  — Model that generates each workflow (default: opus, the latest
#                 Opus alias). Set to an explicit ID to compare tiers, e.g.
#                 EVAL_MODEL=claude-sonnet-4-5 (a weaker model) or
#                 EVAL_MODEL=claude-sonnet-5 (current Sonnet). Note: the bare
#                 alias "sonnet" now resolves to the latest Sonnet, not a weaker one.
#
set -euo pipefail

RUNS=5
BASE_DIR="/tmp/fusion-skill-test"
SAVE_FILE=""
BASELINE_FILE=""
PLUGIN_DIR="."
NO_PLUGIN=0
SKIP_PLUGIN_MANAGE=0
SKIP_DEPLOY=0
KEEP_WORKFLOWS=0

# Definition IDs created across all runs — deleted at the end unless
# --keep-workflows is set, so deploy runs don't accumulate in the CID.
CREATED_WF_IDS=()

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --save)
      SAVE_FILE="$2"
      shift 2
      ;;
    --baseline)
      BASELINE_FILE="$2"
      if [ ! -f "$BASELINE_FILE" ]; then
        echo "ERROR: Baseline file not found: $BASELINE_FILE"
        exit 1
      fi
      shift 2
      ;;
    --runs)
      RUNS="$2"
      shift 2
      ;;
    --dir)
      BASE_DIR="$2"
      shift 2
      ;;
    --plugin-dir)
      PLUGIN_DIR="$2"
      shift 2
      ;;
    --no-plugin)
      NO_PLUGIN=1
      shift
      ;;
    --skip-plugin-manage)
      SKIP_PLUGIN_MANAGE=1
      shift
      ;;
    --skip-deploy)
      SKIP_DEPLOY=1
      shift
      ;;
    --keep-workflows)
      KEEP_WORKFLOWS=1
      shift
      ;;
    *)
      echo "Usage: $0 [--save <file.json>] [--baseline <file.json>] [--runs N] [--dir <path>] [--plugin-dir <path>] [--no-plugin] [--skip-plugin-manage] [--skip-deploy] [--keep-workflows]"
      exit 1
      ;;
  esac
done
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULT_SCHEMA=$(cat "$SCRIPT_DIR/test-result-schema.json")

# Python used for the repo's own scripts (cleanup, validation). Prefer the
# project venv if present — the system python3 may have an older FalconPy that
# predates delete_definitions, which would make cleanup fail. Fall back to
# python3 on PATH.
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
  PYTHON="python3"
fi

# Skill-namespace isolation. ~/.agents/skills/ is a flat, shared namespace across
# every installed plugin; a sibling repo's skill (e.g. a foreign `setup`) can be
# selected mid-run and skew results. The helper stashes every entry out of the
# way during the run and restores it after (move-only; nothing is ever deleted).
export SKILL_ISO_REPO="$SCRIPT_DIR"
export SKILL_ISO_STASH="${SKILL_ISO_STASH:-/tmp/fusion-skill-stash}"
# shellcheck source=scripts/skill-isolation.sh
source "$SCRIPT_DIR/scripts/skill-isolation.sh"
# Reclaim any stash a prior interrupted run left behind, before touching anything.
recover_orphans

# When --skip-deploy is set, instruct the agent to author + validate only.
if [ "$SKIP_DEPLOY" = "1" ]; then
  DEPLOY_INSTRUCTION="Author the workflow YAML to a file in the current directory and validate it with the authoring validate.py script in --preflight-only mode (no live API call). Do NOT import or deploy."
  EXPECTED_STATUS="NOT_ATTEMPTED"
  # No deploy attempted -> there is no workflow id to report.
  EXAMPLE_WF_ID="N/A"
else
  DEPLOY_INSTRUCTION="Author the workflow YAML to a file in the current directory, validate it, then import it to the CID with the deployment import_workflows.py script. Report the workflow definition ID."
  EXPECTED_STATUS="SUCCESS"
  # Report the REAL definition id returned by import_workflows.py — do not
  # invent or copy this placeholder.
  EXAMPLE_WF_ID="<the definition id returned by import_workflows.py>"
fi

PROMPT="Generate a Falcon Fusion workflow that will trigger from a Falcon Next-Gen SIEM detection. The workflow should hydrate the detection using an event query to get the full details of the detection. If a user, host, domain, url, file indicator, or ip indicator is found, enrich each in parallel using HTTP calls to VirusTotal or DomainTools. Summarize the enrichment across all the threat intelligence providers using an LLM completion action and then send an email formatted in HTML. Use the fusion-skills plugin: discover real action IDs with action_search.py (never guess or use PLACEHOLDER values), choose an appropriate trigger, and write valid workflow YAML with a version_constraint on every action. ${DEPLOY_INSTRUCTION} Pick a reasonable workflow name and proceed without asking me any questions.

When done, respond with valid JSON matching this schema:
${RESULT_SCHEMA}

Example:
{\"workflow_name\":\"detection-enrichment-summary-run-RUN_NUMBER\",\"deploy_status\":\"${EXPECTED_STATUS}\",\"workflow_id\":\"${EXAMPLE_WF_ID}\",\"validation_status\":\"PASS\",\"actions\":[\"query-event\",\"http-request\",\"llm-completion\",\"send-email\"],\"trigger_type\":\"detection\",\"errors\":\"NONE\"}"

# Build --plugin-dir flags (empty array = no plugin loaded)
if [ "$NO_PLUGIN" = "1" ]; then
  PLUGIN_DIR_FLAGS=()
  echo "Plugin: none (--no-plugin)"
elif [ -n "$PLUGIN_DIR" ]; then
  PLUGIN_DIR=$(cd "$PLUGIN_DIR" && pwd)  # Resolve to absolute path (script cd's to run dir later)
  PLUGIN_DIR_FLAGS=(--plugin-dir "$PLUGIN_DIR")
  echo "Plugin dir: $PLUGIN_DIR"
else
  PLUGIN_DIR_FLAGS=()
fi

[ "$SKIP_DEPLOY" = "1" ] && echo "Mode: authoring + validation only (--skip-deploy)"

# Isolate the environment when testing via --plugin-dir:
#   1) stash every ~/.agents/skills entry so a sibling repo's skills can't be
#      selected mid-run, and
#   2) disable installed Fusion marketplace plugins (they override --plugin-dir).
# Everything is restored on exit, INCLUDING on Ctrl-C. Skipped when called from
# run-ab-test.sh (which manages isolation itself).
ENABLED_FUSION_PLUGINS=()
SKILLS_STASHED=0
cleanup_isolation() {
  [ -n "${TIMER_PID:-}" ] && kill "$TIMER_PID" 2>/dev/null || true
  [ "$SKILLS_STASHED" = "1" ] && restore_agents_skills
  if [ ${#ENABLED_FUSION_PLUGINS[@]} -gt 0 ]; then
    echo "Re-enabling Fusion plugins..."
    for p in "${ENABLED_FUSION_PLUGINS[@]}"; do
      echo "  Enabling: $p"
      claude plugin enable "$p" 2>/dev/null || true
    done
  fi
}
if [ "$SKIP_PLUGIN_MANAGE" != "1" ] && [ "$NO_PLUGIN" != "1" ] && [ -n "$PLUGIN_DIR" ]; then
  # Restore on any exit path, and on interrupt (guaranteed-restore requirement).
  trap 'echo ""; cleanup_isolation' EXIT
  trap 'echo ""; cleanup_isolation; exit 130' INT TERM

  # 1) Stash competing skills from the shared ~/.agents/skills namespace.
  stash_all_agents_skills
  SKILLS_STASHED=1

  # 2) Disable installed Fusion marketplace plugins.
  PLUGIN_LIST=$(claude plugin list 2>/dev/null || true)
  while IFS= read -r plugin; do
    if [ -n "$plugin" ] && echo "$PLUGIN_LIST" | grep -A3 "$plugin" | grep -q "enabled"; then
      ENABLED_FUSION_PLUGINS+=("$plugin")
    fi
  done < <(echo "$PLUGIN_LIST" | grep -oE '(fusion|falcon-fusion|crowdstrike-falcon-fusion)@[^ ]*' || true)

  if [ ${#ENABLED_FUSION_PLUGINS[@]} -gt 0 ]; then
    echo "Disabling installed Fusion plugins (using --plugin-dir instead):"
    for plugin in "${ENABLED_FUSION_PLUGINS[@]}"; do
      echo "  Disabling: $plugin"
      claude plugin disable "$plugin" 2>/dev/null || true
    done
  fi
  echo ""
fi

rm -rf "$BASE_DIR"
mkdir -p "$BASE_DIR"

# Extract token counts from a stream-json log file.
# Returns "input_tokens output_tokens" on stdout.
get_tokens() {
  local log_file="$1"
  local input output
  input=$(grep '"type":"assistant"' "$log_file" 2>/dev/null | \
    jq -r '[.message.usage.input_tokens // 0, .message.usage.cache_creation_input_tokens // 0, .message.usage.cache_read_input_tokens // 0] | add' 2>/dev/null | \
    awk '{s+=$1} END {printf "%d", s+0}' || echo "0")
  output=$(grep '"type":"assistant"' "$log_file" 2>/dev/null | \
    jq -r '.message.usage.output_tokens // 0' 2>/dev/null | \
    awk '{s+=$1} END {printf "%d", s+0}' || echo "0")
  echo "$input $output"
}

# Find the generated workflow YAML file under a run directory.
# Returns "NOT FOUND" if no workflow YAML exists. A workflow YAML is a *.yaml/
# *.yml file with a top-level 'trigger:' key (distinguishes it from other YAML).
find_workflow_file() {
  local dir="$1"
  local f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if grep -qE '^trigger:' "$f" 2>/dev/null; then
      echo "$f"
      return
    fi
  done < <(find "$dir" \( -name "*.yaml" -o -name "*.yml" \) -maxdepth 3 2>/dev/null)
  echo "NOT FOUND"
}

# Count authoring anti-patterns in a generated workflow file (Fusion discipline).
# Flags, one point each: a PLACEHOLDER_* marker, a bare `$Node.output` reference
# (should be `${data['Node.output']}`), and a 32-char hex action id with no
# accompanying version_constraint. Echoes the integer count (0 = clean).
count_anti_patterns() {
  local wf_file="$1"
  local count=0
  [ -n "$wf_file" ] && [ -f "$wf_file" ] || { echo 0; return; }
  grep -qE 'PLACEHOLDER_[A-Z_]+' "$wf_file" 2>/dev/null && count=$((count + 1))
  grep -qE '\$[A-Za-z_]+\.output' "$wf_file" 2>/dev/null && count=$((count + 1))
  if grep -qE '^\s*id:\s*[0-9a-f]{32}' "$wf_file" 2>/dev/null \
     && ! grep -qE 'version_constraint:' "$wf_file" 2>/dev/null; then
    count=$((count + 1))
  fi
  echo "$count"
}

# Count deploy-churn signals in a run's stream-json log (deploy discipline).
# These are the escape-hatch behaviors that make a run "DEPLOYED" yet unhealthy —
# the scorecard's PASS count hides them. Flags, one point each occurrence:
#   - inline-FalconPy escape hatch: a `python - <<`/`-c` snippet calling
#     update_definition or delete_definition to hand-patch a deployed def
#     (the #61 prohibition; delete_workflow.py / import_workflows.py are fine)
#   - release-validation failure: "has no condition set", "outgoing flow ... not
#     marked as default", or an explicit release/enable failure
#   - API 500 / Internal Server Error responses
#   - retry-copy creation: importing a second "... v2"/"-run-"/"retry" workflow
#     after a failure instead of fixing the source
# Echoes the integer count (0 = clean deploy).
count_deploy_churn() {
  local log_file="$1"
  local count=0
  [ -n "$log_file" ] && [ -f "$log_file" ] || { echo 0; return; }
  # Scope failure/500 detection to actual TOOL OUTPUT, not the whole transcript,
  # and exclude Read/Skill results. In stream-json a tool's output is a
  # tool_result block inside a user-type event; the model's reasoning is
  # assistant TEXT. But a Read of a skill reference doc is ALSO a tool_result,
  # and those docs quote the very phrases below ("release fails", "has no
  # condition set", "status 500") to warn against them. So scoping to
  # tool_result alone is not enough — a doc read still counts as churn. Join each
  # tool_result to its originating tool_use by id and drop the ones produced by
  # Read or Skill, leaving only script/command output (Bash) where a real
  # failure would actually appear.
  local tool_output
  tool_output=$(jq -rs '
    (map(select(.type=="assistant")
         | .message.content[]?
         | select(.type=="tool_use" and (.name=="Read" or .name=="Skill"))
         | .id)) as $docids
    | map(select(.type=="user")
          | .message.content[]?
          | select(type=="object" and .type=="tool_result"
                   and ((.tool_use_id) as $t | ($docids | index($t)) | not))
          | .content
          | if type=="array" then (.[] | .text? // empty) else (. // empty) end)
    | .[]
  ' "$log_file" 2>/dev/null || echo "")
  # Inline escape hatch: an actual method call `.update_definition(` /
  # `.delete_definition(` (dot before, paren after) — a hand-rolled FalconPy
  # patch of a deployed def. Checked against the FULL log because the call can
  # appear in the assistant's Bash tool_use input, not just tool output; the
  # dot+paren shape still distinguishes a real call from prose that merely names
  # the method (e.g. skill text "do not use update_definition").
  count=$((count + $(grep -cE '\.(update_definition|delete_definition)\(' "$log_file" 2>/dev/null)))
  # Release-time gateway validation failure — only within tool output.
  count=$((count + $(printf '%s' "$tool_output" | grep -cE 'has no condition set|not marked as default|release.*fail|enable.*fail' 2>/dev/null)))
  # Server 500s — only within tool output.
  count=$((count + $(printf '%s' "$tool_output" | grep -cE '"status_code": *500|Internal Server Error|status 500' 2>/dev/null)))
  echo "$count"
}
check_api_health() {
  env -u CLAUDECODE claude -p "Reply with OK" \
    --model haiku > /dev/null 2>&1
  return $?
}

for i in $(seq 1 $RUNS); do
  RUN_DIR="$BASE_DIR/run-$i"
  mkdir -p "$RUN_DIR"
  LOG_FILE="$BASE_DIR/run-$i.log"
  ELAPSED_FILE="$BASE_DIR/run-$i.elapsed"
  RUN_PROMPT="${PROMPT//RUN_NUMBER/$i}"

  echo "========================================="
  echo "  RUN $i of $RUNS"
  echo "  Directory: $RUN_DIR"
  echo "  Log: $LOG_FILE"
  echo "========================================="

  # Pre-flight: verify API is reachable before starting an expensive run.
  echo "  Checking API connectivity..."
  API_OK=false
  for attempt in 1 2 3; do
    if check_api_health "$attempt"; then
      API_OK=true
      echo "  API is reachable."
      break
    fi
    if [ "$attempt" -lt 3 ]; then
      echo "  API unreachable (attempt $attempt/3), retrying in 30s..."
      sleep 30
    fi
  done
  if [ "$API_OK" = false ]; then
    echo "  ERROR: API unreachable after 3 attempts. Aborting remaining runs."
    echo "  Check https://status.claude.com for outages."
    echo '{"error":"API ConnectionRefused"}' > "$LOG_FILE"
    break
  fi

  # Run claude in non-interactive pipe mode, bypassing permission prompts.
  # Use stream-json to capture tool calls for anti-pattern analysis.
  cd "$RUN_DIR"
  RUN_START=$(date +%s)
  env -u CLAUDECODE claude -p "$RUN_PROMPT" \
    ${PLUGIN_DIR_FLAGS[@]+"${PLUGIN_DIR_FLAGS[@]}"} \
    --dangerously-skip-permissions \
    --model "${EVAL_MODEL:-opus}" \
    --verbose \
    --output-format stream-json \
    > "$LOG_FILE" 2>&1 &
  CLAUDE_PID=$!
  # Live elapsed timer (updates every 10s on the same line).
  ( while kill -0 "$CLAUDE_PID" 2>/dev/null; do
      ELAPSED=$(( $(date +%s) - RUN_START ))
      printf "\r  ⏱  %d:%02d elapsed" $((ELAPSED/60)) $((ELAPSED%60))
      sleep 10
    done ) &
  TIMER_PID=$!
  wait "$CLAUDE_PID" 2>/dev/null || true
  kill "$TIMER_PID" 2>/dev/null || true
  wait "$TIMER_PID" 2>/dev/null || true
  RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
  echo "$RUN_ELAPSED" > "$ELAPSED_FILE"
  printf "\r  ⏱  %d:%02d total                \n" $((RUN_ELAPSED/60)) $((RUN_ELAPSED%60))

  # Verify skills loaded from --plugin-dir, not from installed cache.
  if [ "$NO_PLUGIN" != "1" ] && [ -n "$PLUGIN_DIR" ]; then
    RESOLVED_PLUGIN_DIR=$(cd "$PLUGIN_DIR" && pwd)
    SKILL_SOURCE=$(grep -o 'Base directory for this skill: [^\\]*' "$LOG_FILE" 2>/dev/null | head -1 | sed 's/Base directory for this skill: //')
    if [ -n "$SKILL_SOURCE" ]; then
      if echo "$SKILL_SOURCE" | grep -q "plugins/cache"; then
        echo ""
        echo "  ❌ FATAL: Skills loaded from installed cache, not --plugin-dir!"
        echo "     Expected: $RESOLVED_PLUGIN_DIR/..."
        echo "     Got:      $SKILL_SOURCE"
        echo ""
        echo "  The installed marketplace plugin overrides --plugin-dir."
        echo "  Fix: disable the installed plugin before running tests."
        echo "     claude plugin disable crowdstrike-falcon-fusion@fusion-marketplace"
        echo ""
        exit 1
      elif ! echo "$SKILL_SOURCE" | grep -q "$RESOLVED_PLUGIN_DIR"; then
        echo ""
        echo "  ⚠️  WARNING: Skills loaded from unexpected path:"
        echo "     Expected: $RESOLVED_PLUGIN_DIR/..."
        echo "     Got:      $SKILL_SOURCE"
        echo ""
      fi
    fi
  fi

  # Extract and display the JSON result summary from stream-json log.
  grep -o '{"type":"assistant".*' "$LOG_FILE" 2>/dev/null | \
    jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text' 2>/dev/null | \
    python3 -c "
import sys, json, re
text = sys.stdin.read()
# Find JSON objects containing deploy_status
for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*(?:\[[^\[\]]*\][^{}]*)*\}', text):
    try:
        obj = json.loads(m.group())
        if 'deploy_status' in obj:
            print(json.dumps(obj, indent=2))
            break
    except: pass
" 2>/dev/null

  # Extract token usage from stream-json log.
  read -r INPUT_TOKENS OUTPUT_TOKENS <<< "$(get_tokens "$LOG_FILE")"
  TOTAL_TOKENS=$((INPUT_TOKENS + OUTPUT_TOKENS))
  printf "  Token usage: %s input, %s output, %s total\n" \
    "${INPUT_TOKENS:-0}" "${OUTPUT_TOKENS:-0}" "$TOTAL_TOKENS"

  # Capture any workflow definition IDs this run created (from the import
  # script's "Imported — ID: <hex>" line) so we can delete them at the end.
  if [ "$SKIP_DEPLOY" != "1" ]; then
    while IFS= read -r wf_id; do
      # De-dupe: the import script prints each ID twice (per-file line + summary).
      if [ -n "$wf_id" ] && [[ " ${CREATED_WF_IDS[*]-} " != *" $wf_id "* ]]; then
        CREATED_WF_IDS+=("$wf_id")
      fi
    done < <(grep -oE 'Imported — ID: [a-f0-9]{32}' "$LOG_FILE" 2>/dev/null | grep -oE '[a-f0-9]{32}')
  fi

  echo ""
  echo "--- Run $i complete ---"
  echo ""
done

# GROUND TRUTH: snapshot the tenant's workflow definitions NOW — after the runs
# have deployed but BEFORE cleanup deletes them — so a run's verdict is checked
# against what's actually on the tenant, not the agent's self-report. A
# self-reported "SUCCESS" with nothing deployed must fail; a real deploy whose
# report was lost (e.g. a stream-json parse miss) must still pass.
# Uses query_workflows.py --list (read-only), NOT action_search.py (1-hour
# cache, succeeds offline → useless as evidence).
TENANT_DEFS=""
if [ "$SKIP_DEPLOY" != "1" ]; then
  echo "Snapshotting tenant workflow definitions for ground-truth verdicts..."
  TENANT_DEFS=$("$PYTHON" "$SCRIPT_DIR/skills/deployment/scripts/query_workflows.py" --list --json 2>/dev/null || true)
  if [ -z "$TENANT_DEFS" ]; then
    echo "  WARN: could not list tenant definitions; verdicts fall back to run artifacts."
  fi
fi

# Determine a run's deploy/validation status from its result JSON + artifacts.
# Returns one of: DEPLOYED, VALIDATED, FAILED, NOT_CREATED.
#
# This is the single source of truth for a run's verdict — the per-run detail
# section, the scorecard, and the JSON report all call it, so a run can never
# be reported "LIKELY SUCCESS" in one place and "FAILED" in another.
check_run_status() {
  local run_dir="$1" text_file="$2"
  local wf_file
  wf_file=$(find_workflow_file "$run_dir")
  if [ "$wf_file" = "NOT FOUND" ]; then
    echo "NOT_CREATED"; return
  fi
  if [ "$SKIP_DEPLOY" = "1" ]; then
    # Author-only mode: pass if YAML exists and validation reported PASS.
    if grep -qE '"validation_status"\s*:\s*"PASS"' "$text_file" 2>/dev/null; then
      echo "VALIDATED"; return
    fi
    # Fall back to running validate.py preflight on the generated file.
    if python3 "$SCRIPT_DIR/skills/authoring/scripts/validate.py" --preflight-only "$wf_file" >/dev/null 2>&1; then
      echo "VALIDATED"; return
    fi
    echo "FAILED"; return
  fi
  # Deploy mode: verdict is GROUND TRUTH, not the self-report. Find the def ID
  # this run claims (the import script's authoritative "Imported — ID" line
  # first, then a workflow_id in the reported JSON), and confirm it is actually
  # present on the tenant snapshot. A def on the tenant passes even if the
  # report was lost; a reported SUCCESS with no def on the tenant fails.
  local log_file="${run_dir}.log" def_id=""
  def_id=$(grep -oE 'Imported — ID: [a-f0-9]{32}' "$log_file" 2>/dev/null | grep -oE '[a-f0-9]{32}' | head -1 || true)
  if [ -z "$def_id" ]; then
    def_id=$(grep -oE '"workflow_id"[[:space:]]*:[[:space:]]*"[a-f0-9]{32}"' "$text_file" 2>/dev/null | grep -oE '[a-f0-9]{32}' | head -1 || true)
  fi
  if [ -n "$def_id" ] && [ -n "$TENANT_DEFS" ]; then
    if printf '%s' "$TENANT_DEFS" | grep -q "$def_id"; then
      echo "DEPLOYED"; return
    fi
    # Claimed/created a def the tenant cannot show — not a real deploy.
    echo "FAILED"; return
  fi
  # Could not snapshot the tenant (offline): fall back to the artifacts we have,
  # but this is weaker evidence — the WARN above already flagged it.
  if [ -z "$TENANT_DEFS" ] && { [ -n "$def_id" ] || grep -qi "imported — id\|import.*success" "$text_file" 2>/dev/null; }; then
    echo "DEPLOYED"; return
  fi
  echo "FAILED"
}

# Clean up the workflows these runs deployed so the CID doesn't accumulate
# run-1..run-N copies. Deletes exactly the captured definition IDs via the
# Workflows delete API — safer than a name-pattern blanket sweep. Skipped in
# --skip-deploy mode (nothing deployed) or with --keep-workflows.
if [ "$SKIP_DEPLOY" != "1" ] && [ "$KEEP_WORKFLOWS" != "1" ] && [ ${#CREATED_WF_IDS[@]} -gt 0 ]; then
  echo "========================================="
  echo "  CLEANUP: deleting ${#CREATED_WF_IDS[@]} deployed workflow(s)"
  echo "========================================="
  DELETE_PY="$SCRIPT_DIR/skills/deployment/scripts/delete_workflow.py"
  DELETE_ARGS=()
  for wf_id in "${CREATED_WF_IDS[@]}"; do
    DELETE_ARGS+=(--id "$wf_id")
  done
  FUSION_SKILLS_SUPPRESS_CONFIRM=1 "$PYTHON" "$DELETE_PY" "${DELETE_ARGS[@]}" --yes || \
    echo "  NOTE: cleanup reported an error; check for leftover workflows."
  echo ""
fi

echo ""
echo "========================================="
echo "  COMPARISON ACROSS ALL $RUNS RUNS"
echo "========================================="
echo ""

for i in $(seq 1 $RUNS); do
  RUN_DIR="$BASE_DIR/run-$i"
  LOG_FILE="$BASE_DIR/run-$i.log"

  echo "=== Run $i ==="

  # Extract text content and tool calls from stream-json log.
  TEXT_FILE="$BASE_DIR/run-$i.text"
  TOOLS_FILE="$BASE_DIR/run-$i.tools"
  grep -o '{"type":"assistant".*' "$LOG_FILE" 2>/dev/null | \
    jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text' 2>/dev/null > "$TEXT_FILE" || true
  # Extract Bash tool commands.
  grep -o '{"type":"assistant".*' "$LOG_FILE" 2>/dev/null | \
    jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use" and .name=="Bash") | .input.command' 2>/dev/null > "$TOOLS_FILE" || true
  # Extract Skill tool invocations.
  SKILLS_FILE="$BASE_DIR/run-$i.skills"
  grep -o '{"type":"assistant".*' "$LOG_FILE" 2>/dev/null | \
    jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use" and .name=="Skill") | .input.skill' 2>/dev/null > "$SKILLS_FILE" || true

  # Extract reference file reads (Read tool calls to references/*.md).
  REFS_FILE="$BASE_DIR/run-$i.refs"
  grep -o '{"type":"assistant".*' "$LOG_FILE" 2>/dev/null | \
    jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use" and .name=="Read") | .input.file_path' 2>/dev/null | \
    grep '/references/' > "$REFS_FILE" || true

  # Fall back to raw text if stream-json parsing fails.
  if [ ! -s "$TEXT_FILE" ] && [ -f "$LOG_FILE" ]; then
    cp "$LOG_FILE" "$TEXT_FILE"
  fi

  # Check if a workflow YAML file was created.
  WF_FILE=$(find_workflow_file "$RUN_DIR")
  echo "Workflow file: $WF_FILE"

  if [ "$WF_FILE" != "NOT FOUND" ] && [ -f "$WF_FILE" ]; then
    echo ""
    echo "Workflow YAML (first 30 lines):"
    head -30 "$WF_FILE" 2>/dev/null | sed 's|^|  |'
  fi

  echo ""

  # Parse JSON result summary if present.
  RESULT_JSON=$(python3 -c "
import sys, json, re
text = open(sys.argv[1]).read()
for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*(?:\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\][^{}]*)*\}', text):
    try:
        obj = json.loads(m.group())
        if 'deploy_status' in obj:
            print(json.dumps(obj))
            break
    except: pass
" "$TEXT_FILE" 2>/dev/null || true)

  if [ -n "$RESULT_JSON" ]; then
    echo "Structured summary:"
    echo "$RESULT_JSON" | jq -r 'to_entries[] | "  \(.key | ascii_upcase): \(.value | if type == "array" then join(", ") else tostring end)"' 2>/dev/null
  else
    # No JSON summary — report the SAME verdict the scorecard will use, so the
    # detail and scorecard sections never disagree (e.g. "LIKELY SUCCESS" here
    # while the scorecard says FAILED). check_run_status is the single source.
    echo "No structured summary found — deploy verdict from logs + artifacts:"
    case "$(check_run_status "$RUN_DIR" "$TEXT_FILE")" in
      DEPLOYED)    echo "  Deploy: SUCCESS" ;;
      VALIDATED)   echo "  Deploy: VALIDATED (author-only)" ;;
      NOT_CREATED) echo "  Deploy: NO WORKFLOW CREATED" ;;
      *)           echo "  Deploy: FAILED" ;;
    esac
  fi

  # ── Anti-pattern checks (Fusion authoring discipline) ──
  echo ""
  echo "Anti-pattern checks:"

  # PLACEHOLDER values must never appear in the generated workflow YAML.
  if [ "$WF_FILE" != "NOT FOUND" ] && [ -f "$WF_FILE" ] && grep -qE 'PLACEHOLDER_[A-Z_]+' "$WF_FILE" 2>/dev/null; then
    echo "  ❌ Workflow YAML contains PLACEHOLDER_* values (action IDs not resolved)"
  else
    echo "  ✅ No PLACEHOLDER_* values in workflow YAML"
  fi

  # Real action IDs must come from discovery OR the Common Action IDs table.
  # Running action_search.py is one valid path; reusing a verified table ID
  # (no search needed) is another. Only guessed/placeholder IDs are the problem,
  # and those are caught by the PLACEHOLDER and "real action ID" checks below.
  if grep -qi "action_search.py" "$TOOLS_FILE" 2>/dev/null; then
    echo "  ✅ Ran action_search.py to discover real action IDs"
  else
    echo "  ✅ Resolved action IDs without searching (Common Action IDs table)"
  fi

  # Every action needs a version_constraint.
  if [ "$WF_FILE" != "NOT FOUND" ] && [ -f "$WF_FILE" ]; then
    if grep -qE '^\s*id:\s*[0-9a-f]{32}' "$WF_FILE" 2>/dev/null && ! grep -qE 'version_constraint:' "$WF_FILE" 2>/dev/null; then
      echo "  ❌ Actions present but no version_constraint found"
    else
      echo "  ✅ version_constraint present (or no class-based actions)"
    fi
  fi

  # validate.py should be run before delivering.
  if grep -qi "validate.py" "$TOOLS_FILE" 2>/dev/null; then
    echo "  ✅ Ran validate.py"
  else
    echo "  ⚠️  Did not run validate.py"
  fi

  # Orchestrator / authoring skill usage.
  if [ -s "$SKILLS_FILE" ] && grep -qiE "workflows|authoring" "$SKILLS_FILE" 2>/dev/null; then
    echo "  ✅ Used fusion-skills orchestrator/authoring skill"
  else
    echo "  ⚠️  May not have used the fusion-skills orchestrator/authoring skill"
  fi

  # ── Static workflow YAML checks ──
  if [ "$WF_FILE" != "NOT FOUND" ] && [ -f "$WF_FILE" ]; then
    echo ""
    echo "Workflow YAML checks:"
    WF_OK=true

    # Header comment on line 1.
    if head -1 "$WF_FILE" | grep -qE '^#'; then
      echo "  ✅ Header comment present"
    else
      echo "  ⚠️  Missing header comment on line 1"
    fi

    # All action IDs are 32-char hex, or a compound plugin ID (<hex>_<hex> / <hex>~<hex>).
    if grep -qE '^\s*id:\s*' "$WF_FILE" 2>/dev/null; then
      BAD_IDS=$(grep -E '^\s*id:\s*' "$WF_FILE" 2>/dev/null | \
        grep -vE 'id:\s*["'"'"']?[0-9a-f]{32}([_~][0-9a-f]{32})?["'"'"']?\s*$' | \
        grep -vE 'id:\s*(api_integrations|functions)\.' || true)
      if [ -n "$BAD_IDS" ]; then
        echo "  ⚠️  Some action IDs are not valid hex IDs (verify they are real):"
        echo "$BAD_IDS" | sed 's|^|     |'
        WF_OK=false
      else
        echo "  ✅ Action IDs look like real action IDs (32-char hex or compound plugin IDs)"
      fi
    fi

    # CEL data references use the ${data['...']} form, not $action.output.
    if grep -qE '\$[A-Za-z_]+\.output' "$WF_FILE" 2>/dev/null; then
      echo "  ❌ Uses \$action.output syntax — Fusion requires \${data['...']} expressions"
      WF_OK=false
    fi

    [ "$WF_OK" = true ] && echo "  ✅ Workflow YAML looks correct"

    # Pipeline stage markers — a missing mark names the stage that wasn't built,
    # mapping onto the skills the canonical prompt exercises. Read from the
    # generated YAML; the deploy marker is GROUND TRUTH (def on the tenant).
    echo ""
    echo "Pipeline stage markers:"
    grep -qiE 'Inline\.QueryEvent' "$WF_FILE" 2>/dev/null \
      && echo "  ✅ event-query hydration (authoring)" || echo "  ⚠️  event-query hydration — not found"
    grep -qiE 'Inline\.HTTPRequest' "$WF_FILE" 2>/dev/null \
      && echo "  ✅ HTTP enrichment" || echo "  ⚠️  HTTP enrichment — not found"
    grep -qiE 'charlotte|llminvocator|completion' "$WF_FILE" 2>/dev/null \
      && echo "  ✅ LLM completion summary" || echo "  ⚠️  LLM completion summary — not found"
    grep -qiE 'send.?email|msg_type' "$WF_FILE" 2>/dev/null \
      && echo "  ✅ send email" || echo "  ⚠️  send email — not found"
    if [ "$SKIP_DEPLOY" != "1" ]; then
      _dmark=$(grep -oE 'Imported — ID: [a-f0-9]{32}' "$BASE_DIR/run-$i.log" 2>/dev/null | grep -oE '[a-f0-9]{32}' | head -1 || true)
      if [ -n "$_dmark" ] && [ -n "$TENANT_DEFS" ] && printf '%s' "$TENANT_DEFS" | grep -q "$_dmark"; then
        echo "  ✅ deployment (def on tenant)"
      else
        echo "  ⚠️  deployment — no def confirmed on tenant"
      fi
    fi
  fi

  echo ""
  echo "---"
  echo ""
done

# Overall scorecard
echo "========================================="
echo "  SCORECARD"
echo "========================================="
PASS=0
TOTAL=0
for i in $(seq 1 $RUNS); do
  RUN_DIR="$BASE_DIR/run-$i"
  TEXT_FILE="$BASE_DIR/run-$i.text"
  TOTAL=$((TOTAL + 1))
  STATUS=$(check_run_status "$RUN_DIR" "$TEXT_FILE")
  case "$STATUS" in
    DEPLOYED)
      echo "  Run $i: ✅ DEPLOYED"; PASS=$((PASS + 1)) ;;
    VALIDATED)
      echo "  Run $i: ✅ VALIDATED (author-only)"; PASS=$((PASS + 1)) ;;
    FAILED)
      echo "  Run $i: ❌ FAILED" ;;
    *)
      echo "  Run $i: ❌ NO WORKFLOW CREATED" ;;
  esac
done
echo ""
if [ "$SKIP_DEPLOY" = "1" ]; then
  echo "  $PASS/$TOTAL validated"
else
  echo "  $PASS/$TOTAL deployed"
fi
echo ""

# Token usage summary
echo "========================================="
echo "  TOKEN USAGE"
echo "========================================="
for i in $(seq 1 $RUNS); do
  LOG_FILE="$BASE_DIR/run-$i.log"
  read -r INPUT_TOKENS OUTPUT_TOKENS <<< "$(get_tokens "$LOG_FILE")"
  TOTAL_TOKENS=$((INPUT_TOKENS + OUTPUT_TOKENS))
  printf "  Run %d: %7s input, %7s output, %7s total\n" \
    "$i" "${INPUT_TOKENS:-0}" "${OUTPUT_TOKENS:-0}" "$TOTAL_TOKENS"
done
echo ""
echo "Full logs: $BASE_DIR/run-*.log"

# Authoring-discipline summary: surface signals the run already gathers but the
# scorecard above does not — anti-pattern count (0 = clean) and how many skill
# reference docs the agent read. Both are strong quality indicators for an
# author-only run, where "validated" alone does not show *how* clean the YAML is.
echo ""
echo "========================================="
echo "  AUTHORING DISCIPLINE"
echo "========================================="
for i in $(seq 1 "$RUNS"); do
  RUN_DIR="$BASE_DIR/run-$i"
  REFS_FILE="$BASE_DIR/run-$i.refs"
  WF_FILE=$(find_workflow_file "$RUN_DIR")
  [ "$WF_FILE" = "NOT FOUND" ] && WF_FILE=""
  AP=$(count_anti_patterns "$WF_FILE")
  REF_READS=0
  [ -s "$REFS_FILE" ] && REF_READS=$(grep -c . "$REFS_FILE" 2>/dev/null)
  if [ "$AP" -eq 0 ]; then
    printf "  Run %d: ✅ %d anti-patterns, %d reference doc(s) read\n" "$i" "$AP" "$REF_READS"
  else
    printf "  Run %d: ⚠️  %d anti-patterns, %d reference doc(s) read\n" "$i" "$AP" "$REF_READS"
  fi
done
echo ""

# DEPLOY HEALTH — only meaningful when we actually deployed. The SCORECARD above
# counts a run as DEPLOYED even if the agent got there via churn: hand-patching a
# deployed def with an inline FalconPy escape hatch, looping past 500s, or
# tripping release-time gateway validation. A "DEPLOYED" that took 6 escape-hatch
# calls is not a healthy run. Surface those signals per run (0 = clean).
if [ "$SKIP_DEPLOY" != "1" ]; then
  echo "========================================="
  echo "  DEPLOY HEALTH"
  echo "========================================="
  for i in $(seq 1 "$RUNS"); do
    LOG_FILE="$BASE_DIR/run-$i.log"
    CHURN=$(count_deploy_churn "$LOG_FILE")
    if [ "$CHURN" -eq 0 ]; then
      printf "  Run %d: ✅ %d churn signals (clean deploy)\n" "$i" "$CHURN"
    else
      printf "  Run %d: ⚠️  %d churn signal(s) — escape-hatch/500/release-fail (see log)\n" "$i" "$CHURN"
    fi
  done
  echo ""
fi

# Build results JSON
TOKENS_JSON=""
ELAPSED_JSON=""
REFS_JSON=""
SKILLS_JSON=""
ANTI_PATTERN_COUNTS=""
DEPLOY_CHURN_COUNTS=""
for i in $(seq 1 $RUNS); do
  LOG_FILE="$BASE_DIR/run-$i.log"
  TEXT_FILE="$BASE_DIR/run-$i.text"
  TOOLS_FILE="$BASE_DIR/run-$i.tools"
  REFS_FILE="$BASE_DIR/run-$i.refs"
  SKILLS_FILE="$BASE_DIR/run-$i.skills"
  RUN_DIR="$BASE_DIR/run-$i"

  IT=0; OT=0
  read -r IT OT <<< "$(get_tokens "$LOG_FILE")"
  SEP=""; [ "$i" -lt "$RUNS" ] && SEP=","
  TOKENS_JSON="${TOKENS_JSON}    {\"run\": $i, \"input\": ${IT}, \"output\": ${OT}, \"total\": $(( IT + OT ))}${SEP}
"

  # Elapsed time per run.
  ELAPSED_FILE="$BASE_DIR/run-$i.elapsed"
  ELAPSED_S=$(cat "$ELAPSED_FILE" 2>/dev/null || echo "0")
  ELAPSED_JSON="${ELAPSED_JSON}    {\"run\": $i, \"seconds\": ${ELAPSED_S}}${SEP}
"

  # Reference file reads per run.
  if [ -s "$REFS_FILE" ]; then
    REF_LIST=$(jq -R -s 'split("\n") | map(select(. != ""))' "$REFS_FILE" 2>/dev/null || echo "[]")
  else
    REF_LIST="[]"
  fi
  REFS_JSON="${REFS_JSON}    \"run_$i\": ${REF_LIST}${SEP}
"

  # Skill invocations per run.
  if [ -s "$SKILLS_FILE" ]; then
    SKILL_LIST=$(jq -R -s 'split("\n") | map(select(. != ""))' "$SKILLS_FILE" 2>/dev/null || echo "[]")
  else
    SKILL_LIST="[]"
  fi
  SKILLS_JSON="${SKILLS_JSON}    \"run_$i\": ${SKILL_LIST}${SEP}
"

  # Count anti-patterns per run (Fusion authoring discipline).
  WF_FILE=$(find_workflow_file "$RUN_DIR")
  [ "$WF_FILE" = "NOT FOUND" ] && WF_FILE=""
  AP_COUNT=$(count_anti_patterns "$WF_FILE")
  # Note: NOT running action_search.py is no longer an anti-pattern. Action IDs
  # can be resolved from the Common Action IDs table without a live search; what
  # matters is that the final IDs are real (checked via PLACEHOLDER / hex-format
  # checks above), not whether a search command was issued.
  ANTI_PATTERN_COUNTS="${ANTI_PATTERN_COUNTS}${AP_COUNT}${SEP}"

  # Count deploy-churn signals per run (Fusion deploy discipline). Author-only
  # runs never deploy, so churn is 0 there by construction.
  if [ "$SKIP_DEPLOY" = "1" ]; then
    CHURN_COUNT=0
  else
    CHURN_COUNT=$(count_deploy_churn "$LOG_FILE")
  fi
  DEPLOY_CHURN_COUNTS="${DEPLOY_CHURN_COUNTS}${CHURN_COUNT}${SEP}"
done

RESULTS_JSON=$(cat <<ENDJSON
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "git_branch": "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")",
  "git_commit": "$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")",
  "mode": "$([ "$SKIP_DEPLOY" = "1" ] && echo "author-only" || echo "deploy")",
  "runs": $RUNS,
  "passes": $PASS,
  "pass_rate": "$(echo "scale=0; $PASS * 100 / $TOTAL" | bc)%",
  "tokens": [
${TOKENS_JSON}  ],
  "elapsed": [
${ELAPSED_JSON}  ],
  "reference_reads": {
${REFS_JSON}  },
  "skill_invocations": {
${SKILLS_JSON}  },
  "anti_pattern_counts": [${ANTI_PATTERN_COUNTS}],
  "deploy_churn_counts": [${DEPLOY_CHURN_COUNTS}]
}
ENDJSON
)

# Auto-save when --save is specified.
if [ -n "$SAVE_FILE" ]; then
  mkdir -p "$(dirname "$SAVE_FILE")"
  echo "$RESULTS_JSON" > "$SAVE_FILE"
  echo ""
  echo "Results saved to: $SAVE_FILE"
fi

# Compare against baseline if --baseline was provided.
if [ -n "$BASELINE_FILE" ]; then
  echo ""
  echo "========================================="
  echo "  A/B COMPARISON vs BASELINE"
  echo "========================================="
  echo "  Baseline: $BASELINE_FILE"
  echo ""

  B_PASSES=$(jq -r '.passes // .deploys' "$BASELINE_FILE" 2>/dev/null || echo "?")
  B_RUNS=$(jq -r '.runs' "$BASELINE_FILE" 2>/dev/null || echo "?")
  B_RATE=$(jq -r '.pass_rate // .deploy_rate' "$BASELINE_FILE" 2>/dev/null || echo "?")
  B_TOTAL_TOKENS=$(jq -r '[.tokens[].total] | add' "$BASELINE_FILE" 2>/dev/null || echo "?")
  B_AVG_TOKENS=$(jq -r '[.tokens[].total] | add / length | floor' "$BASELINE_FILE" 2>/dev/null || echo "?")
  B_AVG_AP=$(jq -r '[.anti_pattern_counts[]] | add / length' "$BASELINE_FILE" 2>/dev/null || echo "?")
  B_AVG_ELAPSED=$(jq -r 'if .elapsed then [.elapsed[].seconds] | add / length | floor else "?" end' "$BASELINE_FILE" 2>/dev/null || echo "?")

  C_TOTAL_TOKENS=$(echo "$RESULTS_JSON" | jq -r '[.tokens[].total] | add' 2>/dev/null || echo "?")
  C_AVG_TOKENS=$(echo "$RESULTS_JSON" | jq -r '[.tokens[].total] | add / length | floor' 2>/dev/null || echo "?")
  C_AVG_AP=$(echo "$RESULTS_JSON" | jq -r '[.anti_pattern_counts[]] | add / length' 2>/dev/null || echo "?")
  C_AVG_ELAPSED=$(echo "$RESULTS_JSON" | jq -r 'if .elapsed then [.elapsed[].seconds] | add / length | floor else "?" end' 2>/dev/null || echo "?")

  printf "  %-24s %-15s %-15s\n" "" "Baseline" "Current"
  printf "  %-24s %-15s %-15s\n" "---" "---" "---"
  printf "  %-24s %-15s %-15s\n" "Pass rate" "$B_RATE" "$(echo "scale=0; $PASS * 100 / $TOTAL" | bc)%"
  printf "  %-24s %-15s %-15s\n" "Passes" "$B_PASSES/$B_RUNS" "$PASS/$TOTAL"
  printf "  %-24s %-15s %-15s\n" "Total tokens" "$B_TOTAL_TOKENS" "$C_TOTAL_TOKENS"
  printf "  %-24s %-15s %-15s\n" "Avg tokens/run" "$B_AVG_TOKENS" "$C_AVG_TOKENS"
  # Format elapsed as m:ss.
  B_ELAPSED_FMT="?"
  C_ELAPSED_FMT="?"
  if [ "$B_AVG_ELAPSED" != "?" ]; then
    B_ELAPSED_FMT="$(printf "%d:%02d" $((B_AVG_ELAPSED/60)) $((B_AVG_ELAPSED%60)))"
  fi
  if [ "$C_AVG_ELAPSED" != "?" ]; then
    C_ELAPSED_FMT="$(printf "%d:%02d" $((C_AVG_ELAPSED/60)) $((C_AVG_ELAPSED%60)))"
  fi
  printf "  %-24s %-15s %-15s\n" "Avg time/run" "$B_ELAPSED_FMT" "$C_ELAPSED_FMT"
  printf "  %-24s %-15s %-15s\n" "Avg anti-patterns/run" "$B_AVG_AP" "$C_AVG_AP"

  # Calculate token delta.
  if [ "$B_AVG_TOKENS" != "?" ] && [ "$C_AVG_TOKENS" != "?" ]; then
    DELTA=$((C_AVG_TOKENS - B_AVG_TOKENS))
    if [ "$DELTA" -lt 0 ]; then
      PCT=$(echo "scale=1; $DELTA * 100 / $B_AVG_TOKENS" | bc)
      echo ""
      echo "  Token change: ${PCT}% (${DELTA} tokens/run)"
    elif [ "$DELTA" -gt 0 ]; then
      PCT=$(echo "scale=1; $DELTA * 100 / $B_AVG_TOKENS" | bc)
      echo ""
      echo "  Token change: +${PCT}% (+${DELTA} tokens/run)"
    else
      echo ""
      echo "  Token change: 0% (no change)"
    fi
  fi
  echo ""
fi
