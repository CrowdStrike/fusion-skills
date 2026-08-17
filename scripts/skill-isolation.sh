#!/usr/bin/env bash
#
# skill-isolation.sh — isolate ~/.agents/skills/ during a test run.
#
# ~/.agents/skills/ is a FLAT, SHARED namespace: every plugin the user has
# installed (fusion-skills, foundry-skills, anything else) drops its skill
# directories there. A test that leaves competing skills live is not measuring
# our skills in isolation — it is measuring them competing with someone else's.
# (Observed: Cursor loaded our `setup` skill during an unrelated test run.)
#
# This library stashes EVERY entry in ~/.agents/skills/ out of the way for the
# duration of a run and restores it afterward. It is MOVE-ONLY: nothing is ever
# deleted, so a bug or a kill can strand an entry in the stash but can never
# destroy it. `recover_orphans` reclaims anything a previous killed run left
# behind, and must run at startup before anything else touches the directory.
#
# Source this file, then call:
#   recover_orphans          # once, at startup, BEFORE any rm -rf of work dirs
#   stash_all_agents_skills  # to empty ~/.agents/skills for the run
#   restore_agents_skills    # from an EXIT/INT/TERM trap, to put it all back
#
# Iteration is GLOB-based (nullglob+dotglob), never find/pipelines/process
# substitution: a glob spawns no subprocess, so restore stays reliable even when
# called from a signal-trap handler (Ctrl-C), where forking a pipeline can not.
#
# Caller-overridable config (defaults shown):
#   SKILL_ISO_HOME   ~/.agents/skills      the shared skills directory
#   SKILL_ISO_STASH  /tmp/fusion-skill-stash   where entries are parked
#   SKILL_ISO_REPO   (unset)               this repo's root, for points_into_repo
#
# The stash lives OUTSIDE any per-run work directory on purpose: a run that
# does `rm -rf "$BASE_DIR"` must not wipe a stash left by a previously killed
# run before recover_orphans has had a chance to reclaim it.

SKILL_ISO_HOME="${SKILL_ISO_HOME:-$HOME/.agents/skills}"
SKILL_ISO_STASH="${SKILL_ISO_STASH:-/tmp/fusion-skill-stash}"
SKILL_ISO_REPO="${SKILL_ISO_REPO:-}"

# _si_glob_on / _si_glob_off — turn on nullglob+dotglob (so a glob over an empty
# dir yields nothing rather than the literal pattern, and dotfiles are included)
# and restore the caller's prior settings. Saved in a global because these run
# in pairs around a single loop.
_SI_GLOB_SAVED=""
_si_glob_on()  { _SI_GLOB_SAVED="$(shopt -p nullglob dotglob 2>/dev/null)"; shopt -s nullglob dotglob; }
_si_glob_off() { [ -n "$_SI_GLOB_SAVED" ] && eval "$_SI_GLOB_SAVED"; return 0; }

