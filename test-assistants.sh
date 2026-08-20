#!/usr/bin/env bash
#
# test-assistants.sh — Smoke-test every assistant in the README against a live tenant.
#
# Each assistant gets the real workflow-creation prompt from the README, plus one
# instruction the README does not need: work for about a minute, then stop and say
# what happened. We are not waiting for a finished, deployed workflow (that takes
# several minutes). We are looking for the failures that bite in the first minute — a
# missing Python dependency, an unresolved `${CLAUDE_PLUGIN_ROOT}` that only Claude
# Code sets, a rejected flag, a TTY demand, denied credentials.
#
# The assistant reports back rather than being cut off mid-thought, which is the
# whole trick: the harness is talking to something that can describe its own state,
# so it asks. Every run ends in a fixed plain-text report naming the skills that
# loaded, the fusion-skills scripts it ran and how each one went, and any blocker;
# --e2e adds the workflow name and definition id to the same shape.
# Classification reads that report. Inferring the outcome by grepping a truncated
# transcript — and calling a clean timeout a pass — could not tell "still building"
# apart from "sat there doing nothing", which is exactly the case that matters.
#
# The timeout is now a safety net rather than the measurement. Assistants stop on
# their own at the report deadline, so a healthy run finishes well inside it.
#
# BIAS CONTROL — this is the point of the script, not a detail. Skills can reach an
# assistant from several places at once (an installed marketplace plugin, symlinks
# in ~/.agents/skills/, a --plugin-dir flag). If more than one is live, a passing
# run tells you nothing about which copy was exercised, and a stale installed copy
# can silently mask your working tree. So before testing, this script:
#
#   1. Disables installed Fusion plugins where the assistant supports it
#   2. Moves EVERY entry in ~/.agents/skills/ out of the way (not only ours — a
#      sibling repo's `setup` skill competes for the same prompt just as much)
#   3. Gives each assistant exactly ONE source pointing at the working tree
#
# The ~/.agents/skills stash/restore is delegated to scripts/skill-isolation.sh,
# which is move-only (never deletes), glob-based (safe to run from a signal trap),
# and unit-tested by test-skill-isolation.sh. Everything is restored on exit,
# including on Ctrl-C, and a stash orphaned by a run that was killed before it could
# tidy up is recovered at startup.
#
# Usage:
#   ./test-assistants.sh                      # test every installed assistant
#   ./test-assistants.sh --include codex      # test only these (comma-separated)
#   ./test-assistants.sh --exclude antigravity # test all but these (comma-separated)
#   ./test-assistants.sh --report-at 90       # ask for the report later (default 60s)
#   ./test-assistants.sh --timeout 300        # raise the hard cap (default 150s)
#   ./test-assistants.sh --e2e                # author, validate, and IMPORT for real
#   ./test-assistants.sh --judge              # judge the last --e2e run, launching nothing
#   ./test-assistants.sh --save results.json  # machine-readable results
#   ./test-assistants.sh --sequential         # one at a time (default: two groups in parallel)
#   ./test-assistants.sh --no-isolate         # skip bias control (not recommended)
#   ./test-assistants.sh --verbose            # list every plugin and symlink touched
#
# --e2e is the other half of the story. Smoke mode deliberately says "do not import",
# so it can prove an assistant reaches the tenant but never that it can ship a
# workflow — and a self-reported "3 scripts OK" is compatible with nothing at all
# being deployed. In --e2e mode the deadline moves out and the appended instructions
# demand a workflow definition id, so a PASS requires an artifact that either exists
# on the tenant or does not.
#
# Two things --e2e sets up that smoke mode does not need. Each assistant gets its own
# working directory, since they would otherwise author on top of each other. And each
# is told to end its workflow name with its own slug, because a run that deploys a
# duplicate name churns the tenant: without the suffix the results are ambiguous.
#
# What --e2e still does NOT do is trust the claim. It records what each assistant
# says it deployed; --judge is what confirms the definition against the tenant with
# query_workflows.py --list and reads the authored YAML for the per-skill markers.
#
# NOTE ON CREDENTIALS: unlike a CLI that caches a token file, the fusion-skills
# scripts authenticate through FalconPy in memory from FALCON_CLIENT_ID /
# FALCON_CLIENT_SECRET (or a ~/.cache/crowdstrike-falcon-fusion/credentials.toml
# profile). There is no shared token file for parallel runs to race on, so this
# harness does not warm or expire one — it only confirms the tenant is reachable
# once before launching, so a credential problem surfaces as a clear message here
# instead of as five identical auth failures in the logs.
#
# Needs bash 4.3+ for case conversion and namerefs. macOS ships 3.2, so this runs
# under the Homebrew bash the shebang finds on PATH, not /bin/bash.
#
# Exit status is non-zero if any tested assistant failed.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_AT=""  # default set below: near the end of the run, which differs by mode
TIMEOUT=""    # default set below: higher in parallel, where agents contend
E2E=0         # --e2e: author, validate, and import for real instead of smoke-testing
JUDGE=0       # --judge: check a previous --e2e run against ground truth, launching nothing
SAVE_FILE=""
ONLY=()
SKIP=()
ISOLATE=1
VERBOSE=0
PARALLEL=1   # two groups in parallel; --sequential to disable
LOG_DIR="/tmp/fusion-assistant-test"
SKILL_HOME="$HOME/.agents/skills"
CODEX_CACHE_STASH="$LOG_DIR/stashed-codex-cache"
CODEX_CACHE_ORIGIN="$LOG_DIR/stashed-codex-cache.origin"

# The ~/.agents/skills stash/restore/recover machinery. Move-only, glob-based, safe
# from a signal trap, and unit-tested. Bind its config BEFORE sourcing (it reads the
# variables at source time). The stash dir lives OUTSIDE $LOG_DIR on purpose, so a
# run that does `rm -rf "$LOG_DIR"` cannot wipe a killed run's stash before
# recover_orphans reclaims it.
export SKILL_ISO_HOME="$SKILL_HOME"
export SKILL_ISO_REPO="$REPO"
# shellcheck source=scripts/skill-isolation.sh
source "$REPO/scripts/skill-isolation.sh"

# The real workflow-creation prompt, matching the README example and test-skill.sh.
# It names no fusion-skills scripts, so an assistant with no skills loaded cannot fake
# its way through — which is exactly what makes it a skills test rather than a script
# test. CI asserts this line still starts with the README example text, so keep
# additions out of it: the reporting instructions are appended at run time instead.
PROMPT="Generate a Falcon Fusion workflow that will trigger from a Falcon Next-Gen SIEM detection. The workflow should hydrate the detection using an event query to get the full details of the detection. If a user, host, domain, url, file indicator, or ip indicator is found, enrich each in parallel using HTTP calls to VirusTotal or DomainTools. Summarize the enrichment across all the threat intelligence providers using an LLM completion action and then send an email formatted in HTML. Pick a reasonable workflow name and proceed without asking me any questions."

