#!/usr/bin/env bash
#
# test-skill-isolation.sh — unit tests for scripts/skill-isolation.sh.
# Fast, no network/API. Exercises stash/restore/recover against a fake
# ~/.agents/skills containing a mix of our-symlinks, foreign symlinks, and a
# real directory, and asserts the move-only / never-delete-foreign guarantees.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/skill-iso-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# Point the library at throwaway locations BEFORE sourcing (it binds config at
# source time). SKILL_ISO_REPO is a fake repo whose skills/ our links target.
export SKILL_ISO_HOME="$WORK/agents/skills"
export SKILL_ISO_STASH="$WORK/stash"
export SKILL_ISO_REPO="$WORK/repo"
# shellcheck source=scripts/skill-isolation.sh
source "$SCRIPT_DIR/scripts/skill-isolation.sh"

PASS=0
FAIL=0
check() { # check <desc> <cond-rc>
  if [ "$2" -eq 0 ]; then echo "  ✅ $1"; PASS=$((PASS + 1));
  else echo "  ❌ $1"; FAIL=$((FAIL + 1)); fi
}

# ---- fixture ---------------------------------------------------------------
build_fixture() {
  rm -rf "$WORK/agents" "$WORK/stash" "$WORK/repo" "$WORK/other"
  mkdir -p "$SKILL_ISO_HOME"
  # Our skills live in the fake repo; our links point at them.
  mkdir -p "$SKILL_ISO_REPO/skills/authoring" "$SKILL_ISO_REPO/skills/deployment"
  echo "authoring-skill" > "$SKILL_ISO_REPO/skills/authoring/SKILL.md"
  ln -s "$SKILL_ISO_REPO/skills/authoring" "$SKILL_ISO_HOME/authoring"
  ln -s "$SKILL_ISO_REPO/skills/deployment" "$SKILL_ISO_HOME/deployment"
  # A FOREIGN skill from another repo — must never be deleted.
  mkdir -p "$WORK/other/skills/foreign-skill"
  echo "foreign-content" > "$WORK/other/skills/foreign-skill/SKILL.md"
  ln -s "$WORK/other/skills/foreign-skill" "$SKILL_ISO_HOME/foreign-skill"
  # A REAL directory sitting directly in the shared namespace.
  mkdir -p "$SKILL_ISO_HOME/real-local-skill"
  echo "real-local" > "$SKILL_ISO_HOME/real-local-skill/SKILL.md"
}

# Snapshot: entry name + (symlink target | dir marker) + a content hash.
snapshot() {
  local p name
  for p in "$SKILL_ISO_HOME"/*; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    name="$(basename "$p")"
    if [ -L "$p" ]; then printf '%s -> %s\n' "$name" "$(readlink "$p")";
    else printf '%s [dir] %s\n' "$name" "$(cat "$p"/SKILL.md 2>/dev/null)"; fi
  done | sort
}

echo "== points_into_repo =="
build_fixture
points_into_repo "$SKILL_ISO_HOME/authoring"; check "our symlink is recognized as ours" $?
points_into_repo "$SKILL_ISO_HOME/foreign-skill"; rc=$?; check "foreign symlink is NOT ours" "$([ $rc -ne 0 ] && echo 0 || echo 1)"
points_into_repo "$SKILL_ISO_HOME/real-local-skill"; rc=$?; check "real dir is NOT ours" "$([ $rc -ne 0 ] && echo 0 || echo 1)"

echo "== stash empties the namespace, restore is byte-identical =="
build_fixture
BEFORE="$(snapshot)"
stash_all_agents_skills >/dev/null
remaining="$(_si_count "$SKILL_ISO_HOME")"
check "namespace empty after stash" "$([ "$remaining" -eq 0 ] && echo 0 || echo 1)"
stashed="$(_si_count "$SKILL_ISO_STASH")"
check "all 4 entries stashed" "$([ "$stashed" -eq 4 ] && echo 0 || echo 1)"
restore_agents_skills >/dev/null
AFTER="$(snapshot)"
check "restore is byte-identical to before" "$([ "$BEFORE" = "$AFTER" ] && echo 0 || echo 1)"
check "foreign target file still exists (never deleted)" "$([ -f "$WORK/other/skills/foreign-skill/SKILL.md" ] && echo 0 || echo 1)"

echo "== recover_orphans reclaims a killed run's stash =="
build_fixture
BEFORE="$(snapshot)"
stash_all_agents_skills >/dev/null   # simulate a run that stashed...
# ...then was killed before restore (stash left populated, namespace empty).
recover_orphans >/dev/null
AFTER="$(snapshot)"
check "orphans recovered byte-identical" "$([ "$BEFORE" = "$AFTER" ] && echo 0 || echo 1)"

echo "== restore never clobbers a live foreign entry =="
build_fixture
stash_all_agents_skills >/dev/null
# A DIFFERENT foreign 'authoring' reappears live while our copy is stashed.
mkdir -p "$WORK/intruder/authoring"
echo "intruder" > "$WORK/intruder/authoring/SKILL.md"
ln -s "$WORK/intruder/authoring" "$SKILL_ISO_HOME/authoring"
restore_agents_skills >/dev/null 2>&1
live_target="$(readlink "$SKILL_ISO_HOME/authoring")"
check "live foreign 'authoring' preserved (not clobbered)" "$([ "$live_target" = "$WORK/intruder/authoring" ] && echo 0 || echo 1)"
check "our stashed 'authoring' left safe in stash" "$([ -L "$SKILL_ISO_STASH/authoring" ] && echo 0 || echo 1)"

echo "== restore fires from an INT (Ctrl-C) trap =="
# Regression guard: restore MUST work when invoked from a signal-trap handler.
# A find/pipeline/process-substitution implementation silently failed here and
# would have left ~/.agents/skills emptied on Ctrl-C; glob iteration fixed it.
build_fixture
cat > "$WORK/int-child.sh" <<CHILD
export SKILL_ISO_HOME="$SKILL_ISO_HOME" SKILL_ISO_STASH="$SKILL_ISO_STASH" SKILL_ISO_REPO="$SKILL_ISO_REPO"
source "$SCRIPT_DIR/scripts/skill-isolation.sh"
S=0
cleanup(){ [ "\$S" = 1 ] && restore_agents_skills >/dev/null 2>&1; }
trap 'cleanup' EXIT
trap 'cleanup; exit 130' INT TERM
stash_all_agents_skills >/dev/null 2>&1; S=1
: > "$WORK/int-ready"
sleep 20 & wait
CHILD
BEFORE_INT="$(snapshot)"
bash "$WORK/int-child.sh" & icpid=$!
tries=0; while [ ! -f "$WORK/int-ready" ] && [ "$tries" -lt 100 ]; do sleep 0.1; tries=$((tries + 1)); done
kill -INT "$icpid" 2>/dev/null; wait "$icpid" 2>/dev/null || true
AFTER_INT="$(snapshot)"
check "INT trap restored byte-identical" "$([ "$BEFORE_INT" = "$AFTER_INT" ] && echo 0 || echo 1)"

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
