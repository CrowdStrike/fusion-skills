#!/usr/bin/env bash
#
# test-skill-scorecard.sh — Unit tests for test-skill.sh scorecard helpers
#
# Tests the count_anti_patterns function that feeds the AUTHORING DISCIPLINE
# scorecard block and the anti_pattern_counts JSON. Fast, no API calls needed.
#
# The function is mirrored here (not sourced) because test-skill.sh runs its
# run-loop at import time — the same approach test-scorecard-parser.sh uses for
# verify-workflows.sh. Keep this copy in sync with test-skill.sh.
#
# Usage: ./test-skill-scorecard.sh
#
set -uo pipefail

PASS=0
FAIL=0
TOTAL=0

if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  GREEN=""; RED=""; BOLD=""; RESET=""
fi

# ── count_anti_patterns: mirrors the function in test-skill.sh ──
# One point each for: a PLACEHOLDER_* marker, a bare `$Node.output` reference
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

# ── Test helpers ──
assert_eq() {
  local test_name="$1" expected="$2" actual="$3"
  TOTAL=$((TOTAL + 1))
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf "${RED}  FAIL: %s${RESET}\n" "$test_name"
    printf "    expected: '%s'\n" "$expected"
    printf "    actual:   '%s'\n" "$actual"
  fi
}

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Write a workflow fixture and echo its path.
mkwf() {
  local name="$1" body="$2"
  local path="$TMP_DIR/$name"
  printf '%s\n' "$body" > "$path"
  echo "$path"
}

# ══════════════════════════════════════════════════════════════
# Clean workflow: real hex id + version_constraint, proper data refs
# ══════════════════════════════════════════════════════════════
printf "${BOLD}Clean workflow (0 anti-patterns)${RESET}\n"

CLEAN=$(mkwf clean.yaml 'trigger:
  type: On demand
actions:
  Enrich:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    name: Enrich
    version_constraint: ~1
    properties:
      note: ${data['"'"'Enrich.output'"'"']}')
assert_eq "clean workflow scores 0" "0" "$(count_anti_patterns "$CLEAN")"

# ══════════════════════════════════════════════════════════════
# Each anti-pattern individually
# ══════════════════════════════════════════════════════════════
printf "${BOLD}Individual anti-patterns${RESET}\n"

PLACEHOLDER=$(mkwf placeholder.yaml 'trigger:
  type: On demand
actions:
  Enrich:
    id: PLACEHOLDER_ACTION_ID
    name: Enrich
    version_constraint: ~1')
assert_eq "PLACEHOLDER marker scores 1" "1" "$(count_anti_patterns "$PLACEHOLDER")"

BARE_REF=$(mkwf bareref.yaml 'trigger:
  type: On demand
actions:
  Enrich:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    name: Enrich
    version_constraint: ~1
    properties:
      note: $Enrich.output')
assert_eq "bare \$Node.output scores 1" "1" "$(count_anti_patterns "$BARE_REF")"

NO_VC=$(mkwf novc.yaml 'trigger:
  type: On demand
actions:
  Enrich:
    id: cdf5c3e0d69f156eaaf56c1f5d3f1b66
    name: Enrich')
assert_eq "hex id without version_constraint scores 1" "1" "$(count_anti_patterns "$NO_VC")"

# ══════════════════════════════════════════════════════════════
# Multiple anti-patterns stack
# ══════════════════════════════════════════════════════════════
printf "${BOLD}Stacked anti-patterns${RESET}\n"

# PLACEHOLDER id (not hex, so the no-version_constraint check does not fire) plus
# a bare output ref = 2 points.
TWO=$(mkwf two.yaml 'trigger:
  type: On demand
actions:
  Enrich:
    id: PLACEHOLDER_ACTION_ID
    name: Enrich
    properties:
      note: $Enrich.output')
assert_eq "placeholder + bare ref scores 2" "2" "$(count_anti_patterns "$TWO")"

# ══════════════════════════════════════════════════════════════
# Edge cases: missing file / empty arg
# ══════════════════════════════════════════════════════════════
printf "${BOLD}Edge cases${RESET}\n"

assert_eq "empty arg scores 0"        "0" "$(count_anti_patterns "")"
assert_eq "nonexistent file scores 0" "0" "$(count_anti_patterns "$TMP_DIR/does-not-exist.yaml")"

# ── count_deploy_churn: mirrors the function in test-skill.sh ──
# One point per occurrence of: an inline-FalconPy escape hatch (python -c/heredoc
# calling update_definition/delete_definition), a release-time gateway validation
# failure, or an API 500. Reads a run's stream-json LOG, not a workflow file.
# Failure signals are matched only within tool OUTPUT, and only within tool
# output that did NOT come from a Read or Skill call — skill reference docs quote
# these very phrases to warn against them, so a doc read must not count as churn.
count_deploy_churn() {
  local log_file="$1"
  local count=0
  [ -n "$log_file" ] && [ -f "$log_file" ] || { echo 0; return; }
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
  count=$((count + $(grep -cE '\.(update_definition|delete_definition)\(' "$log_file" 2>/dev/null)))
  count=$((count + $(printf '%s' "$tool_output" | grep -cE 'has no condition set|not marked as default|release.*fail|enable.*fail' 2>/dev/null)))
  count=$((count + $(printf '%s' "$tool_output" | grep -cE '"status_code": *500|Internal Server Error|status 500' 2>/dev/null)))
  echo "$count"
}

# Write a log fixture and echo its path.
mklog() {
  local name="$1" body="$2"
  local path="$TMP_DIR/$name"
  printf '%s\n' "$body" > "$path"
  echo "$path"
}

# Write a stream-json log fixture: one Bash tool_use + its tool_result carrying
# `body`, so failure signals in `body` are seen as real command output. Optional
# 3rd arg embeds a Read tool_use + tool_result whose content is `docbody`, to
# model the model reading a skill doc that quotes a failure phrase.
mkstreamlog() {
  local name="$1" body="$2" docbody="${3:-}"
  local path="$TMP_DIR/$name"
  {
    printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"bash1","name":"Bash","input":{"command":"import_workflows.py"}}]}}'
    jq -cn --arg t "$body" '{type:"user",message:{content:[{type:"tool_result",tool_use_id:"bash1",content:[{type:"text",text:$t}]}]}}'
    if [ -n "$docbody" ]; then
      printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"read1","name":"Read","input":{"file_path":"skills/workflows/references/yaml-schema.md"}}]}}'
      jq -cn --arg t "$docbody" '{type:"user",message:{content:[{type:"tool_result",tool_use_id:"read1",content:[{type:"text",text:$t}]}]}}'
    fi
  } > "$path"
  echo "$path"
}

