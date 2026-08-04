"""
Tests for scripts/cleanup_workflows.py.

The script deletes Fusion workflows by name/pattern via the Workflows delete API
(``delete_definitions``). No browser, no network: the FalconPy client is a
MagicMock, so arg parsing, selector matching, target selection, and the
list→resolve→delete flow are exercised against fake API envelopes.
"""

from unittest.mock import MagicMock

import pytest

import cleanup_workflows as cw


# --------------------------------------------------------------------------
# parse_args
# --------------------------------------------------------------------------

def test_parse_names():
    args = cw.parse_args(["--names", "wf-1", "wf-2"])
    assert args.names == ["wf-1", "wf-2"]
    assert args.pattern is None
    assert args.all_test is False


def test_parse_pattern():
    args = cw.parse_args(["--pattern", "contain-*-run-*"])
    assert args.pattern == "contain-*-run-*"
    assert args.names is None


def test_parse_all_test_and_flags():
    args = cw.parse_args(["--all-test", "--dry-run"])
    assert args.all_test is True
    assert args.dry_run is True


def test_parse_defaults():
    args = cw.parse_args(["--all-test"])
    assert args.dry_run is False


def test_parse_requires_a_selector():
    """No selector given -> argparse errors out (exit code 2)."""
    with pytest.raises(SystemExit) as exc:
        cw.parse_args([])
    assert exc.value.code == 2


def test_parse_selectors_mutually_exclusive():
    with pytest.raises(SystemExit):
        cw.parse_args(["--names", "wf-1", "--pattern", "wf-*"])


# --------------------------------------------------------------------------
# matches_selector
# --------------------------------------------------------------------------

def test_matches_selector_names():
    args = cw.parse_args(["--names", "wf-1", "wf-2"])
    assert cw.matches_selector("wf-1", args) is True
    assert cw.matches_selector("wf-3", args) is False


def test_matches_selector_pattern():
    args = cw.parse_args(["--pattern", "contain-*-run-*"])
    assert cw.matches_selector("contain-host-run-42", args) is True
    assert cw.matches_selector("other-workflow", args) is False


def test_matches_selector_all_test():
    args = cw.parse_args(["--all-test"])
    assert cw.matches_selector("wf-a-run-1", args) is True
    assert cw.matches_selector("Test QueryEvent", args) is True   # "Test *" convention
    assert cw.matches_selector("prod-workflow", args) is False


# --------------------------------------------------------------------------
# _select_targets
# --------------------------------------------------------------------------

def test_select_targets_surfaces_missing_requested_names():
    """--names should append requested names even if not in the tenant (SKIP)."""
    args = cw.parse_args(["--names", "wf-present", "wf-missing"])
    all_names = ["wf-present", "unrelated"]
    targets = cw._select_targets(all_names, args)
    assert targets == ["wf-present", "wf-missing"]


def test_select_targets_dedups_and_preserves_order():
    args = cw.parse_args(["--all-test"])
    all_names = ["a-run-1", "a-run-1", "b-run-2", "prod"]
    assert cw._select_targets(all_names, args) == ["a-run-1", "b-run-2"]


# --------------------------------------------------------------------------
# run — list → resolve → delete against a mock client
# --------------------------------------------------------------------------

def _client_with(defs):
    """Build a MagicMock client whose search_definitions returns `defs` once."""
    client = MagicMock()
    client.search_definitions.side_effect = [
        {"body": {"resources": defs, "meta": {"pagination": {"total": len(defs)}}}},
        {"body": {"resources": []}},
    ]
    return client


def _install_client(monkeypatch, client):
    monkeypatch.setattr(cw, "get_client", lambda: client)


def test_run_deletes_matched_ids(monkeypatch):
    client = _client_with([
        {"id": "id-a", "name": "wf-a-run-1"},
        {"id": "id-b", "name": "wf-b-run-2"},
        {"id": "id-p", "name": "prod-workflow"},
    ])
    client.delete_definitions.return_value = {
        "body": {"resources": ["id-a", "id-b"], "errors": []}
    }
    _install_client(monkeypatch, client)
    assert cw.run(cw.parse_args(["--all-test"])) == 0
    # Only the two -run- workflows are deleted; prod is left alone.
    called_ids = client.delete_definitions.call_args.kwargs["ids"]
    assert set(called_ids) == {"id-a", "id-b"}


def test_run_dry_run_deletes_nothing(monkeypatch):
    client = _client_with([{"id": "id-a", "name": "wf-a-run-1"}])
    _install_client(monkeypatch, client)
    assert cw.run(cw.parse_args(["--all-test", "--dry-run"])) == 0
    client.delete_definitions.assert_not_called()


def test_run_returns_0_when_nothing_matches(monkeypatch):
    client = _client_with([{"id": "id-p", "name": "prod-workflow"}])
    _install_client(monkeypatch, client)
    assert cw.run(cw.parse_args(["--all-test"])) == 0
    client.delete_definitions.assert_not_called()


def test_run_skips_missing_named_workflow(monkeypatch):
    client = _client_with([{"id": "id-a", "name": "wf-present"}])
    client.delete_definitions.return_value = {
        "body": {"resources": ["id-a"], "errors": []}
    }
    _install_client(monkeypatch, client)
    # wf-missing has no ID -> skipped; wf-present is deleted.
    assert cw.run(cw.parse_args(["--names", "wf-present", "wf-missing"])) == 0
    called_ids = client.delete_definitions.call_args.kwargs["ids"]
    assert called_ids == ["id-a"]


def test_run_returns_1_on_delete_error(monkeypatch):
    client = _client_with([{"id": "id-a", "name": "wf-a-run-1"}])
    client.delete_definitions.return_value = {
        "body": {"resources": [], "errors": [{"message": "boom"}]}
    }
    _install_client(monkeypatch, client)
    assert cw.run(cw.parse_args(["--all-test"])) == 1


def test_run_returns_1_on_list_exception(monkeypatch):
    client = MagicMock()
    client.search_definitions.side_effect = ConnectionError("network down")
    _install_client(monkeypatch, client)
    assert cw.run(cw.parse_args(["--all-test"])) == 1


def test_run_returns_1_when_auth_missing(monkeypatch):
    monkeypatch.setattr(cw, "get_client", None)
    assert cw.run(cw.parse_args(["--all-test"])) == 1
