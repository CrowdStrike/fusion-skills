# shellcheck shell=bash
# state-dir.sh — per-user directory for the hooks' marker files.
#
# Sourced by the hook scripts. A fixed name in the shared /tmp lets another
# local user pre-create the marker (or a symlink in its place): the hook's
# rm would fail, and touch/echo would follow the symlink. macOS /tmp has no
# symlink protection. So markers live in a 0700 directory owned by the current
# user, preferring the per-user runtime/temp dirs. FUSION_HOOK_STATE_DIR
# overrides the location (tests use it).

# Print the state directory, creating it if needed. Returns 1 if it can't be
# trusted (not ours, a symlink, or not creatable); callers then skip markers.
fusion_state_dir() {
  local dir="${FUSION_HOOK_STATE_DIR:-${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/fusion-skills-$(id -u)}"
  dir="${dir%/}"
  [ -e "$dir" ] || [ -L "$dir" ] || mkdir -m 700 "$dir" 2>/dev/null || return 1
  [ -d "$dir" ] && [ ! -L "$dir" ] && [ -O "$dir" ] || return 1
  printf '%s' "$dir"
}
