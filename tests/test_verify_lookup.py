"""Tests for verify_lookup.py."""

import json
import sys

import pytest

import verify_lookup


class TestReadProbeRow:
    """Reading a known probe value from the CSV."""

    def test_first_column_by_default(self, tmp_path):
        f = tmp_path / "l.csv"
        f.write_text("ip,category\n10.0.0.1,c2\n")
        col, val = verify_lookup.read_probe_row(str(f))
        assert col == "ip"
        assert val == "10.0.0.1"

    def test_named_column(self, tmp_path):
        f = tmp_path / "l.csv"
        f.write_text("ip,category\n10.0.0.1,c2\n")
        col, val = verify_lookup.read_probe_row(str(f), column="category")
        assert col == "category"
        assert val == "c2"

    def test_missing_column_returns_none(self, tmp_path):
        f = tmp_path / "l.csv"
        f.write_text("ip,category\n10.0.0.1,c2\n")
        col, val = verify_lookup.read_probe_row(str(f), column="nope")
        assert col is None and val is None

    def test_no_data_rows_returns_none(self, tmp_path):
        f = tmp_path / "l.csv"
        f.write_text("ip,category\n")
        col, val = verify_lookup.read_probe_row(str(f))
        assert col is None and val is None


class TestBuildMatchQuery:
    """The CQL match() query is well-formed."""

    def test_query_contains_match_and_value(self):
        q = verify_lookup.build_match_query("blocklist.csv", "ip", "1.2.3.4")
        assert 'match(file="blocklist.csv"' in q
        assert "column=ip" in q
        assert "field=ip" in q
        assert "strict=true" in q
        assert "1.2.3.4" in q
        # The synthesized event is valid JSON embedded in the CQL literal.
        assert "createEvents(" in q and "parseJson()" in q


class _FakeClient:
    """Minimal NGSIEM client stub: one match row, done immediately."""

    def __init__(self, events, done=True, start_status=200, job_id="P1-abc"):
        self._events = events
        self._done = done
        self._start_status = start_status
        self._job_id = job_id
        self.started = None

    def start_search(self, **kw):
        self.started = kw
        resources = {"id": self._job_id} if self._job_id else {}
        return {"status_code": self._start_status, "resources": resources}

    def get_search_status(self, **_kw):
        return {"status_code": 200, "resources": {"done": self._done, "events": self._events}}


class TestRunMatchQuery:
    """Polling and result extraction."""

    def test_returns_events_when_matched(self):
        client = _FakeClient(events=[{"ip": "1.2.3.4"}])
        ok, events, _msg = verify_lookup.run_match_query(client, "q", timeout=5)
        assert ok is True
        assert events == [{"ip": "1.2.3.4"}]

    def test_empty_events_still_completes(self):
        client = _FakeClient(events=[])
        ok, events, _msg = verify_lookup.run_match_query(client, "q", timeout=5)
        assert ok is True
        assert events == []

    def test_start_failure_reported(self):
        client = _FakeClient(events=[], start_status=403)
        ok, _events, msg = verify_lookup.run_match_query(client, "q", timeout=5)
        assert ok is False
        assert "start_search failed" in msg

    def test_no_job_id_reported(self):
        client = _FakeClient(events=[], job_id=None)
        ok, _events, msg = verify_lookup.run_match_query(client, "q", timeout=5)
        assert ok is False
        assert "no job id" in msg

    def test_timeout_when_never_done(self):
        # done=False plus timeout=0 means the poll loop never runs to completion.
        client = _FakeClient(events=[], done=False)
        ok, _events, msg = verify_lookup.run_match_query(client, "q", timeout=0)
        assert ok is False
        assert "did not complete" in msg


