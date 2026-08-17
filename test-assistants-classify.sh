#!/usr/bin/env bash
#
# test-assistants-classify.sh — unit tests for test-assistants.sh's report reading.
# Fast, no network, launches nothing. Sources the harness as a library
# (FUSION_ASSISTANTS_LIB=1 stops it before any live action) and drives classify()
# against synthetic assistant logs, one per known verdict. This is the anti-false-
# positive core: a self-reported SUCCESS with no evidence must FAIL, a missing
# dependency must be named `deps`, an unresolved ${CLAUDE_PLUGIN_ROOT} must be `root`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/assist-classify.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# Source the harness for its functions only. The guard returns before execution.
export FUSION_ASSISTANTS_LIB=1
# shellcheck source=test-assistants.sh
source "$SCRIPT_DIR/test-assistants.sh"

PASS=0
FAIL=0
check() { # check <desc> <cond-rc>
  if [ "$2" -eq 0 ]; then echo "  ✅ $1"; PASS=$((PASS + 1));
  else echo "  ❌ $1"; FAIL=$((FAIL + 1)); fi
}

# Run classify() on a log body and assert its STATUS|CATEGORY prefix.
# expect_verdict <desc> <rc> <expected STATUS|CATEGORY> <log-body>
expect_verdict() {
  local desc="$1" rc="$2" want="$3" body="$4" log got
  log="$WORK/log.$$"
  printf '%s\n' "$body" > "$log"
  got=$(classify "$log" "$rc")
  local got_prefix="${got%%|*}"                 # STATUS
  local rest="${got#*|}"; got_prefix="$got_prefix|${rest%%|*}"  # STATUS|CATEGORY
  if [ "$got_prefix" = "$want" ]; then check "$desc" 0
  else echo "     got: $got_prefix  want: $want"; check "$desc" 1; fi
}

E2E=0
TIMEOUT=150

echo "== smoke mode =="

expect_verdict "clean run with a script OK passes" 0 "PASS|ok" \
"I loaded the authoring skill and discovered actions.
FUSION-REPORT
STATUS: WORKING
SKILLS: skills/authoring/SKILL.md
COMMANDS: action_search.py => OK, validate.py => OK
BLOCKER: NONE"

expect_verdict "missing falconpy is deps, not a pass" 0 "FAIL|deps" \
"Traceback (most recent call last):
ModuleNotFoundError: No module named 'falconpy'
FUSION-REPORT
STATUS: BLOCKED
SKILLS: skills/authoring/SKILL.md
COMMANDS: action_search.py => FAIL: import error
BLOCKER: could not import falconpy"

expect_verdict "unresolved CLAUDE_PLUGIN_ROOT is root" 0 "FAIL|root" \
'bash: /scripts/python.sh: No such file or directory
FUSION-REPORT
STATUS: BLOCKED
SKILLS: NONE
COMMANDS: NONE
BLOCKER: ${CLAUDE_PLUGIN_ROOT}/scripts/python.sh not found'

expect_verdict "tenant rejecting credentials is auth" 0 "FAIL|auth" \
"401 Unauthorized when calling the Workflows API
FUSION-REPORT
STATUS: BLOCKED
SKILLS: skills/deployment/SKILL.md
COMMANDS: query_workflows.py => FAIL: 401
BLOCKER: 401 Unauthorized"

expect_verdict "rejected flag is flag" 0 "FAIL|flag" \
"validate.py: error: unrecognized arguments: --bogus
FUSION-REPORT
STATUS: BLOCKED
SKILLS: NONE
COMMANDS: validate.py => FAIL: bad flag
BLOCKER: unrecognized arguments"

expect_verdict "self-reported blocker wins even with a script OK" 0 "FAIL|auth" \
"FUSION-REPORT
STATUS: WORKING
SKILLS: skills/authoring/SKILL.md
COMMANDS: action_search.py => OK
BLOCKER: authentication failed against the tenant"

expect_verdict "running out of the time budget is not a blocker" 0 "PASS|ok" \
"FUSION-REPORT
STATUS: WORKING
SKILLS: skills/authoring/SKILL.md
COMMANDS: action_search.py => OK
BLOCKER: ran out of time on the 60-second harness limit"

expect_verdict "no report and a timeout rc is stalled" 124 "FAIL|stalled" \
"I started reading the authoring skill and then"

expect_verdict "reported WORKING but ran nothing is stalled" 0 "FAIL|stalled" \
"FUSION-REPORT
STATUS: WORKING
SKILLS: skills/authoring/SKILL.md
COMMANDS: NONE
BLOCKER: NONE"

# Codex echoes the whole prompt (angle-bracket template lines) into its log. Those
# lines must be dropped, not read as a report.
expect_verdict "prompt echo with angle brackets is not a report" 0 "PASS|ok" \
"STATUS: <one word — WORKING if the scripts are doing real work>
COMMANDS: <comma-separated, every fusion-skills script you ran>
Now my actual reply:
FUSION-REPORT
STATUS: WORKING
SKILLS: skills/authoring/SKILL.md
COMMANDS: action_search.py => OK
BLOCKER: NONE"

# Claude streams stream-json: the report arrives as one escaped JSON string with \n
# for newlines. classify() expands those before matching.
expect_verdict "claude stream-json escaped report parses" 0 "PASS|ok" \
'{"type":"assistant","message":{"content":[{"type":"text","text":"FUSION-REPORT\nSTATUS: WORKING\nSKILLS: skills/authoring/SKILL.md\nCOMMANDS: action_search.py => OK\nBLOCKER: NONE"}]}}'

echo "== e2e mode =="
E2E=1

expect_verdict "e2e with a definition id is deployed" 0 "PASS|deployed" \
"FUSION-REPORT
STATUS: DONE
WORKFLOW: detection-enrichment-claude
DEFINITION: 0123456789abcdef0123456789abcdef
SKILLS: skills/deployment/SKILL.md
COMMANDS: import_workflows.py => OK
BLOCKER: NONE"

expect_verdict "e2e claiming success with no definition id fails" 0 "FAIL|nodeploy" \
"FUSION-REPORT
STATUS: DONE
WORKFLOW: detection-enrichment-claude
DEFINITION: NONE
SKILLS: skills/deployment/SKILL.md
COMMANDS: import_workflows.py => OK
BLOCKER: NONE"

E2E=0

echo "== report_field / blocker_category / is_none =="

got=$(report_field STATUS <<< "STATUS: WORKING")
check "report_field reads a label value" "$([ "$got" = "WORKING" ] && echo 0 || echo 1)"

# The LAST match wins — the report is the end of the reply.
got=$(printf 'STATUS: WORKING\nsome text\nSTATUS: DONE\n' | report_field STATUS)
check "report_field takes the last match" "$([ "$got" = "DONE" ] && echo 0 || echo 1)"

blocker_category "No module named 'yaml'" | grep -qx deps; check "blocker_category: deps" $?
blocker_category "403 Forbidden from the API" | grep -qx auth; check "blocker_category: auth" $?
blocker_category "CLAUDE_PLUGIN_ROOT was empty" | grep -qx root; check "blocker_category: root" $?
blocker_category "workflow name already exists" | grep -qx dupname; check "blocker_category: dupname" $?

is_none "NONE"; check "is_none treats NONE as nothing" $?
is_none ""; check "is_none treats empty as nothing" $?
if is_none "something"; then rc=1; else rc=0; fi; check "is_none is false for real content" "$rc"

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
