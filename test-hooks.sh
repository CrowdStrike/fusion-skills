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

# Reverse order uses the same noun, including the plural.
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"crowdstrike actions we should automate"}' | bash "$ROUTER")
assert_contains "reverse-order plural noun detected" "$OUT" "FUSION PLUGIN DETECTED"

# An explicit skill request may include "the", and it ends at the skill or plugin name.
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"run the fusion skill"}' | bash "$ROUTER")
assert_contains "explicit skill request detected" "$OUT" "FUSION PLUGIN DETECTED"

# 3. "build a playbook" phrase -> detected
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"build a playbook for ransomware"}' | bash "$ROUTER")
assert_contains "playbook phrase detected" "$OUT" "FUSION PLUGIN DETECTED"

# Codex adds turn_id and permission_mode to the shared hook shape. Keep the
# routing instruction host-neutral instead of naming Claude's Skill tool.
rm -f "$MARKER-codex-session"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","session_id":"codex-session","turn_id":"codex-turn","permission_mode":"default","prompt":"create a fusion workflow"}' |
  PLUGIN_ROOT="$SCRIPT_DIR" CLAUDE_PLUGIN_ROOT="$SCRIPT_DIR" bash "$ROUTER")
assert_contains "Codex event uses host-neutral skill routing" "$OUT" "Load and follow the crowdstrike-falcon-fusion workflows orchestrator skill"
if echo "$OUT" | grep -qF "Skill tool"; then fail "Codex event avoids Claude-only Skill tool wording"; else pass "Codex event avoids Claude-only Skill tool wording"; fi
rm -f "$MARKER-codex-session" "$MARKER-codex-session.nudged"

# Cursor names the prompt hook beforeSubmitPrompt and only injects additional_context.
rm -f "$MARKER-cursor-conv"
OUT=$(echo '{"hook_event_name":"beforeSubmitPrompt","conversation_id":"cursor-conv","prompt":"create a fusion workflow"}' |
  CURSOR_PLUGIN_ROOT="$SCRIPT_DIR" bash "$ROUTER")
assert_contains "Cursor event uses additional_context" "$OUT" "\"additional_context\""
if echo "$OUT" | grep -qF "hookSpecificOutput"; then fail "Cursor event avoids Claude hookSpecificOutput"; else pass "Cursor event avoids Claude hookSpecificOutput"; fi
assert_contains "Cursor event routes to the workflows skill" "$OUT" "Load and follow the crowdstrike-falcon-fusion workflows orchestrator skill"
rm -f "$MARKER-cursor-conv" "$MARKER-cursor-conv.nudged"

# 4. Non-fusion prompt -> no output, no marker
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"what is the capital of France"}' | bash "$ROUTER")
assert_empty "non-fusion prompt emits no context" "$OUT"
if [ ! -f "$MARKER" ]; then pass "non-fusion prompt writes no marker"; else fail "non-fusion prompt writes no marker"; fi

# 4a. Loose-match false positives must NOT trigger: the repo name "fusion-skills",
# the plugin name, common verbs (write/run), and a generic "workflow" that isn't
# Fusion work. A verb + a Fusion noun must be near each other, and bare "fusion"
# and generic "create workflow" no longer match.
while IFS= read -r fp; do
  [ -z "$fp" ] && continue
  rm -f "$MARKER"
  OUT=$(jq -n --arg p "$fp" '{hook_event_name:"UserPromptSubmit",prompt:$p}' | bash "$ROUTER")
  assert_empty "no false positive: ${fp:0:45}" "$OUT"
  if [ ! -f "$MARKER" ]; then pass "no marker: ${fp:0:45}"; else fail "no marker: ${fp:0:45}"; fi
done <<'FALSEPOS'
write a file to my desktop describing the issue and I'll tell the agent that works on fusion-skills to fix it
run the tests for the fusion-skills repo
write a changelog entry about the fusion plugin
create workflow docs for the onboarding wiki
monitor soaring cloud costs
the release soared last quarter
notaplaybook deploy today
redeploy to cider
see my_action_search_helper
create an ansible playbook
run fusion plugin tests
FALSEPOS

# 5. PreToolUse with marker present + non-Skill tool -> advisory nudge
rm -f "$MARKER.nudged"
echo "$$" > "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' | bash "$ROUTER")
assert_contains "PreToolUse nudges when marker active" "$OUT" "Fusion plugin reminder"

