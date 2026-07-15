"""Tests for the cold-start dependency bootstrap (`_bootstrap.ensure_deps`).

The shim re-execs a script through the managed venv wrapper when the marker
dependency (falconpy) is missing, so a bare `python script.py` in a
dependency-free interpreter still runs. These tests exercise the decision logic
without actually replacing the process, by patching `find_spec` and `os.execv`.
"""

import os

import _bootstrap


def _patch(monkeypatch, *, has_marker, guard=False, wrapper_exists=True):
    """Configure the environment `ensure_deps` inspects and capture execv calls."""
    monkeypatch.setattr(
        _bootstrap, "find_spec", lambda name: object() if has_marker else None
    )
    monkeypatch.setattr(_bootstrap.os.path, "exists", lambda _p: wrapper_exists)
    if guard:
        monkeypatch.setenv(_bootstrap._GUARD_ENV, "1")
    else:
        monkeypatch.delenv(_bootstrap._GUARD_ENV, raising=False)
    calls = []
    monkeypatch.setattr(_bootstrap.os, "execv", lambda path, args: calls.append((path, args)))
    return calls


def test_returns_without_reexec_when_dependency_present(monkeypatch):
    """Dependency already importable -> no re-exec, just return."""
    calls = _patch(monkeypatch, has_marker=True)
    _bootstrap.ensure_deps("/some/script.py")
    assert calls == []


def test_reexecs_through_wrapper_when_dependency_missing(monkeypatch):
    """Marker missing -> re-exec through bin/python.sh with the script + argv."""
    calls = _patch(monkeypatch, has_marker=False)
    monkeypatch.setattr(_bootstrap.sys, "argv", ["script.py", "--flag", "value"])
    _bootstrap.ensure_deps("/repo/skills/x/scripts/script.py")

    assert len(calls) == 1
    _path, args = calls[0]
    # Wrapper is invoked with: [wrapper, abs_script_path, *original_args]
    assert args[0].endswith(os.path.join("bin", "python.sh"))
    assert args[1] == os.path.abspath("/repo/skills/x/scripts/script.py")
    assert args[2:] == ["--flag", "value"]


def test_guard_prevents_reexec_loop(monkeypatch):
    """If already bootstrapped once, do not re-exec again (avoids infinite loop)."""
    calls = _patch(monkeypatch, has_marker=False, guard=True)
    _bootstrap.ensure_deps("/repo/script.py")
    assert calls == []


def test_no_reexec_when_wrapper_absent(monkeypatch):
    """Missing wrapper -> let the natural import error surface, do not execv."""
    calls = _patch(monkeypatch, has_marker=False, wrapper_exists=False)
    _bootstrap.ensure_deps("/repo/script.py")
    assert calls == []


def test_sets_guard_env_before_reexec(monkeypatch):
    """The guard env var must be set in the child so the child won't loop."""
    _patch(monkeypatch, has_marker=False)
    monkeypatch.setattr(_bootstrap.sys, "argv", ["script.py"])
    _bootstrap.ensure_deps("/repo/script.py")
    assert os.environ.get(_bootstrap._GUARD_ENV) == "1"
