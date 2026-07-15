#!/usr/bin/env bash
# bootstrap.sh — SessionStart hook for the fusion-skills plugin.
# Builds/refreshes the managed Python venv so the plugin's scripts always run with
# the right Python + dependencies. Best-effort: never blocks the session (exit 0).

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$PLUGIN_ROOT/bin/setup-python-venv.sh" >&2 || {
    echo "fusion-skills: venv bootstrap did not complete; scripts may need a manual venv setup (bin/setup-python-venv.sh)." >&2
}

exit 0
