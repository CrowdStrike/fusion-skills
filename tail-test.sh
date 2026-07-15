#!/usr/bin/env bash
# Tail the active fusion-skills test run and show tool calls in real time.
#
# Surfaces both raw tool names (Bash, Read, Edit, Skill, …) and — for Bash
# commands — the fusion-skills Python script being invoked (validate.py,
# action_search.py, import_workflows.py, trigger_workflow.py, etc.), so you can
# watch a workflow authoring/deployment run progress through the pipeline.
#
# Usage:
#   ./tail-test.sh              # Auto-detect the latest test log
#   ./tail-test.sh path/to.log  # Tail a specific log

set -uo pipefail

if [[ -n "${1:-}" ]]; then
  LOG="$1"
else
  # Most recently modified run-*.log under either fusion test dir, preferring
  # logs newer than the tail marker so a fresh run is picked up first.
  LOG=$(find /tmp/fusion-skill-test /tmp/fusion-skill-ab \
    -name 'run-*.log' -newer /tmp/.tail-test-marker \
    2>/dev/null | head -1)
  # Fallback: newest run log overall.
  if [[ -z "$LOG" ]]; then
    LOG=$(ls -t /tmp/fusion-skill-test/run-*.log \
      /tmp/fusion-skill-ab/*/run-*.log 2>/dev/null | head -1)
  fi
  if [[ -z "$LOG" ]]; then
    echo "No fusion test logs found. Start a test first, or pass a log path." >&2
    exit 1
  fi
fi

echo "Tailing: $LOG"
echo "Waiting for output... (Ctrl-C to stop)"

# Each stream-json line may carry tool_use blocks. For every tool call, print:
#   <tool>: <command | skill | file_path | pattern | id | first 80 chars>
# When the tool is Bash and the command runs a fusion-skills Python script,
# append "  ->  <script.py>" so the pipeline step is obvious at a glance.
tail -f "$LOG" \
  | grep --line-buffered '"tool_use"' \
  | jq -r --unbuffered '
      .message.content[]?
      | select(.type=="tool_use")
      | (.input.command // .input.skill // .input.file_path
         // .input.pattern // .input.id // (.input.content[0:80]?) // "") as $detail
      | (.input.command // "") as $cmd
      | ( [$cmd | scan("[a-zA-Z_]+\\.py")] | first // "" ) as $script
      | "\(.name): \($detail)" + (if $script != "" then "  ->  \($script)" else "" end)
    ' 2>/dev/null