printf "${BOLD}Deploy churn signals${RESET}\n"

CLEAN_LOG=$(mkstreamlog clean.log 'Imported — ID: cdf5c3e0d69f156eaaf56c1f5d3f1b66
Released — workflow is now enabled.')
assert_eq "clean deploy log scores 0" "0" "$(count_deploy_churn "$CLEAN_LOG")"

ESCAPE_LOG=$(mklog escape.log 'python3 - <<EOF
client.update_definition(id="x", data=...)
EOF')
assert_eq "inline update_definition escape hatch scores 1" "1" "$(count_deploy_churn "$ESCAPE_LOG")"

ESCAPE_C_LOG=$(mklog escapec.log 'python -c "client.delete_definition(id=x)"')
assert_eq "inline -c delete_definition escape hatch scores 1" "1" "$(count_deploy_churn "$ESCAPE_C_LOG")"

RELEASE_FAIL_LOG=$(mkstreamlog relfail.log 'exclusive gateway "gw" outgoing flow has no condition set and is not marked as default')
assert_eq "release-time gateway failure scores 1" "1" "$(count_deploy_churn "$RELEASE_FAIL_LOG")"

FIVE_HUNDRED_LOG=$(mkstreamlog fivehundred.log 'API returned "status_code": 500 Internal Server Error')
assert_eq "500 response scores 1" "1" "$(count_deploy_churn "$FIVE_HUNDRED_LOG")"

# The critical guard: a log that merely QUOTES the prohibition (e.g. the skill
# instruction "never call update_definition via python -c") must NOT be flagged.
# The regex requires the python invocation and the API call on the SAME line as
# an actual command, so prose describing them across lines stays at 0.
DOC_TEXT_LOG=$(mklog doctext.log 'The deployment skill says: never patch a deployed
definition with a hand-rolled inlineFalconPy call. Do not use update_definition
or delete_definition to hand-edit a deployed copy. The supported update path is
to fix the YAML and re-import.')
assert_eq "prose quoting the prohibition scores 0 (no false positive)" "0" "$(count_deploy_churn "$DOC_TEXT_LOG")"

# The other critical guard (the one this fix adds): a Read of a skill reference
# doc whose content QUOTES a release-failure phrase must NOT count as churn. The
# gateway phrase lives in a Read tool_result, so it is excluded; the Bash output
# for the same run is clean, so the run scores 0.
GATEWAY_DOC_READ_LOG=$(mkstreamlog gatewaydoc.log 'Imported — ID: abc123
Released — workflow is now enabled.' 'Release rejects it: exclusive gateway "<join>" outgoing flow has no condition set and is not marked as default. Do NOT insert a synthetic join node.')
assert_eq "reading a doc that quotes the gateway failure scores 0 (no false positive)" "0" "$(count_deploy_churn "$GATEWAY_DOC_READ_LOG")"

# And the converse: a real gateway failure in Bash output STILL counts, even when
# a doc read in the same run also quotes the phrase. Real signal is not masked.
REAL_PLUS_DOC_LOG=$(mkstreamlog realplusdoc.log 'exclusive gateway "gw" has no condition set and is not marked as default' 'Docs: a gateway with no condition set fails at release.')
assert_eq "real gateway failure still counts despite a doc quote in the same run" "1" "$(count_deploy_churn "$REAL_PLUS_DOC_LOG")"

STACKED_LOG=$(mkstreamlog stacked.log 'python -c "client.update_definition(id=x)"
API returned status 500
exclusive gateway has no condition set')
# All three signals in one Bash tool_result: the escape-hatch call (matched
# against the full log) + the 500 + the gateway failure (matched within tool
# output). A genuinely churny run should sum them.
assert_eq "stacked churn signals sum" "3" "$(count_deploy_churn "$STACKED_LOG")"

assert_eq "empty churn arg scores 0"        "0" "$(count_deploy_churn "")"
assert_eq "nonexistent churn log scores 0"  "0" "$(count_deploy_churn "$TMP_DIR/nope.log")"

# ── check_run_status: mirrors the deploy-mode branch in test-skill.sh ──
# The single source of truth for a run's verdict. The detail section and the
# scorecard both call it, so they can never disagree. These tests pin the
# deploy-mode text-scan rules — in particular that a log which only MENTIONS a
# workflow id or the word "deployed" (but has no import-success line and no
# SUCCESS summary) is FAILED, not a false "LIKELY SUCCESS". That mismatch is the
# bug this guards against.
check_run_status_deploy() {
  # Deploy-mode portion only (workflow file already known to exist).
  local text_file="$1"
  if grep -qE '"deploy_status"\s*:\s*"SUCCESS"' "$text_file" 2>/dev/null; then
    echo "DEPLOYED"; return
  fi
  if grep -qi "imported — id\|import.*success" "$text_file" 2>/dev/null; then
    echo "DEPLOYED"; return
  fi
  echo "FAILED"
}

printf "${BOLD}Run verdict (deploy mode)${RESET}\n"

SUCCESS_SUMMARY=$(mklog success_summary.log '{"deploy_status": "SUCCESS", "workflow_id": "abc"}')
assert_eq "SUCCESS summary -> DEPLOYED" "DEPLOYED" "$(check_run_status_deploy "$SUCCESS_SUMMARY")"

IMPORT_LINE=$(mklog import_line.log 'Imported — ID: cdf5c3e0d69f156eaaf56c1f5d3f1b66')
assert_eq "import-success line -> DEPLOYED" "DEPLOYED" "$(check_run_status_deploy "$IMPORT_LINE")"

# The regression: text that only mentions a workflow id / the word "deployed"
# but never reports import success. The old fallback grepped 'deployed\b' and
# 'workflow.*id' and wrongly printed "LIKELY SUCCESS" while the scorecard said
# FAILED. The single verdict must be FAILED here.
LOOSE_ONLY=$(mklog loose_only.log 'The workflow id will be assigned once deployed.
Attempted import but release validation failed.')
assert_eq "loose mention without import success -> FAILED" "FAILED" "$(check_run_status_deploy "$LOOSE_ONLY")"

EMPTY_LOG=$(mklog empty_verdict.log 'nothing relevant here')
assert_eq "no deploy signal -> FAILED" "FAILED" "$(check_run_status_deploy "$EMPTY_LOG")"

# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
echo ""
printf "${BOLD}=========================================${RESET}\n"
if [ "$FAIL" -eq 0 ]; then
  printf "${GREEN}  ALL %d TESTS PASSED${RESET}\n" "$TOTAL"
else
  printf "${RED}  %d/%d FAILED${RESET}\n" "$FAIL" "$TOTAL"
fi
printf "${BOLD}=========================================${RESET}\n"

exit "$FAIL"
