#!/usr/bin/env bash
set -euo pipefail

# python.sh
# Runs a fusion-skills Python script using the managed venv so the correct Python
# and dependencies (crowdstrike-falconpy, pyyaml) are ALWAYS used — never a stale
# or dependency-free system Python.
#
# Usage: python.sh <script.py> [args...]

CACHE_DIR="${HOME}/.cache/claude-code-fusion"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=./python-detect.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/python-detect.sh"
_pd_set_venv_bins "${CACHE_DIR}/venv"

if [[ ! -x "$VENV_PYTHON_BIN" ]]; then
    # Normally the SessionStart hook builds the venv. If it hasn't (e.g. a bare
    # invocation in a cloned repo where the plugin hook never fired), build it
    # on demand so the script runs without manual setup.
    echo "fusion-skills Python venv not found; building it at ${CACHE_DIR}/venv ..." >&2
    if ! "${SCRIPT_DIR}/setup-python-venv.sh" >&2; then
        echo "ERROR: failed to build the fusion-skills Python venv." >&2
        exit 1
    fi
    _pd_set_venv_bins "${CACHE_DIR}/venv"
    if [[ ! -x "$VENV_PYTHON_BIN" ]]; then
        echo "ERROR: venv build reported success but ${VENV_PYTHON_BIN} is missing." >&2
        exit 1
    fi
fi

if [[ -z "${1:-}" ]]; then
    echo "ERROR: no script specified. Usage: python.sh <script.py> [args...]" >&2
    exit 1
fi

# Windows default codepage (cp1252) can't encode all API-response characters.
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
    export PYTHONIOENCODING=utf-8
fi

exec "$VENV_PYTHON_BIN" "$@"
