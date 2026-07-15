#!/usr/bin/env bash
#
# test-hooks.sh
#
# Unit tests for the fusion-skills hook scripts:
#   - hooks/fusion-skill-router.sh   (intent detection + marker bridge)
#   - hooks/fusion-foundry-bridge.sh (cross-plugin advisory)
#
# Each test feeds JSON on stdin and asserts on stdout / marker state.
# Exits 1 if any test fails.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROUTER="$SCRIPT_DIR/hooks/fusion-skill-router.sh"
BRIDGE="$SCRIPT_DIR/hooks/fusion-foundry-bridge.sh"
MARKER="/tmp/.fusion-skill-router-active"

# Colors (fall back to plain if not a TTY)
if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; NC=$'\033[0m'
else
  GREEN=""; RED=""; NC=""
fi

PASS=0
FAIL=0

pass() { echo "  ${GREEN}PASS${NC}: $1"; PASS=$((PASS + 1)); }
fail() { echo "  ${RED}FAIL${NC}: $1"; FAIL=$((FAIL + 1)); }

# assert_contains <description> <haystack> <needle>
assert_contains() {
  if echo "$2" | grep -qF "$3"; then pass "$1"; else fail "$1 (missing: $3)"; fi
}

# assert_empty <description> <value>
assert_empty() {
  if [ -z "$2" ]; then pass "$1"; else fail "$1 (got: $2)"; fi
}

echo ""
echo "Testing fusion-skill-router.sh"
echo "──────────────────────────────"

# 1. Fusion keyword -> advisory context + marker created
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"create a fusion workflow"}' | bash "$ROUTER")
assert_contains "fusion intent emits advisory context" "$OUT" "FUSION PLUGIN DETECTED"
if [ -f "$MARKER" ]; then pass "fusion intent writes marker file"; else fail "fusion intent writes marker file"; fi

# 2. "automate crowdstrike actions" (verb + noun) -> detected
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"automate crowdstrike actions on detection"}' | bash "$ROUTER")
assert_contains "verb+noun intent detected" "$OUT" "FUSION PLUGIN DETECTED"

# 3. "build a playbook" phrase -> detected
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"build a playbook for ransomware"}' | bash "$ROUTER")
assert_contains "playbook phrase detected" "$OUT" "FUSION PLUGIN DETECTED"

# 4. Non-fusion prompt -> no output, no marker
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"what is the capital of France"}' | bash "$ROUTER")
assert_empty "non-fusion prompt emits no context" "$OUT"
if [ ! -f "$MARKER" ]; then pass "non-fusion prompt writes no marker"; else fail "non-fusion prompt writes no marker"; fi

# 5. PreToolUse with marker present + non-Skill tool -> advisory nudge
echo "$$" > "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' | bash "$ROUTER")
assert_contains "PreToolUse nudges when marker active" "$OUT" "Fusion plugin reminder"

# 6. PreToolUse with Skill tool -> marker cleared, no nudge
echo "$$" > "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Skill"}' | bash "$ROUTER")
assert_empty "Skill invocation emits no nudge" "$OUT"
if [ ! -f "$MARKER" ]; then pass "Skill invocation clears marker"; else fail "Skill invocation clears marker"; fi

# 7. PreToolUse without marker -> no output
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' | bash "$ROUTER")
assert_empty "PreToolUse silent without marker" "$OUT"

echo ""
echo "Testing fusion-foundry-bridge.sh"
echo "────────────────────────────────"

# 8. Foundry skill invoked while fusion intent active -> advise fusion path
echo "$$" > "$MARKER"
OUT=$(echo '{"tool_input":{"skill":"crowdstrike-falcon-foundry:development-workflow"}}' | bash "$BRIDGE")
assert_contains "foundry skill + fusion intent advises fusion path" "$OUT" "STANDALONE Fusion workflow"
rm -f "$MARKER"

# 9. Fusion skill invoked -> advise foundry for app capabilities
OUT=$(echo '{"tool_input":{"skill":"workflows"}}' | bash "$BRIDGE")
assert_contains "fusion skill emits foundry advisory" "$OUT" "Foundry app wrapper"

# 10. lookup-files skill invoked -> advisory emitted
OUT=$(echo '{"tool_input":{"skill":"lookup-files"}}' | bash "$BRIDGE")
assert_contains "lookup-files skill emits advisory" "$OUT" "Cross-plugin note"

# 11. Unrelated skill -> no advisory
OUT=$(echo '{"tool_input":{"skill":"some-other-skill"}}' | bash "$BRIDGE")
assert_empty "unrelated skill emits no advisory" "$OUT"

# Cleanup
rm -f "$MARKER"

echo ""
echo "──────────────────────────────"
echo "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then exit 1; fi
exit 0
