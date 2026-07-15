#!/usr/bin/env bash
#
# test-scorecard-parser.sh — Unit tests for verify-workflows.sh scorecard parsing
#
# Tests the parse_workflow_status function against JSON verification reports
# matching verify-result-schema.json. Fast, no API calls needed.
#
# Usage: ./test-scorecard-parser.sh
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

# ── parse_workflow_status: mirrors the JSON extraction in verify-workflows.sh ──
# Looks up a workflow by exact name and returns the requested step field, or
# "N/A" when the report is missing/invalid or the workflow/field is absent.
parse_workflow_status() {
  local field="$1" json="$2" name="${3:-}"
  local result=""
  if echo "$json" | jq -e '.workflows' > /dev/null 2>&1; then
    result=$(echo "$json" | jq -r --arg name "$name" --arg field "$field" \
      '.workflows[] | select(.workflow_name == $name) | .[$field] // empty' 2>/dev/null)
  fi
  echo "${result:-N/A}"
}

# ── is_fail / is_skip: case-insensitive status classification ──
# verify-workflows.sh colors and counts statuses case-insensitively, so the
# parser helpers must treat "fail"/"FAIL" and "skip"/"SKIP" identically.
is_fail() { echo "$1" | grep -qi "FAIL"; }
is_skip() { echo "$1" | grep -qi "SKIP"; }

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

assert_true() {
  local test_name="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf "${RED}  FAIL: %s (expected true)${RESET}\n" "$test_name"
  fi
}

assert_false() {
  local test_name="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@"; then
    FAIL=$((FAIL + 1))
    printf "${RED}  FAIL: %s (expected false)${RESET}\n" "$test_name"
  else
    PASS=$((PASS + 1))
  fi
}

# ══════════════════════════════════════════════════════════════
# JSON format: all fields present, all PASS
# ══════════════════════════════════════════════════════════════
printf "${BOLD}JSON: Basic parsing (all PASS)${RESET}\n"

ALL_PASS_JSON='{"workflows":[
  {"workflow_name":"contain-host-on-detection","run":1,"validate":"PASS","deploy":"PASS","execute":"PASS","results":"PASS","cleanup":"PASS","notes":"Execution completed in 3.2s"},
  {"workflow_name":"severity-routing","run":2,"validate":"PASS","deploy":"PASS","execute":"PASS","results":"PASS","cleanup":"PASS"}
],"summary":"2/2 workflows passed all checks"}'

assert_eq "json: wf1 validate" "PASS" "$(parse_workflow_status validate "$ALL_PASS_JSON" "contain-host-on-detection")"
assert_eq "json: wf1 deploy"   "PASS" "$(parse_workflow_status deploy   "$ALL_PASS_JSON" "contain-host-on-detection")"
assert_eq "json: wf1 execute"  "PASS" "$(parse_workflow_status execute  "$ALL_PASS_JSON" "contain-host-on-detection")"
assert_eq "json: wf1 results"  "PASS" "$(parse_workflow_status results  "$ALL_PASS_JSON" "contain-host-on-detection")"
assert_eq "json: wf1 cleanup"  "PASS" "$(parse_workflow_status cleanup  "$ALL_PASS_JSON" "contain-host-on-detection")"
assert_eq "json: wf2 validate" "PASS" "$(parse_workflow_status validate "$ALL_PASS_JSON" "severity-routing")"

# ══════════════════════════════════════════════════════════════
# JSON format: mixed results (PASS, FAIL, SKIP)
# ══════════════════════════════════════════════════════════════
printf "${BOLD}JSON: Mixed results (PASS, FAIL, SKIP)${RESET}\n"

MIXED_JSON='{"workflows":[
  {"workflow_name":"contain-host-on-detection","run":1,"validate":"PASS","deploy":"PASS","execute":"PASS","results":"PASS","cleanup":"PASS"},
  {"workflow_name":"severity-routing","run":2,"validate":"PASS","deploy":"PASS","execute":"FAIL","results":"SKIP","cleanup":"PASS","notes":"execute: execution did not complete"},
  {"workflow_name":"bad-yaml","run":3,"validate":"FAIL","deploy":"SKIP","execute":"SKIP","results":"SKIP","cleanup":"N/A","notes":"validate: Missing required top-level key trigger"}
],"summary":"1/3 workflows passed all checks"}'