# 6. PreToolUse with Skill tool -> no nudge, marker kept for the bridge
rm -f "$MARKER.nudged"
echo "$$" > "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Skill"}' | bash "$ROUTER")
assert_empty "Skill invocation emits no nudge" "$OUT"
if [ -f "$MARKER" ]; then pass "Skill invocation keeps marker for the bridge"; else fail "Skill invocation keeps marker for the bridge"; fi
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' | bash "$ROUTER")
assert_empty "no reminder after the Skill call" "$OUT"
rm -f "$MARKER" "$MARKER.nudged"

# 7. PreToolUse without marker -> no output
rm -f "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' | bash "$ROUTER")
assert_empty "PreToolUse silent without marker" "$OUT"

# 7a. Reminder fires once per detected prompt, not on every tool call
rm -f "$MARKER" "$MARKER.nudged"
echo "$$" > "$MARKER"
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' | bash "$ROUTER" >/dev/null
OUT=$(echo '{"hook_event_name":"PreToolUse","tool_name":"Read"}' | bash "$ROUTER")
assert_empty "second tool call gets no repeated reminder" "$OUT"
if [ -f "$MARKER" ]; then pass "marker survives the reminder for the bridge"; else fail "marker survives the reminder for the bridge"; fi

# 7b. A new prompt that doesn't match clears the leftover marker
OUT=$(echo '{"hook_event_name":"UserPromptSubmit","prompt":"what is the capital of France"}' | bash "$ROUTER")
if [ ! -f "$MARKER" ] && [ ! -f "$MARKER.nudged" ]; then pass "non-fusion prompt clears leftover marker"; else fail "non-fusion prompt clears leftover marker"; fi

# 7c. Another session's marker doesn't leak into this one
echo "$$" > "$MARKER"
OUT=$(echo '{"hook_event_name":"PreToolUse","session_id":"other-session","tool_name":"Bash"}' | bash "$ROUTER")
assert_empty "marker from another session emits no reminder" "$OUT"
rm -f "$MARKER" "$MARKER-other-session" "$MARKER-other-session.nudged"

# 7d. Session-scoped marker round-trips from UserPromptSubmit to PreToolUse
SA="$MARKER-sess-a"; SB="$MARKER-sess-b"
rm -f "$SA" "$SA.nudged" "$SB" "$SB.nudged"
echo '{"hook_event_name":"UserPromptSubmit","session_id":"sess-a","prompt":"create a fusion workflow"}' | bash "$ROUTER" >/dev/null
if [ -f "$SA" ]; then pass "prompt with session_id writes the scoped marker"; else fail "prompt with session_id writes the scoped marker"; fi
OUT=$(echo '{"hook_event_name":"PreToolUse","session_id":"sess-a","tool_name":"Bash"}' | bash "$ROUTER")
assert_contains "same session gets the reminder" "$OUT" "Fusion plugin reminder"

# 7e. A second session sees nothing from the first
OUT=$(echo '{"hook_event_name":"PreToolUse","session_id":"sess-b","tool_name":"Bash"}' | bash "$ROUTER")
assert_empty "other session gets no reminder" "$OUT"
OUT=$(echo '{"session_id":"sess-b","tool_input":{"skill":"crowdstrike-falcon-foundry:development-workflow"}}' | bash "$BRIDGE")
assert_empty "other session gets no bridge advisory" "$OUT"

# 7f. The bridge reads the same scoped marker after the router sees the Skill call
echo '{"hook_event_name":"PreToolUse","session_id":"sess-a","tool_name":"Skill"}' | bash "$ROUTER" >/dev/null
OUT=$(echo '{"session_id":"sess-a","tool_input":{"skill":"crowdstrike-falcon-foundry:development-workflow"}}' | bash "$BRIDGE")
assert_contains "bridge sees the scoped marker after a Skill call" "$OUT" "STANDALONE Fusion workflow"

# 7g. Unsafe characters in session_id can't escape the marker filename
rm -f "$MARKER-evil"
echo '{"hook_event_name":"UserPromptSubmit","session_id":"../ev/il","prompt":"create a fusion workflow"}' | bash "$ROUTER" >/dev/null
if [ -f "$MARKER-evil" ]; then pass "session_id is sanitized into the marker name"; else fail "session_id is sanitized into the marker name"; fi
rm -f "$SA" "$SA.nudged" "$SB" "$SB.nudged" "$MARKER-evil" "$MARKER-evil.nudged"