# points_into_repo <path> — true (0) only if <path> is a symlink whose target
# resolves under $SKILL_ISO_REPO, i.e. one of *our* links, safe to reclaim.
# Anything else (a real directory, or a symlink into another repo) returns 1
# and is treated as "not ours — never delete, never clobber."
points_into_repo() {
  local path="$1" repo target tdir tbase
  [ -n "$SKILL_ISO_REPO" ] || return 1
  [ -L "$path" ] || return 1
  repo="$(cd "$SKILL_ISO_REPO" 2>/dev/null && pwd -P)" || return 1
  target="$(readlink "$path")" || return 1
  case "$target" in
    /*) : ;;
    *)  target="$(cd "$(dirname "$path")" 2>/dev/null && cd "$(dirname "$target")" 2>/dev/null && pwd -P)/$(basename "$target")" || return 1 ;;
  esac
  tdir="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)" || return 1
  tbase="$(basename "$target")"
  case "$tdir/$tbase" in
    "$repo"|"$repo"/*) return 0 ;;
    *) return 1 ;;
  esac
}

# _si_count <dir> — number of immediate entries (incl. dotfiles / broken links).
_si_count() {
  local dir="$1" p n=0
  [ -d "$dir" ] || { echo 0; return 0; }
  _si_glob_on
  for p in "$dir"/*; do
    if [ -e "$p" ] || [ -L "$p" ]; then n=$((n + 1)); fi
  done
  _si_glob_off
  echo "$n"
}

# stash_all_agents_skills — move every entry in SKILL_ISO_HOME into
# SKILL_ISO_STASH. Move-only. Sets global SKILL_ISO_STASHED to the count.
stash_all_agents_skills() {
  SKILL_ISO_STASHED=0
  [ -d "$SKILL_ISO_HOME" ] || return 0
  mkdir -p "$SKILL_ISO_STASH"
  local p name
  _si_glob_on
  for p in "$SKILL_ISO_HOME"/*; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    name="${p##*/}"
    if [ -e "$SKILL_ISO_STASH/$name" ] || [ -L "$SKILL_ISO_STASH/$name" ]; then
      echo "  skill-isolation: WARN '$name' already in stash; leaving live copy in place" >&2
      continue
    fi
    if mv "$p" "$SKILL_ISO_STASH/$name" 2>/dev/null; then
      SKILL_ISO_STASHED=$((SKILL_ISO_STASHED + 1))
    else
      echo "  skill-isolation: WARN could not stash '$name'" >&2
    fi
  done
  _si_glob_off
  [ "$SKILL_ISO_STASHED" -gt 0 ] && \
    echo "  skill-isolation: stashed $SKILL_ISO_STASHED entr(y|ies) from $SKILL_ISO_HOME"
  return 0
}

# restore_agents_skills — move every stashed entry back into SKILL_ISO_HOME.
# Idempotent and safe to call from a trap. Never clobbers a live entry that
# reappeared unless it is one of OUR stale symlinks; otherwise the stashed copy
# is kept (never deleted) and reported.
restore_agents_skills() {
  [ -d "$SKILL_ISO_STASH" ] || return 0
  mkdir -p "$SKILL_ISO_HOME"
  local p name restored=0 kept=0
  _si_glob_on
  for p in "$SKILL_ISO_STASH"/*; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    name="${p##*/}"
    if [ -e "$SKILL_ISO_HOME/$name" ] || [ -L "$SKILL_ISO_HOME/$name" ]; then
      if points_into_repo "$SKILL_ISO_HOME/$name"; then
        rm -f "$SKILL_ISO_HOME/$name"
      else
        echo "  skill-isolation: WARN '$name' is live in $SKILL_ISO_HOME; leaving stashed copy at $SKILL_ISO_STASH/$name" >&2
        kept=$((kept + 1))
        continue
      fi
    fi
    if mv "$p" "$SKILL_ISO_HOME/$name" 2>/dev/null; then
      restored=$((restored + 1))
    else
      echo "  skill-isolation: WARN could not restore '$name'" >&2
      kept=$((kept + 1))
    fi
  done
  _si_glob_off
  [ "$restored" -gt 0 ] && \
    echo "  skill-isolation: restored $restored entr(y|ies) to $SKILL_ISO_HOME"
  if [ "$kept" -eq 0 ]; then rmdir "$SKILL_ISO_STASH" 2>/dev/null || true; fi
  return 0
}

# recover_orphans — reclaim a stash left by a PREVIOUS run that was killed
# between stash and restore. Nothing else on the machine would put these back.
# Run this ONCE at startup, before any rm -rf of work dirs. Idempotent.
recover_orphans() {
  [ -d "$SKILL_ISO_STASH" ] || return 0
  local n
  n="$(_si_count "$SKILL_ISO_STASH")"
  [ "${n:-0}" -gt 0 ] || { rmdir "$SKILL_ISO_STASH" 2>/dev/null || true; return 0; }
  echo "  skill-isolation: recovering $n orphaned entr(y|ies) from a prior interrupted run"
  restore_agents_skills
}