assert_eq "json: wf2 execute FAIL" "FAIL" "$(parse_workflow_status execute "$MIXED_JSON" "severity-routing")"
assert_eq "json: wf2 results SKIP" "SKIP" "$(parse_workflow_status results "$MIXED_JSON" "severity-routing")"
assert_eq "json: wf3 validate FAIL" "FAIL" "$(parse_workflow_status validate "$MIXED_JSON" "bad-yaml")"
assert_eq "json: wf3 deploy SKIP" "SKIP" "$(parse_workflow_status deploy "$MIXED_JSON" "bad-yaml")"
assert_eq "json: wf3 cleanup N/A" "N/A" "$(parse_workflow_status cleanup "$MIXED_JSON" "bad-yaml")"

# ══════════════════════════════════════════════════════════════
# Case-insensitive FAIL / SKIP detection
# ══════════════════════════════════════════════════════════════
printf "${BOLD}Case-insensitive FAIL/SKIP detection${RESET}\n"

assert_true  "is_fail: FAIL"        is_fail "FAIL"
assert_true  "is_fail: fail (lower)" is_fail "fail"
assert_true  "is_fail: Failed"      is_fail "Failed"
assert_false "is_fail: PASS"        is_fail "PASS"
assert_false "is_fail: SKIP"        is_fail "SKIP"
assert_true  "is_skip: SKIP"        is_skip "SKIP"
assert_true  "is_skip: skip (lower)" is_skip "skip"
assert_false "is_skip: PASS"        is_skip "PASS"

# ══════════════════════════════════════════════════════════════
# JSON format: similar workflow names (no greedy matching)
# ══════════════════════════════════════════════════════════════
printf "${BOLD}JSON: Similar workflow names${RESET}\n"

SIMILAR_JSON='{"workflows":[
  {"workflow_name":"contain-host","run":1,"validate":"PASS","deploy":"PASS","execute":"PASS","results":"PASS","cleanup":"PASS"},
  {"workflow_name":"contain-host-on-detection","run":2,"validate":"FAIL","deploy":"SKIP","execute":"SKIP","results":"SKIP","cleanup":"N/A"}
],"summary":"1/2 passed"}'

assert_eq "json: exact name match"  "PASS" "$(parse_workflow_status validate "$SIMILAR_JSON" "contain-host")"
assert_eq "json: longer name match" "FAIL" "$(parse_workflow_status validate "$SIMILAR_JSON" "contain-host-on-detection")"

# ══════════════════════════════════════════════════════════════
# JSON format: validate-only run (deploy/execute/results SKIP)
# ══════════════════════════════════════════════════════════════
printf "${BOLD}JSON: Validate-only run (--skip-deploy)${RESET}\n"

VALIDATE_ONLY_JSON='{"workflows":[
  {"workflow_name":"wf-a","run":1,"validate":"PASS","deploy":"SKIP","execute":"SKIP","results":"SKIP","cleanup":"N/A"},
  {"workflow_name":"wf-b","run":2,"validate":"PASS","deploy":"SKIP","execute":"SKIP","results":"SKIP","cleanup":"N/A"}
],"summary":"2/2 workflows passed all checks"}'

assert_eq "json: validate-only wf-a validate" "PASS" "$(parse_workflow_status validate "$VALIDATE_ONLY_JSON" "wf-a")"
assert_eq "json: validate-only wf-a deploy"   "SKIP" "$(parse_workflow_status deploy   "$VALIDATE_ONLY_JSON" "wf-a")"
assert_eq "json: validate-only wf-b cleanup"  "N/A"  "$(parse_workflow_status cleanup  "$VALIDATE_ONLY_JSON" "wf-b")"

# ══════════════════════════════════════════════════════════════
# N/A fallback: workflow not found or empty/invalid input
# ══════════════════════════════════════════════════════════════
printf "${BOLD}N/A fallback${RESET}\n"

assert_eq "missing: nonexistent workflow" "N/A" "$(parse_workflow_status validate "$ALL_PASS_JSON" "does-not-exist")"
assert_eq "missing: empty json"           "N/A" "$(parse_workflow_status validate "" "wf-a")"
assert_eq "missing: invalid json"         "N/A" "$(parse_workflow_status validate "not json at all" "wf-a")"
assert_eq "missing: no workflows key"     "N/A" "$(parse_workflow_status validate '{"foo":"bar"}' "wf-a")"
assert_eq "missing: unknown field"        "N/A" "$(parse_workflow_status nonsuch "$ALL_PASS_JSON" "contain-host-on-detection")"

# ══════════════════════════════════════════════════════════════
# Optional notes field
# ══════════════════════════════════════════════════════════════
printf "${BOLD}JSON: Notes field${RESET}\n"

assert_eq "json: notes present" "execute: execution did not complete" "$(parse_workflow_status notes "$MIXED_JSON" "severity-routing")"
assert_eq "json: notes absent"  "N/A" "$(parse_workflow_status notes "$ALL_PASS_JSON" "severity-routing")"

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
