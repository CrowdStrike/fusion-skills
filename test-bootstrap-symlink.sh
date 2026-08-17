#!/usr/bin/env bash
#
# test-bootstrap-symlink.sh — regression guard for symlink-based skill invocation.
#
# Non-Claude assistants (Codex, Copilot CLI, Cursor, Antigravity) discover skills
# through ~/.agents/skills/<skill> SYMLINKS, and they invoke the scripts by path
# rather than through ${CLAUDE_PLUGIN_ROOT}/scripts/python.sh (that variable is set
# only by Claude Code). For that to work, a script launched via its symlinked path
# must still resolve two things in the REAL repo tree:
#   1. common/scripts (for the shared auth module import), and
#   2. scripts/python.sh (the managed-venv wrapper the cold-start bootstrap re-execs).
#
# The bug this guards against: anchoring with os.path.abspath(__file__) collapses the
# ".." components LEXICALLY, so through a symlink it fabricates a path that doesn't
# physically exist — the wrapper is "not found", the venv re-exec never fires, and the
# script dies with ModuleNotFoundError: falconpy. os.path.realpath resolves the
# symlink and the ".." against the real filesystem, so both paths resolve correctly.
#
# This test is hermetic: pure path resolution, no venv build, no network, no creds.
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/bootstrap-symlink.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
check() { # check <desc> <cond-rc>
  if [ "$2" -eq 0 ]; then echo "  ✅ $1"; PASS=$((PASS + 1));
  else echo "  ❌ $1"; FAIL=$((FAIL + 1)); fi
}

# A ~/.agents/skills-style layout: a symlink to a real skill dir, nested deep enough
# that a lexical ".." collapse would land somewhere that does NOT physically exist.
mkdir -p "$WORK/home/.agents/skills"
ln -s "$REPO/skills/authoring" "$WORK/home/.agents/skills/authoring"
SYMLINK_SCRIPT="$WORK/home/.agents/skills/authoring/scripts/action_search.py"

echo "== the shipped scripts anchor with realpath, not abspath =="
# A reintroduced abspath(__file__) would silently break symlink invocation again.
if grep -rn "abspath(__file__)" "$REPO/common/scripts"/*.py "$REPO"/skills/*/scripts/*.py >/dev/null 2>&1; then
  check "no abspath(__file__) remains in shipped scripts" 1
  grep -rn "abspath(__file__)" "$REPO/common/scripts"/*.py "$REPO"/skills/*/scripts/*.py | sed 's|'"$REPO"'/|    |'
else
  check "no abspath(__file__) remains in shipped scripts" 0
fi

echo "== _bootstrap resolves python.sh through a symlinked import path =="
# Import _bootstrap exactly as an entry-point script does — via a sys.path entry that
# contains ".." and runs through the symlink — then call the private resolver and
# assert it points at a file that actually exists. This is the exact code path the bug
# lived in.
python3 - "$SYMLINK_SCRIPT" <<'PY'
import os, sys
script = sys.argv[1]
# Mirror the scripts' own sys.path insert (…/scripts/../../../common/scripts), kept
# with literal ".." just like the shipped code.
common = os.path.join(os.path.dirname(script), "..", "..", "..", "common", "scripts")
sys.path.insert(0, common)
import _bootstrap
wrapper = _bootstrap._python_sh_path()
ok = os.path.exists(wrapper)
print(f"    resolved wrapper: {wrapper}")
print(f"    exists: {ok}")
sys.exit(0 if ok else 1)
PY
check "_python_sh_path() resolves to an existing wrapper via the symlink" $?

echo "== the shared auth module resolves through the symlinked path =="
# The sys.path entry the scripts insert must reach the REAL common/scripts/auth.py
# when the script is launched through the symlink.
python3 - "$SYMLINK_SCRIPT" <<'PY'
import os, sys
script = sys.argv[1]
common = os.path.join(os.path.dirname(script), "..", "..", "..", "common", "scripts")
auth = os.path.join(common, "auth.py")
real = os.path.realpath(auth)
ok = os.path.isfile(auth) and os.path.basename(real) == "auth.py"
print(f"    auth.py via symlink resolves to: {real}")
sys.exit(0 if ok else 1)
PY
check "common/scripts/auth.py resolves through the symlink" $?

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
