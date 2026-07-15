"""
Tests for skills/deployment/scripts/delete_workflow.py.

The script deletes Fusion workflow definitions by ID or exact name via the
Workflows delete API (``delete_definitions``). The FalconPy client is mocked —
no network, no credentials. Covers arg parsing, name→ID resolution, ID
de-duplication, the confirmation gate, and the delete envelope handling.
"""

from unittest.mock import MagicMock

import pytest

import delete_workflow as dw


def _client(defs=None, delete_body=None):
    """MagicMock client: search_definitions returns defs, delete returns body."""
    client = MagicMock()
    defs = defs or []
    client.search_definitions.side_effect = [
        {"body": {"resources": defs, "meta": {"pagination": {"total": len(defs)}}}},
        {"body": {"resources": []}},
    ]
    client.delete_definitions.return_value = {
        "body": delete_body or {"resources": [], "errors": []}
    }
    return client


# --------------------------------------------------------------------------
# resolve_names_to_ids
# --------------------------------------------------------------------------

def test_resolve_names_maps_and_flags_missing(monkeypatch):
    client = _client([
        {"id": "id-1", "name": "Alpha"},
        {"id": "id-2", "name": "Beta"},
    ])
    monkeypatch.setattr(dw, "get_client", lambda: client)
    id_map, missing = dw.resolve_names_to_ids(["alpha", "Gamma"])
    assert id_map == {"alpha": ["id-1"]}      # case-insensitive match
    assert missing == ["Gamma"]


# --------------------------------------------------------------------------
# _gather_ids — de-dup across --id and --name
# --------------------------------------------------------------------------

def test_gather_ids_dedups_id_and_name(monkeypatch):
    client = _client([{"id": "id-1", "name": "Alpha"}])
    monkeypatch.setattr(dw, "get_client", lambda: client)
    parsed = _parse(["--id", "id-1", "--name", "Alpha"])
    ids, missing = dw._gather_ids(parsed)
    assert ids == ["id-1"]        # id-1 from --id and from name 'Alpha' collapsed
    assert missing == []


def _parse(argv):
    """Parse args the way main() does (argparse is built inline in main)."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", action="append")
    parser.add_argument("--name", action="append")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------
# delete_definitions
# --------------------------------------------------------------------------

def test_delete_definitions_success(monkeypatch):
    client = _client(delete_body={"resources": ["id-1", "id-2"], "errors": []})
    monkeypatch.setattr(dw, "get_client", lambda: client)
    ok, msg, resources = dw.delete_definitions(["id-1", "id-2"])
    assert ok is True
    assert msg == "OK"
    assert resources == ["id-1", "id-2"]
    assert client.delete_definitions.call_args.kwargs["ids"] == ["id-1", "id-2"]


def test_delete_definitions_reports_errors(monkeypatch):
    client = _client(delete_body={"resources": [], "errors": [{"message": "nope"}]})
    monkeypatch.setattr(dw, "get_client", lambda: client)
    ok, msg, _ = dw.delete_definitions(["id-x"])
    assert ok is False
    assert "nope" in msg


def test_delete_definitions_handles_exception(monkeypatch):
    client = MagicMock()
    client.delete_definitions.side_effect = ConnectionError("down")
    monkeypatch.setattr(dw, "get_client", lambda: client)
    ok, msg, _ = dw.delete_definitions(["id-x"])
    assert ok is False
    assert "down" in msg


# --------------------------------------------------------------------------
# main — end to end (confirmation suppressed via env)
# --------------------------------------------------------------------------

def test_main_deletes_by_id_json(monkeypatch, capsys):
    client = _client(delete_body={"resources": ["id-1"], "errors": []})
    monkeypatch.setattr(dw, "get_client", lambda: client)
    monkeypatch.setattr("sys.argv", ["delete_workflow.py", "--id", "id-1", "--json"])
    with pytest.raises(SystemExit) as exc:
        dw.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "id-1" in out


def test_main_reports_actual_deleted_count_not_request_size(monkeypatch, capsys):
    # A 200 with empty resources means the IDs were already gone. The non-JSON
    # output must report 0 deleted, not the request size (2).
    client = _client(delete_body={"resources": [], "errors": []})
    monkeypatch.setattr(dw, "get_client", lambda: client)
    monkeypatch.setenv("FUSION_SKILLS_SUPPRESS_CONFIRM", "1")
    monkeypatch.setattr("sys.argv", ["delete_workflow.py", "--id", "id-1", "--id", "id-2"])
    dw.main()
    out = capsys.readouterr().out
    assert "Deleted 0 workflow definition(s)." in out
    assert "2 requested ID(s) not found" in out


def test_main_errors_without_selector(monkeypatch):
    monkeypatch.setattr("sys.argv", ["delete_workflow.py"])
    with pytest.raises(SystemExit) as exc:
        dw.main()
    assert exc.value.code == 2


def test_main_exits_1_when_nothing_resolves(monkeypatch):
    client = _client([])   # no definitions -> name resolves to nothing
    monkeypatch.setattr(dw, "get_client", lambda: client)
    monkeypatch.setattr("sys.argv", ["delete_workflow.py", "--name", "Ghost", "--json"])
    with pytest.raises(SystemExit) as exc:
        dw.main()
    assert exc.value.code == 1


def test_main_confirmation_suppressed_by_env(monkeypatch):
    client = _client(delete_body={"resources": ["id-1"], "errors": []})
    monkeypatch.setattr(dw, "get_client", lambda: client)
    monkeypatch.setenv("FUSION_SKILLS_SUPPRESS_CONFIRM", "1")
    monkeypatch.setattr("sys.argv", ["delete_workflow.py", "--id", "id-1"])
    # The env var suppresses the input() prompt; the non-JSON success path
    # returns normally (no SystemExit) after deleting. input() must never be
    # called — a real prompt in a test would hang.
    monkeypatch.setattr("builtins.input", lambda *_a: pytest.fail("prompted despite suppression"))
    dw.main()
    assert client.delete_definitions.call_args.kwargs["ids"] == ["id-1"]