class TestVerifyLookup:
    """End-to-end orchestration with create/delete/search mocked."""

    def test_success_matched_row(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "l.csv"
        f.write_text("ip,category\n1.2.3.4,c2\n")
        monkeypatch.setattr(verify_lookup, "create_lookup", lambda *a, **k: (True, "ok"))
        deleted = {}
        monkeypatch.setattr(verify_lookup, "delete_lookup",
                            lambda name, **k: deleted.update({"name": name}) or (True, "deleted"))
        monkeypatch.setattr(verify_lookup, "get_ngsiem_client",
                            lambda: _FakeClient(events=[{"ip": "1.2.3.4"}]))
        ok, msg = verify_lookup.verify_lookup(str(f), filename="l.csv", column="ip")
        assert ok is True
        assert "Verified" in msg
        # Probe file is cleaned up by default.
        assert deleted["name"] == "l.csv"

    def test_upload_failure_short_circuits(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "l.csv"
        f.write_text("ip\n1.2.3.4\n")
        monkeypatch.setattr(verify_lookup, "create_lookup", lambda *a, **k: (False, "boom"))
        ok, msg = verify_lookup.verify_lookup(str(f), filename="l.csv")
        assert ok is False
        assert "Upload failed" in msg

    def test_no_match_is_failure(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "l.csv"
        f.write_text("ip\n1.2.3.4\n")
        monkeypatch.setattr(verify_lookup, "create_lookup", lambda *a, **k: (True, "ok"))
        monkeypatch.setattr(verify_lookup, "delete_lookup", lambda name, **k: (True, "deleted"))
        monkeypatch.setattr(verify_lookup, "get_ngsiem_client",
                            lambda: _FakeClient(events=[]))
        ok, msg = verify_lookup.verify_lookup(str(f), filename="l.csv")
        assert ok is False
        assert "no rows" in msg

    def test_keep_skips_delete(self, tmp_path, monkeypatch, fake_credentials):
        f = tmp_path / "l.csv"
        f.write_text("ip\n1.2.3.4\n")
        monkeypatch.setattr(verify_lookup, "create_lookup", lambda *a, **k: (True, "ok"))
        calls = {"deleted": False}
        monkeypatch.setattr(verify_lookup, "delete_lookup",
                            lambda name, **k: calls.update({"deleted": True}) or (True, "d"))
        monkeypatch.setattr(verify_lookup, "get_ngsiem_client",
                            lambda: _FakeClient(events=[{"ip": "1.2.3.4"}]))
        ok, _msg = verify_lookup.verify_lookup(str(f), filename="l.csv", keep=True)
        assert ok is True
        assert calls["deleted"] is False


class TestMain:
    """CLI entry point."""

    def test_main_missing_file_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["verify_lookup.py", "--file", "/nope/x.csv", "--json"])
        with pytest.raises(SystemExit) as exc:
            verify_lookup.main()
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
        assert "not found" in out["error"].lower()

    def test_main_success_json(self, tmp_path, monkeypatch, fake_credentials, capsys):
        f = tmp_path / "data.csv"
        f.write_text("ip\n1.2.3.4\n")
        monkeypatch.setattr(verify_lookup, "create_lookup", lambda *a, **k: (True, "ok"))
        monkeypatch.setattr(verify_lookup, "delete_lookup", lambda name, **k: (True, "d"))
        monkeypatch.setattr(verify_lookup, "get_ngsiem_client",
                            lambda: _FakeClient(events=[{"ip": "1.2.3.4"}]))
        monkeypatch.setattr(sys, "argv", ["verify_lookup.py", "--file", str(f), "--json"])
        verify_lookup.main()
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["filename"] == "data.csv"

    def test_main_failure_exits(self, tmp_path, monkeypatch, fake_credentials, capsys):
        f = tmp_path / "data.csv"
        f.write_text("ip\n1.2.3.4\n")
        monkeypatch.setattr(verify_lookup, "create_lookup", lambda *a, **k: (True, "ok"))
        monkeypatch.setattr(verify_lookup, "delete_lookup", lambda name, **k: (True, "d"))
        monkeypatch.setattr(verify_lookup, "get_ngsiem_client",
                            lambda: _FakeClient(events=[]))  # no match -> failure
        monkeypatch.setattr(sys, "argv", ["verify_lookup.py", "--file", str(f), "--json"])
        with pytest.raises(SystemExit) as exc:
            verify_lookup.main()
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