# What turns a transcript into a result. Appended to the prompt above, never spliced
# into it.
#
# Three details in here are load-bearing. The labels are asked for as plain lines,
# because the classifier drops blockquoted lines and a report wrapped in `>` would
# vanish with them. The fields below are described in angle brackets and a real
# report contains none, so a log that merely echoes the prompt back — Codex prints
# the whole thing — cannot be mistaken for a report. And the deadline is stated as
# wall clock with `date` offered as the way to read it, because an assistant has no
# other clock and will otherwise keep going until something kills it.
report_instructions() {
  cat <<EOF

Two more things, because this is a timed test harness rather than a real build.

This is a lightweight smoke test of the SKILL, not a real build. Your ONLY goal is
to confirm the skill loaded and its scripts run: run one or two discovery commands
(for example action_search.py or trigger_search.py, or this skill's own script such
as query_workflows.py --list), then STOP and report. Do NOT author workflow YAML,
and do NOT import or deploy anything.

Report within about ${REPORT_AT} seconds — run \`date\` to check where you are. Report
immediately if something blocks you or you find yourself about to ask a question.
Running out of the time budget is not a failure; report what you have with BLOCKER: NONE.

To report, end your reply with these five lines, in this order, each starting a line
of plain text. No code fence, no blockquote, no bullets, no bold, and no angle
brackets in anything you write:

FUSION-REPORT
STATUS: <one word — WORKING or DONE if the skill's scripts ran, BLOCKED only if a real problem stopped you. Running out of the time budget is NOT blocked; that is WORKING>
SKILLS: <comma-separated paths of the skill files you loaded, or NONE>
COMMANDS: <comma-separated, every fusion-skills script you ran (action_search.py, validate.py, etc.), each written as the script followed by => OK or => FAIL: reason. NONE if you ran none>
BLOCKER: <one line naming a real problem, quoting the error verbatim if there was one. NONE if nothing did. The time budget is not a blocker — if you simply ran out of time and nothing failed, write NONE>
EOF
}

# The --e2e counterpart. Same seven-line plain-text shape as above, for the same
# reason: this is parsed out of a transcript that may have echoed the prompt back,
# and reports from five different assistants arrive fenced, prefixed, or truncated.
# Two extra fields carry the evidence smoke mode cannot produce.
#
# Note what is NOT said here: no fusion-skills script is named, and the workflow is
# never described as something to "validate then import" step by step. Deploy is a
# step of the deployment skill, so an assistant that loaded the skills knows to do
# it. Spelling out the steps would measure instruction-following instead, and the
# canonical prompt's whole value is that it names no scripts.
e2e_instructions() {   # slug
  cat <<EOF

Two more things, because this runs against a live shared tenant.

Name the workflow so it ends with \`-$1\`. Other builds are running alongside yours;
the suffix keeps each workflow definition distinct so a duplicate name does not churn
the tenant. Otherwise pick whatever name you like.

You have about ${REPORT_AT} seconds of wall clock; run \`date\` if you need to know where you
are. Report before that runs out, and report immediately if something blocks you or if
you find yourself about to ask me a question. A partial result reported is worth more
than a finished workflow I never hear about.

To report, end your reply with these seven lines, in this order, each starting a line
of plain text. No code fence, no blockquote, no bullets, no bold, and no angle
brackets in anything you write:

FUSION-REPORT
STATUS: <one word — DONE if the workflow imported, WORKING if you ran out of time mid-build, BLOCKED only if a real problem stopped you>
WORKFLOW: <the workflow name you chose, or NONE if you never created one>
DEFINITION: <the workflow definition id returned by the import, or NONE if you did not get one>
SKILLS: <comma-separated paths of the skill files you loaded, or NONE>
COMMANDS: <comma-separated, every fusion-skills script you ran, each written as the script followed by => OK or => FAIL: reason. NONE if you ran none>
BLOCKER: <one line naming a real problem, quoting the error verbatim if there was one. NONE if nothing did>
EOF
}

# Both selectors take a comma-separated list and are repeatable, so
# `--include codex,cursor` and `--include codex --include cursor` are the same thing.
add_selectors() {   # array_name csv
  local -n arr="$1"
  local IFS=','
  read -r -a _parts <<< "$2"
  local v
  for v in ${_parts[@]+"${_parts[@]}"}; do
    v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
    [ -n "$v" ] && arr+=("$v")
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include|--only) add_selectors ONLY "$2"; shift 2 ;;
    --exclude|--skip) add_selectors SKIP "$2"; shift 2 ;;
    --report-at)    REPORT_AT="$2"; shift 2 ;;
    --timeout)      TIMEOUT="$2"; shift 2 ;;
    --save)         SAVE_FILE="$2"; shift 2 ;;
    --prompt)       PROMPT="$2"; shift 2 ;;
    --no-isolate)   ISOLATE=0; shift ;;
    --e2e)          E2E=1; shift ;;
    --judge)        JUDGE=1; shift ;;
    -v|--verbose)   VERBOSE=1; shift ;;
    --sequential)   PARALLEL=0; shift ;;
    -h|--help)      sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Concurrency slows each agent under contention, so the parallel cap is higher than
# the sequential one to leave room to report. The parallel cap is generous because
# the slowest assistant sets the group's wall clock and some (Cursor) are slow per
# step — Cursor was observed still writing its report at 150s, so 240s gives it room
# to finish rather than being killed mid-sentence. Fast assistants finish early and
# don't pad to the cap. --e2e is a different measurement (author-validate-import).
if [ -z "$TIMEOUT" ]; then
  if   [ "$E2E" -eq 1 ];      then TIMEOUT=900
  elif [ "$PARALLEL" -eq 1 ]; then TIMEOUT=240
  else                             TIMEOUT=180
  fi
fi

# Judging reads files and the tenant. It launches nothing, so there is nothing to
# isolate and no reason to touch the user's plugins or symlinks.
[ "$JUDGE" -eq 1 ] && ISOLATE=0

if [ -z "$REPORT_AT" ]; then
  if [ "$E2E" -eq 1 ]; then REPORT_AT=780; else REPORT_AT=60; fi
fi

# The cap has to leave room for the report to be written after the deadline, or the
# harness kills the assistant mid-sentence and we are back to guessing.
if [ "$TIMEOUT" -le "$REPORT_AT" ]; then
  TIMEOUT=$(( REPORT_AT + 30 ))
fi


RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'; MAGENTA=$'\033[0;35m'; CYAN=$'\033[0;36m'
DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

ok()   { printf '  %s✓%s  %s\n' "$GREEN" "$RESET" "$1"; }
# Per-item bookkeeping: shown only with --verbose, so 20 lines of checkmarks don't
# bury 5 lines of actual results.
vok()  { [ "$VERBOSE" -eq 1 ] && printf '  %s✓%s  %s%s%s\n' "$GREEN" "$RESET" "$DIM" "$1" "$RESET"; return 0; }
warn() { printf '  %s▲%s  %s\n' "$YELLOW" "$RESET" "$1"; }
info() { printf '      %s%s%s\n' "$DIM" "$1" "$RESET"; }
head2(){ printf '\n%s%s%s%s\n' "$BOLD" "$CYAN" "$1" "$RESET"; }

# ── Bias control ───────────────────────────────────────────────
DISABLED_CLAUDE=()
DISABLED_AGY=()
OURS=()                 # symlinks this script created, so we only ever remove our own
CODEX_CACHE=""          # moved-aside Codex plugin cache, restored on exit

restore() {
  local had=0
  [ ${#DISABLED_CLAUDE[@]} -gt 0 ] && had=1
  [ ${#DISABLED_AGY[@]} -gt 0 ] && had=1
  [ "${SKILL_ISO_STASHED:-0}" -gt 0 ] && had=1
  [ -n "$CODEX_CACHE" ] && had=1
  [ -d "${SKILL_ISO_STASH:-/nonexistent}" ] && had=1
  [ "$had" -eq 0 ] && return 0

  head2 "Restoring your setup"
  local p
  for p in ${DISABLED_CLAUDE[@]+"${DISABLED_CLAUDE[@]}"}; do
    claude plugin enable "$p" >/dev/null 2>&1 && vok "re-enabled claude plugin $p" || warn "could not re-enable claude plugin $p"
  done
  for p in ${DISABLED_AGY[@]+"${DISABLED_AGY[@]}"}; do
    agy plugin enable "$p" >/dev/null 2>&1 && vok "re-enabled agy plugin $p" || warn "could not re-enable agy plugin $p"
  done
  if [ -n "$CODEX_CACHE" ] && [ -d "$CODEX_CACHE_STASH" ]; then
    rm -rf "$CODEX_CACHE"
    mv "$CODEX_CACHE_STASH" "$CODEX_CACHE" && vok "restored Codex plugin cache"
    rm -f "$CODEX_CACHE_ORIGIN"
  fi
  # Put every stashed ~/.agents/skills entry back. Delegated to the helper: move-only,
  # glob-based, and safe to call from this EXIT/INT trap.
  restore_agents_skills
  local plugins=$(( ${#DISABLED_CLAUDE[@]} + ${#DISABLED_AGY[@]} ))
  printf '  %s✓%s  re-enabled %s%s%s plugin(s), skill namespace restored\n' \
    "$GREEN" "$RESET" "$BOLD" "$plugins" "$RESET"
}

# A run killed between stashing and restoring leaves the Codex plugin cache in
# $CODEX_CACHE_STASH and, worse, this run would overwrite it. Nothing else on the
# machine will put it back, so recover it before touching anything. The ~/.agents
# skills stash is recovered by the helper's recover_orphans (called below).
recover_codex_cache_orphan() {
  [ -d "$CODEX_CACHE_STASH" ] && [ -f "$CODEX_CACHE_ORIGIN" ] || return 0
  local origin
  origin=$(cat "$CODEX_CACHE_ORIGIN")
  if [ -n "$origin" ] && [ ! -e "$origin" ]; then
    mkdir -p "$(dirname "$origin")"
    if mv "$CODEX_CACHE_STASH" "$origin"; then
      rm -f "$CODEX_CACHE_ORIGIN"
      warn "recovered a Codex plugin cache left behind by an interrupted run"
    fi
  fi
}

# Ctrl-C must kill the assistant that is actually running, not just this script.
# The child runs in the background so it has its own PID we can signal; without
# that, the signal lands on the wrapper, the assistant keeps going, and the loop
# moves on to the next one.
CHILD_PIDS=()
INTERRUPTED=0
on_interrupt() {
  if [ "$INTERRUPTED" -eq 1 ]; then
    printf '\n  %s▲%s  forcing exit\n' "$YELLOW" "$RESET"
    for _p in ${CHILD_PIDS[@]+"${CHILD_PIDS[@]}"}; do kill -KILL -- -"$_p" 2>/dev/null; done
    exit 130
  fi
  INTERRUPTED=1
  printf '\n  %s▲%s  stopping %s assistant(s) (Ctrl-C again to force)\n' \
    "$YELLOW" "$RESET" "${#CHILD_PIDS[@]}"
  # Signal each whole process group: assistants spawn node, npm and Python, and
  # killing only the direct child leaves those running.
  for _p in ${CHILD_PIDS[@]+"${CHILD_PIDS[@]}"}; do
    kill -TERM -- -"$_p" 2>/dev/null || kill -TERM "$_p" 2>/dev/null || true
  done
  for _ in 1 2 3 4 5 6; do
    local alive=0
    for _p in ${CHILD_PIDS[@]+"${CHILD_PIDS[@]}"}; do kill -0 -- -"$_p" 2>/dev/null && alive=1; done
    [ "$alive" -eq 0 ] && break
    sleep 0.25
  done
  for _p in ${CHILD_PIDS[@]+"${CHILD_PIDS[@]}"}; do kill -KILL -- -"$_p" 2>/dev/null; done
  exit 130   # restore runs via the EXIT trap
}
trap on_interrupt INT TERM
trap restore EXIT

isolate() {
  head2 "Isolating skill sources (so results mean something)"

  # Installed plugins that could shadow the working tree.
  if command -v claude >/dev/null 2>&1; then
    local out
    out=$(claude plugin list 2>/dev/null || true)
    while read -r p; do
      [ -z "$p" ] && continue
      if claude plugin disable "$p" >/dev/null 2>&1; then
        DISABLED_CLAUDE+=("$p"); vok "disabled claude plugin $p"
      fi
    done < <(echo "$out" | grep -oE '[a-z0-9-]*fusion[a-z0-9-]*' | sort -u)
  fi
  if command -v agy >/dev/null 2>&1; then
    while read -r p; do
      [ -z "$p" ] && continue
      if agy plugin disable "$p" >/dev/null 2>&1; then
        DISABLED_AGY+=("$p"); vok "disabled agy plugin $p"
      fi
    done < <(agy plugin list 2>/dev/null | grep -oE '"name": *"[^"]*fusion[^"]*"' | sed 's/.*: *"//;s/"//' | sort -u)
  fi

  # Codex has no `plugin disable`, and it loads the plugin cache *and*
  # ~/.agents/skills at once, so leaving the cache in place would defeat the whole
  # exercise. Move the directory aside rather than uninstalling; it is restored on
  # exit, so no reinstall is needed.
  local cc
  for cc in "$HOME"/.codex/plugins/cache/*/; do
    [ -d "$cc" ] || continue
    if find "$cc" -maxdepth 1 -name '*fusion*' -print -quit 2>/dev/null | grep -q .; then
      CODEX_CACHE="${cc%/}"
      rm -rf "$CODEX_CACHE_STASH"
      if mv "$CODEX_CACHE" "$CODEX_CACHE_STASH" 2>/dev/null; then
        # Record where it came from, so a run that dies before restoring can be
        # cleaned up by the next one instead of leaving Codex plugin-less.
        printf '%s\n' "$CODEX_CACHE" > "$CODEX_CACHE_ORIGIN"
        vok "moved Codex plugin cache aside"
      else
        warn "could not move the Codex plugin cache; its results will be ambiguous"
        CODEX_CACHE=""
      fi
      break
    fi
  done

  # Copilot and Cursor cannot disable, only uninstall — too destructive to do
  # automatically. Warn instead, since --plugin-dir should win anyway.
  if command -v copilot >/dev/null 2>&1 && copilot plugin list 2>/dev/null | grep -qi fusion; then
    warn "copilot has a Fusion plugin installed and cannot disable it"
    info "--plugin-dir should take precedence; uninstall manually for a fully clean run"
  fi

  # Every symlink in ~/.agents/skills, not only this repo's. A sibling repo competes
  # just as much: a foundry-skills `setup` skill loaded into a fusion run skews it.
  # Delegated to the helper — move-only, so nothing here can be destroyed.
  stash_all_agents_skills

  local plugins=$(( ${#DISABLED_CLAUDE[@]} + ${#DISABLED_AGY[@]} ))
  if [ "$plugins" -eq 0 ] && [ "${SKILL_ISO_STASHED:-0}" -eq 0 ]; then
    ok "nothing to isolate — no competing sources found"
  else
    printf '  %s✓%s  disabled %s%s%s plugin(s), stashed %s%s%s skill entr(y|ies)\n' \
      "$GREEN" "$RESET" "$BOLD" "$plugins" "$RESET" "$BOLD" "${SKILL_ISO_STASHED:-0}" "$RESET"
    [ "$VERBOSE" -eq 0 ] && info "run with --verbose to list each one"
  fi
  return 0
}

# Codex and Antigravity have no --plugin-dir, so give them the one source they do
# read: symlinks into the working tree, created fresh for this run. The namespace was
# emptied by stash_all_agents_skills, so these are the only entries present.
link_repo_skills() {
  mkdir -p "$SKILL_HOME" "$SKILL_ISO_STASH"
  local d n path
  for d in "$REPO"/skills/*/; do
    n=$(basename "${d%/}")
    path="$SKILL_HOME/$n"
    # Anything already at this name belongs to someone else — another clone, or a
    # real directory that reappeared. Preserve it in the helper's stash rather than
    # letting `ln -sfn` destroy it.
    if [ -e "$path" ] || [ -L "$path" ]; then
      if mv "$path" "$SKILL_ISO_STASH/$n" 2>/dev/null; then
        vok "stashed colliding $n"
      else
        warn "could not move aside $n; leaving it alone"
        continue
      fi
    fi
    ln -sfn "${d%/}" "$path" && OURS+=("$n")
  done
}
unlink_repo_skills() {
  # Remove only the symlinks we created. Never touch anything we did not make.
  local n
  for n in ${OURS[@]+"${OURS[@]}"}; do
    [ -L "$SKILL_HOME/$n" ] && points_into_repo "$SKILL_HOME/$n" && rm -f "$SKILL_HOME/$n"
  done
  OURS=()
}

# ── Assistants ─────────────────────────────────────────────────
# name|binary|source|argv   (%%PROMPT%% substituted at run time)
#
# The source column decides which parallel group an assistant runs in: --plugin-dir
# assistants run with ~/.agents/skills emptied, while Codex and Antigravity need this
# repo's symlinks present there. Cursor's CLI binary is `agent` (also installed as `cursor-agent`; both are the same executable).
ASSISTANTS=(
  "Claude Code|claude|--plugin-dir|-p %%PROMPT%% --plugin-dir $REPO --dangerously-skip-permissions --verbose --output-format stream-json"
  "Codex|codex|~/.agents/skills|exec %%PROMPT%% --skip-git-repo-check --json"
  "Copilot CLI|copilot|--plugin-dir|-p %%PROMPT%% --plugin-dir $REPO --allow-all --output-format json"
  "Cursor|agent|--plugin-dir|-p %%PROMPT%% --plugin-dir $REPO --force --trust --output-format stream-json"
  "Antigravity CLI|agy|~/.agents/skills|-p %%PROMPT%% --dangerously-skip-permissions --output-format stream-json"
)

want() {
  local n="$1" b="$2" o
  # Match either the display name or the binary. Names are what the table shows;
  # binaries are what the log files are named after, so `--only agent` has to
  # select Cursor or someone reading agent.log gets a silent empty run. (`--only
  # cursor` also works — it matches the display name.)
  for o in ${SKIP[@]+"${SKIP[@]}"}; do
    [[ "${n,,}" == *"${o,,}"* || "${b,,}" == *"${o,,}"* ]] && return 1
  done
  [ ${#ONLY[@]} -eq 0 ] && return 0
  for o in "${ONLY[@]}"; do
    [[ "${n,,}" == *"${o,,}"* || "${b,,}" == *"${o,,}"* ]] && return 0
  done
  return 1
}

# ── Reading the report ─────────────────────────────────────────

# One line, no pipes or quotes. These strings land in a |-delimited array, a
# fixed-width table, and hand-rolled JSON, and a blocker quoted from a traceback can
# contain any of those characters.
clean() {
  local s max="${2:-72}"
  s=$(printf '%s' "$1" | tr '\r\n\t|"\\' '      ' \
      | sed -E $'s/\033\\[[0-9;]*[A-Za-z]//g; s/  +/ /g; s/^ +//; s/ +$//')
  [ "${#s}" -gt "$max" ] && s="${s:0:$max}…"
  printf '%s' "$s"
}

# The value of one report label, read from the body on stdin.
#
# Two guards. Lines holding an angle bracket are the prompt's own template rather
# than a report — a real report is asked for without any, and Codex echoes the whole
# prompt into its log — so they are dropped outright. And of what survives we take
# the LAST match, because the report is the end of the reply.
report_field() {
  local label="$1"
  grep -v '<' \
    | grep -E "^[^A-Za-z]*${label}\**:" \
    | tail -1 \
    | sed -E "s/^[^A-Za-z]*${label}\**:\**[[:space:]]*//"
}

# Claude (--output-format stream-json) and Cursor (cursor-agent) emit the whole
# self-report inside an escaped JSON string, so its lines arrive as literal \n
# sequences in the middle of one very long line. Restore line structure (first sed),
# then drop the JSON that trails the report's closing quote — `"}]}` from a content
# array or a bare `","` from a result-level string, e.g. Cursor's `","session_id":...`
# (second sed, a separate process so it sees the already-split lines, not the mega-line),
# and finally drop quoted-context lines echoed back from the prompt. No-op on plain-text
# logs. Every consumer of a log goes through here — classify() and the --e2e claim
# capture both need the same shape, and two copies of the transform would drift.
unwrap_log() {
  sed 's/\\n/\
/g' "$1" 2>/dev/null | sed 's/"[]}),].*$//' | grep -v '^[[:space:]]*>'
}

# Treat an empty, absent, or explicitly-nothing field as nothing.
is_none() {
  local v="${1^^}"
  [ -z "$v" ] || [[ "$v" =~ ^(NONE|N/A|NA|NOTHING|-)[.]?$ ]]
}

# Which known failure mode a self-reported blocker describes. Bare phrases are safe
# to match here, unlike in the log scan below: this is one line the assistant wrote
# about itself, with the prompt's own template already filtered out.
blocker_category() {
  local t="$1"
  grep -qiE 'unknown (flag|option|argument)|unrecognized argument|unsupported flag'  <<< "$t" && { echo flag;   return; }
  grep -qiE 'no module named|modulenotfound|importerror|falconpy.*not|pip install|venv' <<< "$t" && { echo deps; return; }
  grep -qiE 'CLAUDE_PLUGIN_ROOT|python\.sh: (no such|not found)|/scripts/python\.sh: '  <<< "$t" && { echo root; return; }
  grep -qiE '401|403|unauthorized|forbidden|invalid_client|access denied|authenticat' <<< "$t" && { echo auth; return; }
  grep -qiE '\btty\b|terminal device|/dev/tty'                                        <<< "$t" && { echo tty;  return; }
  grep -qiE 'trusted directory|not trusted'                                          <<< "$t" && { echo trust; return; }
  # A --e2e-only failure, and a harness fault rather than a skills one: two assistants
  # that ignored the slug suffix authored the same workflow name and churned the
  # tenant. Categorised separately so it cannot be read as an assistant problem.
  grep -qiE 'name already exists|already in use|duplicate (workflow|definition)'      <<< "$t" && { echo dupname; return; }
  # A server-side 5xx from the import/release API (Internal Server Error, trace-id
  # for support). The workflow validated locally; the tenant API failed the import
  # itself. Categorised separately from a skills fault so a run of API 500s on
  # complex workflows (a known platform behaviour) is legible and trackable.
  grep -qiE 'internal server error|HTTP 50[0-9]\b|\b50[0-9] (internal server|bad gateway|service unavailable)|import failed.*(internal server|50[0-9])' <<< "$t" && { echo api500; return; }
  echo other
}

# Returns STATUS|CATEGORY|detail|skills|commands. The category is the trackable part:
# it says which known failure mode was hit, so counts can be compared across runs and
# branches.
#
# Two sources, in this order. First the log, for the handful of errors that are
# decisive whatever the assistant believes happened — anchored on a real error
# prefix, because assistants echo skill text that discusses these same strings and
# matching bare phrases reports our own documentation as a failure. Then the
# assistant's own report, which is what decides everything else. Nothing is inferred
# from how far the transcript got: that could not tell "still building" from "sat
# there doing nothing", and it read a clean timeout as success.
classify() {
  local log="$1" rc="$2" body status skills raw_cmds raw_blocker cmds detail cat
  # Unwrap stream-json/cursor-agent JSON so a self-report inside an escaped string
  # becomes real lines report_field can match (see unwrap_log). No-op on plain text.
  body=$(unwrap_log "$log")

  # An account-level block — quota or subscription exhausted — is not a skills or
  # harness fault and cannot be fixed by re-running, so treat it as an environment SKIP
  # (like a missing CLI), not a failure. Anchored on assistant billing phrasing so it
  # cannot match a skill doc's own "rate limit" guidance.
  grep -qiE "quota reached|quota exceeded|upgrade your subscription|subscription (required|expired|to increase)|insufficient (credits|quota)|out of (credits|quota)" <<< "$body" && { echo "SKIP|account|account quota/subscription limit reached||"; return; }

  # A transient backend error — the assistant's own model service is momentarily
  # busy ("Our servers are experiencing high traffic right now, please try again in
  # a minute"). Not a skills or harness fault and it clears on a retry, so treat it
  # as an environment SKIP like a quota block, not a failure. Anchored on
  # backend-busy phrasing so it cannot match a skill doc's own throttling guidance.
  grep -qiE "experiencing high traffic|our servers are (experiencing|busy|overloaded)|temporarily (unavailable|overloaded)|(server|service) is (busy|overloaded)|please try again in a (minute|moment|few)|overloaded_error" <<< "$body" && { echo "SKIP|transient|assistant backend busy — retryable||"; return; }

  # A Python traceback for a missing dependency is decisive: the venv was never built
  # (the SessionStart hook is Claude-only) or the script was run outside python.sh.
  grep -qiE "ModuleNotFoundError|No module named '(falconpy|yaml|tomli)'" <<< "$body" && { echo "FAIL|deps|missing Python dependency (venv not built?)||"; return; }
  # An unresolved ${CLAUDE_PLUGIN_ROOT} means the skill's own invocation path expanded
  # empty — the env var is set only by Claude Code. The real failure surfaces as the
  # shell's own "No such file"/"command not found" on the python.sh path; match ONLY
  # that, never the bare variable name, because a SKILL.md documents `$CLAUDE_PLUGIN_ROOT`
  # for Claude users and an assistant streaming that doc text would otherwise false-fail.
  grep -qiE '(^|/)scripts/python\.sh: (No such file|command not found)' <<< "$body" && { echo "FAIL|root|CLAUDE_PLUGIN_ROOT unset — script path did not resolve||"; return; }
  # A launch-flag rejection by the ASSISTANT CLI itself is decisive. A fusion *script's*
  # argparse error (e.g. "action_search.py: error: unrecognized arguments") is NOT — the
  # assistant can fix the args and retry — so it is deliberately not matched here; it
  # flows through to the report/OK-FAIL logic below.
  grep -qiE "^[[:space:]]*(❌[[:space:]]*)?(Error|error): (unknown|unrecognized|unsupported) (flag|option)" <<< "$body" && { echo "FAIL|flag|the assistant CLI rejected a launch flag||"; return; }
  grep -qiE "401 Unauthorized|403 Forbidden|\"?errors\"?.*invalid_client|access denied|Failed to authenticate|Could not authenticate" <<< "$body" && { echo "FAIL|auth|credentials rejected by the tenant||"; return; }
  grep -qiE "^[[:space:]]*(❌[[:space:]]*)?Error: no TTY available|^[[:space:]]*(❌[[:space:]]*)?could not open a new TTY|/dev/tty: device not configured" <<< "$body" && { echo "FAIL|tty|CLI demanded a TTY||"; return; }
  grep -qiE "Not inside a trusted directory" <<< "$body" && { echo "FAIL|trust|refused to run in this directory||"; return; }

  status=$(report_field STATUS      <<< "$body")
  raw_cmds=$(report_field COMMANDS  <<< "$body")
  raw_blocker=$(report_field BLOCKER <<< "$body")
  skills=$(clean "$(report_field SKILLS <<< "$body")" 54)
  cmds=$(clean "$raw_cmds" 54)

  # Count outcomes on the raw value, before it is truncated for display.
  local oks fails
  oks=$(grep -oiE '=>[[:space:]]*OK' <<< "$raw_cmds" | grep -c .)
  fails=$(grep -oiE '=>[[:space:]]*FAIL' <<< "$raw_cmds" | grep -c .)

  if [ -z "$status" ]; then
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
      echo "FAIL|stalled|cut off at ${TIMEOUT}s with no report|$skills|$cmds"; return
    fi
    echo "FAIL|other|stopped without reporting (exit $rc)|$skills|$cmds"; return
  fi

  # Our own time budget is not a failure, however the assistant phrases it. Discard
  # that class of blocker before judging.
  if grep -qiE 'time (budget|limit)|timed? (out|harness)|harness limit|ran out of time|60[- ]second' <<< "$raw_blocker"; then
    raw_blocker=NONE
    grep -qi 'BLOCK' <<< "$status" && status=WORKING
  fi

  # A model sometimes writes the NONE sentinel straight into an explanatory
  # sentence with no separator ("NONEThe background job was stopped") — it meant
  # NONE and merely broke the one-line contract. The glued capital letter is the
  # signature; a genuine "None of the actions could be discovered" keeps its space
  # and is left intact. Only when the model did not self-report BLOCKED.
  if [[ "$raw_blocker" =~ ^[Nn][Oo][Nn][Ee][A-Za-z] ]] && ! grep -qi 'BLOCK' <<< "$status"; then
    raw_blocker=NONE
  fi

  # A real blocker is the result, whatever else the assistant managed to do.
  if grep -qi 'BLOCK' <<< "$status" || ! is_none "$raw_blocker"; then
    cat=$(blocker_category "$raw_blocker")
    detail=$(clean "blocked: ${raw_blocker:-no detail given}" 40)
    echo "FAIL|$cat|$detail|$skills|$cmds"; return
  fi

  # In --e2e the bar is an artifact rather than activity. "N scripts OK" was exactly
  # the verdict that let a run pass with nothing on the tenant, so a definition id is
  # the only thing that counts here. Whether the id is real is --judge's call.
  if [ "$E2E" -eq 1 ]; then
    local wf def
    wf=$(report_field WORKFLOW   <<< "$body")
    def=$(report_field DEFINITION <<< "$body")
    is_none "$wf" && wf="?"
    if is_none "$def"; then
      echo "FAIL|nodeploy|$(clean "no definition id (workflow: $wf)" 40)|$skills|$cmds"; return
    fi
    echo "PASS|deployed|$(clean "imported $wf · $def" 40)|$skills|$cmds"; return
  fi

  # No blocker, so the pass needs evidence — and the report carries it: a fusion-skills
  # script the assistant says came back OK.
  if [ "$oks" -gt 0 ]; then
    if grep -qiE 'DONE|COMPLETE|FINISH' <<< "$status"; then
      detail="authored the workflow · ${oks} script(s) OK"
    else
      detail="${oks} fusion script(s) OK, no blocker"
    fi
    echo "PASS|ok|$detail|$skills|$cmds"; return
  fi
  [ "$fails" -gt 0 ] && {
    cat=$(blocker_category "$raw_cmds")
    echo "FAIL|$cat|$(clean "every script failed: $raw_cmds" 40)|$skills|$cmds"; return
  }
  echo "FAIL|stalled|reported $(clean "$status" 12) but ran no scripts|$skills|$cmds"
}

# Confirm the tenant is reachable ONCE before launching, so a credential problem
# surfaces here as a clear message instead of as five identical auth failures in the
# logs. There is no shared token file to warm (FalconPy authenticates in memory), so
# this is a reachability probe, not a cache write.
warm_creds() {
  local q="$REPO/skills/deployment/scripts/query_workflows.py"
  local runner="$REPO/scripts/python.sh"
  [ -x "$runner" ] && [ -f "$q" ] || return 0
  head2 "Checking the tenant is reachable"
  if "$runner" "$q" --list >/dev/null 2>&1; then
    ok "tenant reachable — credentials resolve"
  else
    warn "could not reach the tenant with the current credentials"
    info "assistants will each hit the same auth failure — set FALCON_CLIENT_ID/SECRET"
    info "or run /crowdstrike-falcon-fusion:setup, then re-run this harness"
  fi
}

# ── Execution ──────────────────────────────────────────────────
# Everything above is pure definitions. A unit test sources this file with
# FUSION_ASSISTANTS_LIB=1 to exercise classify()/report_field()/blocker_category()
# against synthetic logs without launching anything or touching the filesystem.
[ "${FUSION_ASSISTANTS_LIB:-0}" = "1" ] && return 0

# Run-time prerequisites (only for an actual run — not needed when sourced as a library
# by test-verdict-parser.sh, which returns above with just the pure functions).
TIMEOUT_BIN=$(command -v timeout || command -v gtimeout || true)
[ -z "$TIMEOUT_BIN" ] && { echo "ERROR: needs 'timeout' or 'gtimeout' (brew install coreutils)" >&2; exit 1; }
mkdir -p "$LOG_DIR"

recover_orphans           # helper: reclaim a ~/.agents/skills stash from a killed run
recover_codex_cache_orphan

if [ "$ISOLATE" -eq 1 ]; then
  isolate
else
  [ "$JUDGE" -eq 0 ] && warn "bias control skipped (--no-isolate): results may reflect an installed copy"
fi

[ "$PARALLEL" -eq 1 ] && [ "$JUDGE" -eq 0 ] && warm_creds

RUN_START=$(date +%s)
[ "$JUDGE" -eq 0 ] && head2 "Running"
if [ "$JUDGE" -eq 1 ]; then
  :
elif [ "$E2E" -eq 1 ]; then
  info "END-TO-END: real import required · report at ${REPORT_AT}s · hard cap ${TIMEOUT}s · logs in ${LOG_DIR/#$HOME/\~}"
  info "a PASS means the assistant reported a definition id; confirm it with --judge"
else
  info "real workflow-creation prompt · self-report at ${REPORT_AT}s · hard cap ${TIMEOUT}s · logs in ${LOG_DIR/#$HOME/\~}"
fi
printf '\n'

RESULTS=(); CATEGORIES=(); FAILURES=0; TESTED=0; SKIPPED=0

# Two groups, because they need OPPOSITE filesystem state and cannot overlap:
# --plugin-dir assistants run with this repo's symlinks stashed away, while Codex and
# Antigravity need those same symlinks present. Set the state once per group, run the
# group in parallel, then move on. Wall clock becomes the slowest member of each group
# instead of the sum of all five.
# Sets LAUNCHED_PID / LAUNCHED_START. Deliberately NOT echoing them: calling this via
# $(...) would run it in a subshell that owns the background job, and the parent shell
# then cannot wait on the pid ("is not a child of this shell").
LAUNCHED_PID=""
LAUNCHED_START=""
launch() {   # name bin source argv
  local name="$1" bin="$2" argv="$4" log="$LOG_DIR/${2}.log"
  local -a parts=() cmd=()
  read -r -a parts <<< "$argv"
  cmd=("$bin")
  # The canonical prompt, then the mode's instructions. Appended, never spliced:
  # CI asserts the PROMPT line still starts with the README example text.
  local tail_instructions work_dir="$LOG_DIR"
  if [ "$E2E" -eq 1 ]; then
    # Its own directory, or assistants author on top of each other. The binary name
    # doubles as the workflow-name suffix — short, and already unique per assistant.
    # Wipe it first so the judge never reads a YAML left by an earlier run (LOG_DIR
    # persists across runs).
    work_dir="$LOG_DIR/e2e/$bin"
    rm -rf "$work_dir"
    mkdir -p "$work_dir"
    tail_instructions=$(e2e_instructions "$bin")
  else
    tail_instructions=$(report_instructions)
  fi
  local full_prompt="${PROMPT}
${tail_instructions}"
  local pp
  for pp in "${parts[@]}"; do
    if [ "$pp" = "%%PROMPT%%" ]; then cmd+=("$full_prompt"); else cmd+=("$pp"); fi
  done
  local start; start=$(date +%s)
  # `set -m` gives each job its own process group so on_interrupt can signal the whole
  # tree. < /dev/null is load-bearing: `claude -p` reads stdin and, backgrounded
  # without a redirect, blocks on input that never arrives — full timeout, empty log.
  set -m
  ( cd "$work_dir" && env -u CLAUDECODE "$TIMEOUT_BIN" "$TIMEOUT" "${cmd[@]}" ) < /dev/null > "$log" 2>&1 &
  LAUNCHED_PID=$!
  set +m
  LAUNCHED_START=$start
}

report_one() {   # name bin source rc elapsed
  local name="$1" bin="$2" source="$3" rc="$4" elapsed="$5"
  local log="$LOG_DIR/${bin}.log" status category detail rskills rcmds
  IFS='|' read -r status category detail rskills rcmds <<< "$(classify "$log" "$rc")"
  case "$status" in
    PASS)    printf '  %s%-16s%s %s✔ PASS%s  %-43s %s%4ss%s\n' \
               "$BOLD" "$name" "$RESET" "$GREEN$BOLD" "$RESET" "$detail" "$DIM" "$elapsed" "$RESET" ;;
    SKIP)    printf '  %s%-16s%s %s⊘ SKIP%s  %-43s %s%4ss%s\n' \
               "$BOLD" "$name" "$RESET" "$YELLOW" "$RESET" "$detail" "$DIM" "$elapsed" "$RESET"
             SKIPPED=$((SKIPPED+1)) ;;
    TIMEOUT) printf '  %s%-16s%s %s◷ SLOW%s   %-43s %s%4ss%s\n' \
               "$BOLD" "$name" "$RESET" "$YELLOW$BOLD" "$RESET" "$detail" "$DIM" "$elapsed" "$RESET"
             FAILURES=$((FAILURES+1)) ;;
    *)       printf '  %s%-16s%s %s✘ FAIL%s  %s%-43s%s %s%4ss%s\n' \
               "$BOLD" "$name" "$RESET" "$RED$BOLD" "$RESET" "$RED" "$detail" "$RESET" "$DIM" "$elapsed" "$RESET"
             FAILURES=$((FAILURES+1)) ;;
  esac
  info "source: $source · log: ${log/#$HOME/\~}"
  # What it said it loaded and ran — the two things worth reading without opening the
  # log, and the pair that shows a pass came from the working tree.
  [ -n "$rskills" ] && info "skills: $rskills"
  [ -n "$rcmds" ]   && info "ran: $rcmds"
  # Only real failures contribute a failure-cause; an environment SKIP or a PASS does not.
  [ "$status" != "PASS" ] && [ "$status" != "SKIP" ] && CATEGORIES+=("$category")
  # --e2e records the two claims a judging pass needs as their own fields, not buried
  # in the display string: --judge has to look them up on the tenant.
  local rwf="" rdef=""
  if [ "$E2E" -eq 1 ]; then
    # Same unwrap as classify(): Claude and Cursor wrap the report in JSON, so
    # report_field needs real lines to read the WORKFLOW and DEFINITION claims.
    local ebody; ebody=$(unwrap_log "$log")
    rwf=$(clean "$(report_field WORKFLOW   <<< "$ebody")" 60)
    rdef=$(clean "$(report_field DEFINITION <<< "$ebody")" 60)
    # Persist the claims so a standalone `--judge` — a separate process, where the
    # in-memory RESULTS is gone — can still match by the authoritative definition id
    # instead of falling back to the workflow name.
    mkdir -p "$LOG_DIR/e2e/$bin"
    printf '%s\t%s\n' "$rwf" "$rdef" > "$LOG_DIR/e2e/$bin/claim.tsv"
  fi
  RESULTS+=("$name|$status|$category|$detail|$elapsed|$source|$rskills|$rwf|$rdef")
  # An environment SKIP (account/quota) is "could not test", like a missing CLI — it is
  # not counted among the tested assistants and never fails the run.
  [ "$status" = "SKIP" ] || TESTED=$((TESTED+1))
  return 0
}