# 7h. Markers older than a day are pruned on the next prompt
STALE="$MARKER-stale-test"
touch -t 202001010000 "$STALE"
echo '{"hook_event_name":"UserPromptSubmit","prompt":"what is the capital of France"}' | bash "$ROUTER" >/dev/null
if [ ! -f "$STALE" ]; then pass "stale session marker is pruned"; else fail "stale session marker is pruned"; fi
rm -f "$STALE"

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

# Codex stores enabled plugins in config.toml. The bridge should recognize the
# sibling there instead of telling the user to install it again.
CODEX_HOME=$(mktemp -d)
mkdir -p "$CODEX_HOME/.codex"
cat > "$CODEX_HOME/.codex/config.toml" <<'EOF'
[plugins."crowdstrike-falcon-foundry@openai-api-curated"]
enabled = true
EOF
OUT=$(echo '{"tool_input":{"skill":"workflows"}}' | HOME="$CODEX_HOME" bash "$BRIDGE")
assert_contains "Codex config recognizes installed Foundry plugin" "$OUT" "foundry-skills plugin is installed"
rm -rf "$CODEX_HOME"

# A Codex turn must not inherit Claude's registry. turn_id is Codex-only; when
# Codex has the sibling disabled, Claude's installed_plugins.json does not count.
BOTH_HOME=$(mktemp -d)
mkdir -p "$BOTH_HOME/.claude/plugins" "$BOTH_HOME/.codex"
cat > "$BOTH_HOME/.claude/plugins/installed_plugins.json" <<'EOF'
{"plugins":{"crowdstrike-falcon-foundry@claude-plugins-official":[{"scope":"user"}]}}
EOF
cat > "$BOTH_HOME/.codex/config.toml" <<'EOF'
[plugins."crowdstrike-falcon-foundry@openai-api-curated"]
enabled = false
EOF
OUT=$(echo '{"turn_id":"codex-turn","tool_input":{"skill":"workflows"}}' | HOME="$BOTH_HOME" bash "$BRIDGE")
if echo "$OUT" | grep -qF "foundry-skills plugin is installed"; then
  fail "Codex disabled sibling ignores Claude registry"
else
  pass "Codex disabled sibling ignores Claude registry"
fi
assert_contains "Codex disabled sibling still names the install command" "$OUT" "/plugins in Codex"
rm -rf "$BOTH_HOME"

# A nested table under a disabled plugin is not the plugin's own enabled flag.
NESTED_HOME=$(mktemp -d)
mkdir -p "$NESTED_HOME/.codex"
cat > "$NESTED_HOME/.codex/config.toml" <<'EOF'
[plugins."crowdstrike-falcon-foundry@openai-api-curated"]
enabled = false

[plugins."crowdstrike-falcon-foundry@openai-api-curated".mcp_servers.example]
enabled = true
EOF
OUT=$(echo '{"turn_id":"codex-turn","tool_input":{"skill":"workflows"}}' | HOME="$NESTED_HOME" bash "$BRIDGE")
if echo "$OUT" | grep -qF "foundry-skills plugin is installed"; then
  fail "Codex nested enabled table is not the plugin"
else
  pass "Codex nested enabled table is not the plugin"
fi
rm -rf "$NESTED_HOME"

# Cursor marketplace installs live in the plugin cache.
CURSOR_HOME=$(mktemp -d)
mkdir -p "$CURSOR_HOME/.cursor/plugins/cache/cursor-public/crowdstrike-falcon-foundry"
OUT=$(echo '{"tool_input":{"skill":"workflows"}}' | HOME="$CURSOR_HOME" bash "$BRIDGE")
assert_contains "Cursor plugin cache recognizes installed Foundry plugin" "$OUT" "foundry-skills plugin is installed"
rm -rf "$CURSOR_HOME"

# 10. lookup-files skill invoked -> advisory emitted
OUT=$(echo '{"tool_input":{"skill":"lookup-files"}}' | bash "$BRIDGE")
assert_contains "lookup-files skill emits advisory" "$OUT" "Cross-plugin note"

# 11. Unrelated skill -> no advisory
OUT=$(echo '{"tool_input":{"skill":"some-other-skill"}}' | bash "$BRIDGE")
assert_empty "unrelated skill emits no advisory" "$OUT"

# Cleanup
rm -f "$MARKER" "$MARKER.nudged"

echo ""
echo "──────────────────────────────"
echo "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then exit 1; fi
exit 0
