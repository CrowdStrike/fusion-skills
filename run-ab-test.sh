#!/usr/bin/env bash
#
# run-ab-test.sh — A/B test: baseline ref (RED) vs local branch (GREEN) for the
# fusion-skills plugin.
#
# Usage:
#   ./run-ab-test.sh              # baseline (main) vs local skills (5 runs each)
#   ./run-ab-test.sh 3            # 3 runs per phase
#   ./run-ab-test.sh --ref v1.2.2 # compare local branch against a specific tag
#   ./run-ab-test.sh --no-skill   # no plugin vs local skills (1 baseline run, timed)
#   ./run-ab-test.sh --fresh      # force baseline re-run even if cached
#   ./run-ab-test.sh --skip-deploy        # author + validate only (no live API)
#   ./run-ab-test.sh --skip-plugin-manage # don't disable/enable installed plugins
#
# Smart baseline caching:
#   First run:  RED (baseline ref) + GREEN (local branch)
#   Next runs:  Reuses cached baseline if ref hasn't changed, runs GREEN only
#   ref moves:  Detects stale baseline, re-runs RED automatically
#
# Uses claude --plugin-dir to load the plugin from either:
#   RED:   a temp checkout of the baseline ref (default: main)
#   GREEN: the local working tree
#
set -euo pipefail

# ── Argument parsing ──────────────────────────────────────────
RUNS=5
NO_SKILL=0
FRESH=0
SKIP_DEPLOY=0
SKIP_PLUGIN_MANAGE_ARG=0
BASELINE_REF=main
BASELINE_TIMEOUT=1800  # 30 minutes default for no-skill baseline

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-skill)
      NO_SKILL=1
      shift
      ;;
    --fresh)
      FRESH=1
      shift
      ;;
    --skip-deploy)
      SKIP_DEPLOY=1
      shift
      ;;
    --skip-plugin-manage)
      SKIP_PLUGIN_MANAGE_ARG=1
      shift
      ;;
    --ref)
      BASELINE_REF="$2"
      shift 2
      ;;
    --timeout)
      BASELINE_TIMEOUT="$2"
      shift 2
      ;;
    [0-9]*)
      RUNS="$1"
      shift
      ;;
    *)
      echo "Usage: $0 [--no-skill] [--fresh] [--skip-deploy] [--skip-plugin-manage] [--ref <git-ref>] [--timeout <seconds>] [N]"
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
AB_RESULTS_DIR="/tmp/fusion-skill-ab"
RED_DIR="$AB_RESULTS_DIR/red-runs"
GREEN_DIR="$AB_RESULTS_DIR/green-runs"
BASELINE_JSON="$AB_RESULTS_DIR/baseline.json"

# Pass-through flag for the inner test harness.
EXTRA_TEST_FLAGS=()
[ "$SKIP_DEPLOY" = "1" ] && EXTRA_TEST_FLAGS+=(--skip-deploy)

# Find the generated workflow YAML under a run directory (top-level 'trigger:').
find_workflow_file() {
  local dir="$1" f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if grep -qE '^trigger:' "$f" 2>/dev/null; then
      echo "$f"; return
    fi
  done < <(find "$dir" \( -name "*.yaml" -o -name "*.yml" \) -maxdepth 3 2>/dev/null)
  echo ""
}