run_group() {
  local want_src="$1"
  # `local -a x` leaves the array UNSET under set -u; `=()` makes it set-but-empty.
  local -a g_names=() g_bins=() g_pids=() g_starts=()
  local entry name bin source argv

  for entry in "${ASSISTANTS[@]}"; do
    IFS='|' read -r name bin source argv <<< "$entry"
    want "$name" "$bin" || continue
    [ "$source" = "$want_src" ] || continue
    if ! command -v "$bin" >/dev/null 2>&1; then
      printf '  %s%-16s SKIP%s    %s not installed\n' "$DIM" "$name" "$RESET" "$bin"
      RESULTS+=("$name|SKIP|skip|not installed|0|none|")
      continue
    fi
    g_names+=("$name"); g_bins+=("$bin")
  done
  [ ${#g_names[@]} -eq 0 ] && return 0

  # Set the filesystem state ONCE for the whole group.
  [ "$want_src" = "~/.agents/skills" ] && link_repo_skills

  local i
  for i in "${!g_names[@]}"; do
    for entry in "${ASSISTANTS[@]}"; do
      IFS='|' read -r name bin source argv <<< "$entry"
      [ "$name" = "${g_names[$i]}" ] || continue
      launch "$name" "$bin" "$source" "$argv"
      g_pids+=("$LAUNCHED_PID"); g_starts+=("$LAUNCHED_START")
      CHILD_PIDS+=("$LAUNCHED_PID")
      if [ "$PARALLEL" -eq 1 ]; then
        printf '  %s%-16s%s %s▸ running%s\n' "$BLUE" "$name" "$RESET" "$DIM" "$RESET"
      else
        printf '  %s%-16s%s running… ' "$BLUE" "$name" "$RESET"
        wait "${g_pids[$i]}"; g_rcs[$i]=$?
        printf '\r'
      fi
      break
    done
  done

  if [ "$PARALLEL" -eq 1 ]; then
    printf '\n'
    for i in "${!g_pids[@]}"; do wait "${g_pids[$i]}"; g_rcs[$i]=$?; done
  fi

  for i in "${!g_names[@]}"; do
    for entry in "${ASSISTANTS[@]}"; do
      IFS='|' read -r name bin source argv <<< "$entry"
      [ "$name" = "${g_names[$i]}" ] || continue
      report_one "$name" "$bin" "$source" "${g_rcs[$i]:-1}" \
        "$(( $(date +%s) - ${g_starts[$i]} ))"
      break
    done
  done

  [ "$want_src" = "~/.agents/skills" ] && unlink_repo_skills
  CHILD_PIDS=()
  return 0
}

# ── Judging ───────────────────────────────────────────────────────
# A PASS above means the assistant SAID it imported a workflow. This checks whether it
# did, and whether the YAML it authored contains the stages that were asked for. Every
# check reads a file or the tenant; none of it trusts the transcript.
#
# Workflows are located by identity — the definition name/id — rather than by path,
# because the per-assistant working directory is advisory: an assistant that `cd`s
# elsewhere and authors there would fail a path-only judge that genuinely worked. A
# YAML found outside its working directory is reported as "escaped", not as a failure.

PYTHON_RUNNER="$REPO/scripts/python.sh"
QUERY_WF="$REPO/skills/deployment/scripts/query_workflows.py"

# Every workflow definition on the tenant, as raw JSON, fetched once and reused. Used
# only to test presence of a name or id — never to read a secret.
TENANT_DEFS=""
load_tenant_defs() {
  [ -x "$PYTHON_RUNNER" ] && [ -f "$QUERY_WF" ] || return 0
  TENANT_DEFS=$("$PYTHON_RUNNER" "$QUERY_WF" --list --json 2>/dev/null || true)
}

# Sets WF_PATH and FOUND_OUTSIDE. Deliberately not echoing the path: called through
# $(...) the assignments would happen in a subshell and never reach the caller, which
# is the same trap that broke launch() returning a pid.
WF_PATH=""
FOUND_OUTSIDE=""
find_workflow_yaml() {   # bin
  local bin="$1" f
  local -a cands=()
  WF_PATH=""; FOUND_OUTSIDE=""
  # A workflow YAML has a trigger and an actions/nodes block. Search the assistant's
  # working directory, newest file first, and take the first that qualifies. The work
  # dir is wiped at the start of each --e2e run, so this normally sees just the authored
  # file; the mtime order is a guard for the case where more than one lands there.
  while IFS= read -r f; do cands+=("$f"); done \
    < <(find "$LOG_DIR/e2e/$bin" -maxdepth 4 \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null)
  if [ "${#cands[@]}" -gt 0 ]; then
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      if grep -qiE '^(trigger|triggers):' "$f" 2>/dev/null && grep -qiE '^(actions|nodes):' "$f" 2>/dev/null; then
        WF_PATH="$f"; return
      fi
    done < <(ls -1t "${cands[@]}" 2>/dev/null)
  fi
  # Not where we put it. Fall back to whatever YAML path the transcript mentions.
  f=$(grep -oE '/[^ "]*\.(yml|yaml)' "$LOG_DIR/${bin}.log" 2>/dev/null | head -1)
  if [ -n "$f" ] && [ -f "$f" ]; then
    WF_PATH="$f"; FOUND_OUTSIDE=1
  fi
}

# Present-or-not for the stages the skills are supposed to produce. Each maps to one
# sub-skill or authoring step, so a missing mark says which part did not really run.
# Mirrors the pipeline-stage markers in test-skill.sh.
judge_one() {   # name bin claimed_workflow claimed_definition
  local name="$1" bin="$2" c_wf="$3" c_def="$4"
  local wf app_name marks="" notes="" state

  find_workflow_yaml "$bin"; wf="$WF_PATH"
  if [ -z "$wf" ]; then
    # Some CLIs author in a private session workspace we can't read afterward — Copilot
    # writes to ~/.copilot/session-state/<id>/files/, and its transcript line-wraps the
    # path so the log fallback can't recover it either. The deploy can still be real: if
    # the claimed definition id is on the tenant, report ON-TENANT with the per-skill
    # markers left unread, rather than a red failure for a workflow that actually shipped.
    if [ -n "$TENANT_DEFS" ] && [ -n "$c_def" ] && ! is_none "$c_def" \
       && printf '%s' "$TENANT_DEFS" | grep -q "$c_def"; then
      printf '  %s%-16s%s %s%-9s%s %-26s %s%s%s\n' \
        "$BOLD" "$name" "$RESET" "$GREEN$BOLD" "ON-TENANT" "$RESET" "${c_wf:-?}" \
        "$DIM" "markers unread (YAML not on disk)" "$RESET"
      JUDGED+=("$name|OK|$c_wf|ON-TENANT|"); return
    fi
    printf '  %s%-16s%s %s✘ NO YAML%s nothing on disk and no on-tenant definition\n' \
      "$BOLD" "$name" "$RESET" "$RED$BOLD" "$RESET"
    JUDGED+=("$name|NOYAML|||"); return
  fi
  app_name=$(sed -n 's/^name:[[:space:]]*//p' "$wf" | head -1)
  # Strip surrounding quotes so a quoted `name: '...'` still matches the tenant's
  # plain name (the tenant stores the unquoted value).
  app_name="${app_name#[\"\']}"; app_name="${app_name%[\"\']}"
  [ -n "$FOUND_OUTSIDE" ] && notes="authored outside its working directory: ${wf/#$HOME/\~}"

  # Pipeline-stage markers, read from the authored YAML.
  grep -qiE 'Inline\.QueryEvent|event.?query'                    "$wf" && marks+="eventquery " || marks+="---------- "
  grep -qiE 'Inline\.HTTPRequest|http.?request'                  "$wf" && marks+="http "       || marks+="---- "
  grep -qiE 'charlotte|llminvocator|completion'                  "$wf" && marks+="llm "        || marks+="--- "
  grep -qiE 'send.?email|SendEmail|msg_type|email'               "$wf" && marks+="email "      || marks+="----- "
  # Authoring discipline: every action carries a version_constraint. A workflow with
  # actions but no version_constraint is the classic authoring miss.
  grep -qE 'version_constraint'                                  "$wf" && marks+="vc "         || marks+="-- "

  # Ground truth: is the definition actually on the tenant? Match the claimed id first
  # (authoritative), then fall back to the authored name.
  state="ABSENT"
  if [ -n "$TENANT_DEFS" ]; then
    if { [ -n "$c_def" ] && ! is_none "$c_def" && printf '%s' "$TENANT_DEFS" | grep -q "$c_def"; } \
       || { [ -n "$app_name" ] && printf '%s' "$TENANT_DEFS" | grep -q "\"$app_name\""; }; then
      state="ON-TENANT"
    fi
  fi

  local verdict colour
  case "$state" in
    ON-TENANT) verdict=OK;       colour=$GREEN ;;
    *)         verdict=NOTENANT; colour=$RED ;;
  esac
  # The disagreement that matters: it claimed a definition the tenant cannot show.
  [ "$verdict" = NOTENANT ] && [ -n "$c_def" ] && ! is_none "$c_def" \
    && notes="claimed $c_def but the tenant has no such definition"
  [ -n "$c_wf" ] && ! is_none "$c_wf" && [ "$c_wf" != "$app_name" ] && [ "$c_wf" != "?" ] \
    && notes="${notes:+$notes; }reported \"$c_wf\" but the YAML says \"$app_name\""

  printf '  %s%-16s%s %s%-9s%s %-26s %s%s%s\n' \
    "$BOLD" "$name" "$RESET" "$colour$BOLD" "$state" "$RESET" "${app_name:-?}" "$DIM" "$marks" "$RESET"
  [ -n "$notes" ] && info "$notes"
  JUDGED+=("$name|$verdict|$app_name|$state|$marks")
}

