#!/usr/bin/env bash
# python-detect.sh - Cross-platform Python discovery for the fusion-skills plugin.
# Source this file, then call find_compatible_python (echoes a python binary) or
# _pd_set_venv_bins <venv_dir> (sets VENV_PYTHON_BIN / VENV_PIP_BIN).
#
# Requires Python 3.13+ (fusion-skills' stated floor). 3.14+ is allowed.
# Supports: macOS (Homebrew, pyenv), Linux (apt, deadsnakes, pyenv, SCL), Windows (msys/cygwin).

# Version floor — fusion-skills requires Python 3.13+.
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=13
PYTHON_MAX_MINOR=14  # 3.14+ should work

_PD_RED='\033[0;31m'
_PD_GREEN='\033[0;32m'
_PD_YELLOW='\033[1;33m'
_PD_NC='\033[0m'

_pd_detect_platform() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)
            if [[ -f /etc/redhat-release ]]; then echo "rhel"
            elif [[ -f /etc/debian_version ]]; then echo "debian"
            else echo "linux"; fi ;;
        *) echo "unknown" ;;
    esac
}

# Parse "major.minor" from a python binary, or empty on failure.
_pd_get_python_version() {
    local python_bin="$1" version_output
    if [[ ! -x "$python_bin" ]] && ! command -v "$python_bin" &>/dev/null; then
        return 1
    fi
    version_output=$("$python_bin" --version 2>&1) || return 1
    echo "$version_output" | sed -n 's/^Python \([0-9]*\.[0-9]*\).*/\1/p'
}

# True if version is within the supported range (>= 3.13, <= 3.14).
_pd_is_version_supported() {
    local version="$1" major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    [[ "$major" -eq "$PYTHON_MIN_MAJOR" ]] || return 1
    [[ "$minor" -ge "$PYTHON_MIN_MINOR" ]] && [[ "$minor" -le "$PYTHON_MAX_MINOR" ]]
}

_pd_get_search_paths() {
    local platform="$1"
    case "$platform" in
        macos)
            echo "/opt/homebrew/bin"
            echo "/usr/local/bin"
            echo "/opt/homebrew/opt/python@3.13/bin"
            echo "/usr/local/opt/python@3.13/bin"
            echo "$HOME/.pyenv/shims"
            echo "/usr/bin"
            ;;
        debian)
            echo "/usr/bin"
            echo "$HOME/.pyenv/shims"
            echo "/usr/local/bin"
            ;;
        rhel)
            echo "/opt/rh/rh-python313/root/usr/bin"
            echo "/usr/bin"
            echo "$HOME/.pyenv/shims"
            ;;
        *)
            echo "/usr/bin"
            echo "/usr/local/bin"
            echo "$HOME/.pyenv/shims"
            ;;
    esac
}

# Set VENV_PYTHON_BIN and VENV_PIP_BIN for the given venv directory (OS-aware).
_pd_set_venv_bins() {
    local venv_dir="$1"
    if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
        # shellcheck disable=SC2034
        VENV_PYTHON_BIN="${venv_dir}/Scripts/python.exe"
        # shellcheck disable=SC2034
        VENV_PIP_BIN="${venv_dir}/Scripts/pip.exe"
    else
        # shellcheck disable=SC2034
        VENV_PYTHON_BIN="${venv_dir}/bin/python3"
        # shellcheck disable=SC2034
        VENV_PIP_BIN="${venv_dir}/bin/pip"
    fi
}

# Echo a path to a compatible Python binary, or return 1 if none found.
find_compatible_python() {
    local python_bin="" version="" platform
    platform=$(_pd_detect_platform)

    # 1. Explicit override.
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        if [[ -x "$PYTHON_BIN" ]] || command -v "$PYTHON_BIN" &>/dev/null; then
            version=$(_pd_get_python_version "$PYTHON_BIN")
            if _pd_is_version_supported "$version"; then echo "$PYTHON_BIN"; return 0; fi
            echo -e "${_PD_YELLOW}WARNING: PYTHON_BIN ($PYTHON_BIN) is Python $version, not 3.${PYTHON_MIN_MINOR}+${_PD_NC}" >&2
        else
            echo -e "${_PD_YELLOW}WARNING: PYTHON_BIN ($PYTHON_BIN) not found${_PD_NC}" >&2
        fi
    fi

    # 2. System python3, if in range.
    if command -v python3 &>/dev/null; then
        version=$(_pd_get_python_version "python3")
        if _pd_is_version_supported "$version"; then echo "python3"; return 0; fi
    fi

    # 3. Versioned binaries in PATH.
    for bin in "python3.14" "python3.13"; do
        if command -v "$bin" &>/dev/null; then
            version=$(_pd_get_python_version "$bin")
            if _pd_is_version_supported "$version"; then echo "$bin"; return 0; fi
        fi
    done

    # 4. Platform-specific paths.
    local search_path
    while IFS= read -r search_path; do
        [[ -d "$search_path" ]] || continue
        for bin in "python3.14" "python3.13" "python3"; do
            local full_path="$search_path/$bin"
            if [[ -x "$full_path" ]]; then
                version=$(_pd_get_python_version "$full_path")
                if _pd_is_version_supported "$version"; then echo "$full_path"; return 0; fi
            fi
        done
    done < <(_pd_get_search_paths "$platform")

    return 1
}

# Platform-specific install instructions when no compatible Python is found.
print_install_instructions() {
    local platform
    platform=$(_pd_detect_platform)
    echo ""
    echo -e "${_PD_RED}ERROR: No compatible Python found (fusion-skills requires 3.${PYTHON_MIN_MINOR}+)${_PD_NC}"
    echo ""
    case "$platform" in
        macos)
            echo "Install Python 3.13 on macOS:"
            echo "  brew install python@3.13"
            echo "  # or: brew install uv && uv python install 3.13"
            ;;
        debian)
            echo "Install Python 3.13 on Ubuntu/Debian:"
            echo "  sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update"
            echo "  sudo apt install python3.13 python3.13-venv"
            echo "  # or: curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13"
            ;;
        rhel)
            echo "Install Python 3.13 on RHEL/CentOS:"
            echo "  sudo dnf install python3.13"
            echo "  # or: curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13"
            ;;
        *)
            echo "Install Python 3.13 (cross-platform):"
            echo "  curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13"
            ;;
    esac
    echo ""
    echo "Then restart your terminal, or set PYTHON_BIN=/path/to/python3.13 and retry."
    echo ""
}