# Clean up deployed Fusion workflows from a phase directory.
#
# Deletes via the Workflows delete API (scripts/cleanup_workflows.py, FalconPy
# delete_definitions) — no browser. Collects the workflow names deployed by each
# run in the phase, then deletes them by name. Skipped entirely in --skip-deploy
# mode, where nothing was imported.
cleanup_phase_workflows() {
  local phase_dir="$1"
  [ "$SKIP_DEPLOY" = "1" ] && return 0
  [ -d "$phase_dir" ] || return 0

  # Collect the workflow names deployed in this phase.
  local names=()
  for dir in "$phase_dir"/run-*/; do
    [ -d "$dir" ] || continue
    local wf_file wf_name
    wf_file=$(find_workflow_file "$dir")
    [ -n "$wf_file" ] || continue
    wf_name=$(grep -m1 -E '^name:' "$wf_file" 2>/dev/null | sed -E "s/^name:[[:space:]]*['\"]?(.+?)['\"]?[[:space:]]*$/\1/")
    [ -n "$wf_name" ] && names+=("$wf_name")
  done

  if [ ${#names[@]} -eq 0 ]; then
    return 0
  fi

  local cleanup_py="$REPO_ROOT/scripts/cleanup_workflows.py"
  if [ ! -f "$cleanup_py" ]; then
    echo "  NOTE: scripts/cleanup_workflows.py not found — skipping cleanup."
    echo "        Remove these manually in Falcon console → Fusion → Workflows:"
    printf '          - %s\n' "${names[@]}"
    return 0
  fi

  echo "  Deleting ${#names[@]} workflow(s) via the delete API..."
  # Best-effort: a failed delete must never abort the A/B run, so swallow
  # non-zero exit. cleanup_workflows.py reports per-name.
  python3 "$cleanup_py" --names "${names[@]}" || \
    echo "  NOTE: cleanup reported issues (see output above). Continuing."
}

BASELINE_SHA_FILE="$AB_RESULTS_DIR/baseline-main-sha"
OPTIMIZED_JSON="$AB_RESULTS_DIR/optimized.json"
MAIN_EXTRACT_DIR="$AB_RESULTS_DIR/main-branch"
mkdir -p "$AB_RESULTS_DIR"

# Copy test-skill.sh and its schema to /tmp so it works regardless of branch.
cp "$REPO_ROOT/test-skill.sh" /tmp/fusion-test-skill.sh
cp "$REPO_ROOT/test-result-schema.json" /tmp/test-result-schema.json
chmod +x /tmp/fusion-test-skill.sh
# The inner harness reads its schema relative to its own dir, so keep them together.
TEST_SKILL=/tmp/fusion-test-skill.sh

# ── Baseline staleness check ─────────────────────────────────
SKIP_RED=0
if [ "$NO_SKILL" != "1" ] && [ "$FRESH" != "1" ]; then
  CURRENT_BASELINE_SHA=$(git -C "$REPO_ROOT" rev-parse "$BASELINE_REF" 2>/dev/null || echo "")

  if [ -f "$BASELINE_JSON" ] && [ -f "$BASELINE_SHA_FILE" ] && [ -n "$CURRENT_BASELINE_SHA" ]; then
    CACHED_SHA=$(cat "$BASELINE_SHA_FILE")
    if [ "$CACHED_SHA" = "$CURRENT_BASELINE_SHA" ]; then
      SKIP_RED=1
      BASELINE_RUNS=$(jq -r '.runs // 0' "$BASELINE_JSON" 2>/dev/null || echo "?")
      echo "========================================="
      echo "  CACHED BASELINE FOUND"
      echo "========================================="
      echo "  Baseline ref:  $BASELINE_REF"
      echo "  Baseline SHA:  ${CURRENT_BASELINE_SHA:0:10}"
      echo "  Baseline runs: $BASELINE_RUNS"
      echo "  Baseline file: $BASELINE_JSON"
      echo ""
      echo "  Skipping RED phase — baseline ref hasn't changed."
      echo "  Use --fresh to force a new baseline."
      echo ""
    else
      echo "========================================="
      echo "  STALE BASELINE DETECTED"
      echo "========================================="
      echo "  Cached SHA:  ${CACHED_SHA:0:10}"
      echo "  Current SHA: ${CURRENT_BASELINE_SHA:0:10}"
      echo ""
      echo "  Baseline ref ($BASELINE_REF) has moved. Re-running RED phase."
      echo ""
    fi
  fi
fi

# ── Extract baseline ref to temp dir ─────────────────────────
if [ "$NO_SKILL" != "1" ]; then
  if [ "$SKIP_RED" != "1" ]; then
    echo "Extracting $BASELINE_REF plugin..."
    if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      echo "ERROR: $REPO_ROOT is not a git repository."
      echo "  run-ab-test.sh extracts the baseline ref via 'git archive'."
      echo "  Initialize the repo and commit a baseline before A/B testing."
      exit 1
    fi
    rm -rf "$MAIN_EXTRACT_DIR"
    mkdir -p "$MAIN_EXTRACT_DIR"
    # Extract the full plugin structure (skills, hooks, .claude-plugin, etc.).
    git -C "$REPO_ROOT" archive "$BASELINE_REF" | tar -x -C "$MAIN_EXTRACT_DIR"
    echo "  Extracted to: $MAIN_EXTRACT_DIR"
    echo ""
  fi
fi

if [ "$NO_SKILL" = "1" ]; then
  RUNS=1
  echo "========================================="
  echo "  A/B TEST: no-skill vs with-skill (1 run each)"
  echo "========================================="
  echo "  Baseline timeout: ${BASELINE_TIMEOUT}s"
  echo ""
  echo "  No-skill mode runs 1 vs 1 since the no-skill run is"
  echo "  expected to struggle without action discovery + validation."
else
  echo "========================================="
  if [ "$SKIP_RED" = "1" ]; then
    echo "  A/B TEST: GREEN only (${RUNS} runs, cached baseline)"
  else
    echo "  A/B TEST: ${RUNS} runs per phase"
  fi
  echo "========================================="
fi
echo "  Repo root:     $REPO_ROOT"
echo "  Baseline ref:  $BASELINE_REF (${MAIN_EXTRACT_DIR:-N/A})"
echo "  Local branch:  $REPO_ROOT"
[ "$SKIP_DEPLOY" = "1" ] && echo "  Mode:          author + validate only (--skip-deploy)"
if [ "$SKIP_RED" != "1" ]; then
  echo "  RED results:   $RED_DIR"
fi
echo "  GREEN results: $GREEN_DIR"
echo ""

# ── Pre-flight: ensure local plugin differs from baseline ────
if [ "$NO_SKILL" != "1" ]; then
  if [ "$SKIP_RED" = "1" ] && [ ! -d "$MAIN_EXTRACT_DIR" ]; then
    echo "Extracting $BASELINE_REF for pre-flight check..."
    rm -rf "$MAIN_EXTRACT_DIR"
    mkdir -p "$MAIN_EXTRACT_DIR"
    git -C "$REPO_ROOT" archive "$BASELINE_REF" | tar -x -C "$MAIN_EXTRACT_DIR"
  fi

  PREFLIGHT_DIFF=0
  # All six skills live under skills/; common/, hooks/, and use-cases/ stay at root.
  for dir in skills common hooks use-cases; do
    if [ -d "$MAIN_EXTRACT_DIR/$dir" ] || [ -d "$REPO_ROOT/$dir" ]; then
      if ! diff -rq "$MAIN_EXTRACT_DIR/$dir" "$REPO_ROOT/$dir" >/dev/null 2>&1; then
        PREFLIGHT_DIFF=1
        break
      fi
    fi
  done
  # Also check top-level files that affect plugin behavior.
  for f in CLAUDE.md hooks.json; do
    if [ -f "$MAIN_EXTRACT_DIR/$f" ] || [ -f "$REPO_ROOT/$f" ]; then
      if ! diff -q "$MAIN_EXTRACT_DIR/$f" "$REPO_ROOT/$f" >/dev/null 2>&1; then
        PREFLIGHT_DIFF=1
        break
      fi
    fi
  done
  if [ "$PREFLIGHT_DIFF" = "0" ]; then
    echo "ERROR: Local plugin files are identical to $BASELINE_REF."
    echo ""
    echo "  The A/B test compares baseline ref ($BASELINE_REF) skills (RED) vs local skills (GREEN)."
    echo "  If they're the same, the test is meaningless."
    echo ""
    echo "  Directories checked: workflows/, authoring/, deployment/, execution/, lookup-files/, setup/, common/, hooks/, use-cases/"
    echo "  Files checked: CLAUDE.md, hooks.json"
    echo ""
    exit 1
  fi
  echo "Pre-flight: local plugin files differ from $BASELINE_REF. Good."
  echo ""
fi

# ── Fresh run: prompt for workflow cleanup ───────────────────
if [ "$FRESH" = "1" ]; then
  echo "⚠️  Fresh run requested. Disable/delete test workflows in the Falcon"
  echo "   console (Fusion → Workflows) to avoid name collisions on import."
  echo ""
  if [ -d "$RED_DIR" ]; then
    echo "  Cleaning up RED phase workflows..."
    cleanup_phase_workflows "$RED_DIR"
  fi
  if [ -d "$GREEN_DIR" ]; then
    echo "  Cleaning up GREEN phase workflows..."
    cleanup_phase_workflows "$GREEN_DIR"
  fi
  echo ""
  read -p "  Press Enter when ready (or Ctrl+C to abort)... "
  echo ""
fi

# ── Disable installed Fusion plugins ─────────────────────────
# --plugin-dir adds a plugin, but installed marketplace plugins take priority.
# Disable them so --plugin-dir is the only source of Fusion skills/hooks.
ENABLED_FUSION_PLUGINS=()
if [ "$SKIP_PLUGIN_MANAGE_ARG" != "1" ]; then
  PLUGIN_LIST=$(claude plugin list 2>/dev/null || true)
  while IFS= read -r plugin; do
    if [ -n "$plugin" ] && echo "$PLUGIN_LIST" | grep -A3 "$plugin" | grep -q "enabled"; then
      ENABLED_FUSION_PLUGINS+=("$plugin")
    fi
  done < <(echo "$PLUGIN_LIST" | grep -oE '(fusion|falcon-fusion|crowdstrike-falcon-fusion)@[^ ]*' || true)

  if [ ${#ENABLED_FUSION_PLUGINS[@]} -gt 0 ]; then
    echo "Disabling installed Fusion plugins (using --plugin-dir instead):"
    for plugin in "${ENABLED_FUSION_PLUGINS[@]}"; do
      echo "  Disabling: $plugin"
      claude plugin disable "$plugin" 2>/dev/null || true
    done
    # Re-enable on exit (even if interrupted).
    trap 'echo ""; echo "Re-enabling Fusion plugins..."; for p in "${ENABLED_FUSION_PLUGINS[@]}"; do echo "  Enabling: $p"; claude plugin enable "$p" 2>/dev/null || true; done' EXIT
    echo ""
  fi
fi

# ── RED Phase ─────────────────────────────────────────────────
if [ "$SKIP_RED" = "1" ]; then
  echo "========================================="
  echo "  RED PHASE: Skipped (using cached baseline)"
  echo "========================================="
  echo ""
  echo "  Baseline: $BASELINE_JSON"
  echo ""

elif [ "$NO_SKILL" = "1" ]; then
  echo "========================================="
  echo "  RED PHASE: No skills (Fusion plugins disabled)"
  echo "========================================="
  echo ""
  echo "  Running 1 baseline test with ${BASELINE_TIMEOUT}s timeout..."
  echo ""

  # Run with timeout (macOS compatible: background + kill).
  "$TEST_SKILL" --save "$BASELINE_JSON" --runs 1 --dir "$RED_DIR" --no-plugin --skip-plugin-manage "${EXTRA_TEST_FLAGS[@]+"${EXTRA_TEST_FLAGS[@]}"}" &
  TEST_PID=$!
  ( sleep "$BASELINE_TIMEOUT" && kill "$TEST_PID" 2>/dev/null && echo "" && echo "  Baseline timed out after ${BASELINE_TIMEOUT}s" ) &
  TIMER_PID=$!
  wait "$TEST_PID" 2>/dev/null || true
  kill "$TIMER_PID" 2>/dev/null || true
  wait "$TIMER_PID" 2>/dev/null || true

else
  echo "========================================="
  echo "  RED PHASE: Baseline ($BASELINE_REF)"
  echo "========================================="
  echo ""

  "$TEST_SKILL" --save "$BASELINE_JSON" --runs "$RUNS" --dir "$RED_DIR" \
    --plugin-dir "$MAIN_EXTRACT_DIR" --skip-plugin-manage "${EXTRA_TEST_FLAGS[@]+"${EXTRA_TEST_FLAGS[@]}"}"

  # Save baseline SHA so we can detect staleness later.
  git -C "$REPO_ROOT" rev-parse "$BASELINE_REF" > "$BASELINE_SHA_FILE"
  echo ""
  echo "  Baseline SHA saved: $(cat "$BASELINE_SHA_FILE")"
fi

if [ "$SKIP_RED" != "1" ]; then
  echo ""
  echo "Baseline saved to: $BASELINE_JSON"
  echo ""

  # Clean up RED phase workflows so GREEN phase can reuse the same names.
  echo "Cleaning up RED phase workflows..."
  cleanup_phase_workflows "$RED_DIR"
  echo "  Done."
  echo ""
fi

# ── GREEN Phase: local branch ────────────────────────────────
# Clean up previous GREEN phase workflows before starting.
if [ -d "$GREEN_DIR" ]; then
  echo "Cleaning up previous GREEN phase workflows..."
  cleanup_phase_workflows "$GREEN_DIR"
  rm -rf "$GREEN_DIR"
  echo "  Done."
  echo ""
fi

echo "========================================="
echo "  GREEN PHASE: Local branch skills"
echo "========================================="
echo ""

"$TEST_SKILL" --save "$OPTIMIZED_JSON" --baseline "$BASELINE_JSON" --runs "$RUNS" --dir "$GREEN_DIR" \
  --plugin-dir "$REPO_ROOT" --skip-plugin-manage "${EXTRA_TEST_FLAGS[@]+"${EXTRA_TEST_FLAGS[@]}"}"

echo ""
echo "========================================="
echo "  A/B TEST COMPLETE"
echo "========================================="
echo "  Baseline: $BASELINE_JSON"
echo "  Optimized: $OPTIMIZED_JSON"
if [ "$SKIP_RED" != "1" ]; then
  echo "  RED runs:  $RED_DIR"
fi
echo "  GREEN runs: $GREEN_DIR"
echo ""