run_judge() {
  head2 "Judging against the tenant and the YAML on disk"
  info "state · workflow · eventquery http llm email vc   (dashes mean absent)"
  load_tenant_defs
  [ -z "$TENANT_DEFS" ] && warn "could not list tenant definitions — on-tenant checks will read ABSENT"
  JUDGED=()
  local entry name bin source argv r rn rwf rdef
  for entry in "${ASSISTANTS[@]}"; do
    IFS='|' read -r name bin source argv <<< "$entry"
    want "$name" "$bin" || continue
    rwf=""; rdef=""
    for r in ${RESULTS[@]+"${RESULTS[@]}"}; do
      IFS='|' read -r rn _ _ _ _ _ _ rwf rdef <<< "$r"
      [ "$rn" = "$name" ] && break
      rwf=""; rdef=""
    done
    # Standalone --judge runs in a separate process from --e2e, so RESULTS is empty
    # and the loop above found nothing. Reload the claims --e2e persisted to disk so
    # the match can key on the authoritative definition id, not just the name.
    if [ -z "$rdef" ] && [ -r "$LOG_DIR/e2e/$bin/claim.tsv" ]; then
      IFS=$'\t' read -r rwf rdef < "$LOG_DIR/e2e/$bin/claim.tsv"
    fi
    judge_one "$name" "$bin" "$rwf" "$rdef"
  done
}

declare -a g_rcs=()
if [ "$JUDGE" -eq 0 ]; then
  run_group "--plugin-dir"
  run_group "~/.agents/skills"
fi

WALL=$(( $(date +%s) - RUN_START ))
SEQ=0
for r in ${RESULTS[@]+"${RESULTS[@]}"}; do
  IFS='|' read -r _n _s _c _d _e _src _sk <<< "$r"
  SEQ=$(( SEQ + ${_e:-0} ))
done

if [ "$JUDGE" -eq 1 ]; then
  run_judge
  printf '\n'
  exit 0
fi

head2 "Summary"
skipnote=""
[ "$SKIPPED" -gt 0 ] && skipnote="   ${DIM}·  ${SKIPPED} skipped (environment)${RESET}"
if [ "$TESTED" -eq 0 ]; then
  if [ "$SKIPPED" -gt 0 ]; then
    printf '  no assistants tested (%s skipped: account/quota)\n' "$SKIPPED"
  else
    printf '  no assistants tested\n'
  fi
elif [ "$FAILURES" -eq 0 ]; then
  printf '  %s%s✔ %s of %s%s reached the tenant%s%s\n' \
    "$GREEN" "$BOLD" "$TESTED" "$TESTED" "$RESET$GREEN" "$RESET" "$skipnote"
  printf '    %s%ss wall clock · %ss if run one at a time%s\n' "$DIM" "$WALL" "$SEQ" "$RESET"
else
  printf '  %s%s%s of %s%s reached the tenant%s   %s│%s   %s%s✘ %s failed%s%s\n' \
    "$GREEN" "$BOLD" "$((TESTED-FAILURES))" "$TESTED" "$RESET$GREEN" "$RESET" \
    "$DIM" "$RESET" "$RED" "$BOLD" "$FAILURES" "$RESET" "$skipnote"
  printf '    %s%ss wall clock · %ss if run one at a time%s\n' "$DIM" "$WALL" "$SEQ" "$RESET"
  printf '\n  %sfailures by cause%s\n' "$BOLD" "$RESET"
  # Counts per known failure mode, worth tracking run to run.
  printf '%s\n' ${CATEGORIES[@]+"${CATEGORIES[@]}"} | sort | uniq -c | sort -rn | while read -r n cat; do
    case "$cat" in
      stalled)    label="no report, or no script run before the cap"    ; col=$MAGENTA ;;
      deps)       label="missing Python dependency — venv not built"    ; col=$RED ;;
      root)       label="\${CLAUDE_PLUGIN_ROOT} unset — script path unresolved" ; col=$RED ;;
      auth)       label="credentials rejected by the tenant"            ; col=$RED ;;
      tty)        label="TTY demanded by the CLI"                       ; col=$MAGENTA ;;
      flag)       label="unsupported CLI flag"                          ; col=$YELLOW ;;
      trust)      label="refused to run in the test directory"          ; col=$YELLOW ;;
      timeout)    label="timed out"                                     ; col=$YELLOW ;;
      nodeploy)   label="never produced a definition id"                ; col=$RED ;;
      dupname)    label="workflow name collided on the tenant (harness fault)" ; col=$YELLOW ;;
      *)          label="other"                                         ; col=$DIM ;;
    esac
    printf '    %s%s×%s %s%s%s\n' "$BOLD" "$n" "$RESET" "$col" "$label" "$RESET"
  done
  printf '\n'
  if printf '%s\n' ${CATEGORIES[@]+"${CATEGORIES[@]}"} | grep -qx deps; then
    info 'A missing dependency means the managed venv was not built. The scripts build'
    info 'it on demand when run through scripts/python.sh — check the skill was loaded.'
  elif printf '%s\n' ${CATEGORIES[@]+"${CATEGORIES[@]}"} | grep -qx root; then
    info '${CLAUDE_PLUGIN_ROOT} is set only by Claude Code. A non-Claude assistant that'
    info 'copied that path literally cannot find scripts/python.sh — see the log.'
  elif printf '%s\n' ${CATEGORIES[@]+"${CATEGORIES[@]}"} | grep -qx auth; then
    info 'Credentials did not resolve. Set FALCON_CLIENT_ID / FALCON_CLIENT_SECRET or'
    info 'run /crowdstrike-falcon-fusion:setup, then re-run.'
  elif printf '%s\n' ${CATEGORIES[@]+"${CATEGORIES[@]}"} | grep -qx nodeploy; then
    info 'Reaching the tenant is not shipping to it. Read the log to see how far it'
    info 'got, and raise --timeout if it simply ran out of wall clock.'
  else
    info 'No dependency, auth, or TTY failures. Read the logs above.'
  fi
fi

if [ -n "$SAVE_FILE" ]; then
  {
    printf '{\n  "mode": "%s",\n  "report_at": %s,\n  "timeout": %s,\n  "isolated": %s,\n  "results": [\n' \
      "$([ "$E2E" -eq 1 ] && echo e2e || echo smoke)" \
      "$REPORT_AT" "$TIMEOUT" "$ISOLATE"
    first=1
    for r in "${RESULTS[@]}"; do
      IFS='|' read -r n st cat d e src sk wf def <<< "$r"
      [ $first -eq 0 ] && printf ',\n'; first=0
      printf '    {"assistant": "%s", "status": "%s", "category": "%s", "detail": "%s", "seconds": %s, "source": "%s", "skills": "%s", "workflow": "%s", "definition_id": "%s"}' \
        "$n" "$st" "$cat" "$d" "$e" "$src" "$sk" "$wf" "$def"
    done
    printf '\n  ]\n}\n'
  } > "$SAVE_FILE"
  info "saved $SAVE_FILE"
fi

printf '\n'
[ "$FAILURES" -eq 0 ]
