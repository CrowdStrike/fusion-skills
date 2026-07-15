"""Tests for action_search.py — formatting, caching, search, and API handling.

All API calls are mocked; no CrowdStrike credentials are needed.
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

import action_search


def _mock_client(monkeypatch, resources, total=None, status_code=200, errors=None):
    """Patch action_search.get_client to return a client whose
    search_activities yields *resources* on the first page and an empty page
    afterwards (so pagination loops terminate)."""
    if total is None:
        total = len(resources)
    client = MagicMock()
    calls = {"n": 0}

    def _search_activities(*_args, **_kwargs):
        calls["n"] += 1
        page = resources if calls["n"] == 1 else []
        return {
            "status_code": status_code,
            "body": {
                "resources": page,
                "errors": errors or [],
                "meta": {"pagination": {"total": total}},
            },
        }

    client.search_activities.side_effect = _search_activities
    monkeypatch.setattr(action_search, "get_client", lambda: client)
    return client


class TestFormatActionSummary:
    """Test action summary formatting."""

    def test_basic_format(self):
        action = {
            "id": "abc123",
            "name": "Contain Host",
            "description": "Contain a device",
            "category": "action",
            "vendor": "CrowdStrike",
        }
        output = action_search.format_action_summary(action)
        assert "Contain Host" in output
        assert "abc123" in output
        assert "Contain a device" in output

    def test_non_crowdstrike_vendor_shown(self):
        action = {"id": "x", "name": "Test", "vendor": "Okta"}
        output = action_search.format_action_summary(action)
        assert "[Okta]" in output

    def test_crowdstrike_vendor_hidden(self):
        action = {"id": "x", "name": "Test", "vendor": "CrowdStrike"}
        output = action_search.format_action_summary(action)
        assert "[CrowdStrike]" not in output


class TestFormatActionDetails:
    """Test action detail formatting."""

    def test_includes_input_fields(self):
        action = {
            "id": "abc123",
            "name": "Test Action",
            "category": "action",
            "description": "Does stuff",
            "vendor": "CrowdStrike",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "The device ID",
                    "required": True,
                },
            },
        }
        output = action_search.format_action_details(action)
        assert "device_id" in output
        assert "(required)" in output

    def test_plugin_action_flagged(self):
        action = {
            "id": "x",
            "name": "Okta Action",
            "vendor": "Okta",
            "namespace": "plugin:okta",
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert "config_id" in output

    def test_class_based_action_with_semantic_version(self):
        action = {
            "id": "x",
            "name": "CreateVariable",
            "vendor": "CrowdStrike",
            "class": "generic",
            "semantic_version": "1.2.3",
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert "version_constraint" in output
        assert "~1" in output

    def test_class_based_action_without_semantic_version(self):
        action = {
            "id": "x",
            "name": "CreateVariable",
            "vendor": "CrowdStrike",
            "class": "generic",
            "semantic_version": None,
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert "~0" in output

    def test_missing_permission_flagged(self):
        action = {
            "id": "x",
            "name": "Locked Action",
            "vendor": "Okta",
            "has_permission": False,
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert "NOT AVAILABLE" in output

    def test_non_class_action_shows_version_constraint(self):
        # Bug fix: non-class actions (Send email, Charlotte AI, VirusTotal,
        # DomainTools) must still print a version_constraint line.
        action = {
            "id": "x",
            "name": "Send email",
            "vendor": "CrowdStrike",
            "semantic_version": "1.0.4",
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert "version_constraint : ~1" in output
        assert "Class" not in output  # no class -> no Class line

    def test_non_class_action_without_semantic_version(self):
        # A non-class action with no semantic_version -> ~0.
        action = {
            "id": "x",
            "name": "VirusTotal Lookup",
            "vendor": "VirusTotal",
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert "version_constraint : ~0" in output
        assert "Class" not in output

    @pytest.mark.parametrize(
        "semantic_version, expected",
        [
            ("0.0.14", "~0"),
            ("1.0.4", "~1"),
            ("2.3.0", "~2"),
            (None, "~0"),
        ],
    )
    def test_version_constraint_major_derivation(self, semantic_version, expected):
        # version_constraint is the MAJOR component of semantic_version
        # (or ~0 when absent), not ~1 for any non-empty version.
        action = {
            "id": "x",
            "name": "A",
            "vendor": "CrowdStrike",
            "semantic_version": semantic_version,
            "properties": {},
        }
        output = action_search.format_action_details(action)
        assert f"version_constraint : {expected}" in output


class TestCache:
    """Test local action cache."""

    def test_save_and_load(self, tmp_path, monkeypatch):
        cache_file = str(tmp_path / ".action_cache.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)

        resources = [{"id": "a", "name": "Action A"}]
        action_search._save_cache(resources)
        loaded = action_search._load_cache()
        assert loaded == resources

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        # Freshness is mtime-based, so age the file past the TTL.
        cache_file = str(tmp_path / ".action_cache.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"ts": 0, "resources": [{"id": "old"}]}, f)
        old = time.time() - (action_search._CACHE_TTL + 100)
        os.utime(cache_file, (old, old))

        assert action_search._load_cache() is None

    def test_clear_cache(self, tmp_path, monkeypatch):
        cache_file = str(tmp_path / ".action_cache.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)

        with open(cache_file, "w", encoding="utf-8") as f:
            f.write("{}")

        assert action_search._clear_cache() is True
        assert not os.path.exists(cache_file)

    def test_clear_nonexistent_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / "nope.json"))
        assert action_search._clear_cache() is False


class TestFormatVendorsTable:
    """Test vendor table formatting."""

    def test_formats_vendors(self):
        vendors = {
            "CrowdStrike": {
                "count": 50,
                "use_cases": {"Detection", "Response"},
                "has_permission": True,
            },
            "Okta": {"count": 10, "use_cases": {"Identity"}, "has_permission": False},
        }
        output = action_search.format_vendors_table(vendors)
        assert "CrowdStrike" in output
        assert "Okta" in output
        assert "60 actions" in output
        assert "2 vendors" in output


class TestFqlHelpers:
    """Test the server-side FQL search primitives."""

    def test_fql_quote_escapes_quotes_and_backslashes(self):
        assert action_search._fql_quote("O'Brien") == "O\\'Brien"
        assert action_search._fql_quote("a\\b") == "a\\\\b"

    def test_fql_search_returns_resources(self, monkeypatch):
        resources = [{"id": "1", "name": "Contain Host"}]
        _mock_client(monkeypatch, resources)
        results = action_search._fql_search("Contain")
        assert results == resources

    def test_fql_search_error_returns_none(self, monkeypatch):
        _mock_client(monkeypatch, [], status_code=500)
        assert action_search._fql_search("Contain") is None

    def test_fql_search_empty_returns_empty_list(self, monkeypatch):
        _mock_client(monkeypatch, [])
        assert action_search._fql_search("nomatch") == []

    def test_fql_search_partial_404_returns_resources(self, monkeypatch):
        """A 404 that still carries resources is a partial failure, not fatal.

        The search_activities API aggregates per-item 'artifact not found'
        errors (from orphaned catalog entries) into a top-level 404 while still
        returning every action it *could* resolve. Discarding those valid
        resources over the aggregate status was the bug that made common
        searches (email, query event, LLM) look like connection failures.
        """
        resources = [{"id": "1", "name": "Send Email"}, {"id": "2", "name": "Query Email"}]
        errors = [{"code": 404, "message": "artifact not found", "id": "orphaned"}]
        _mock_client(monkeypatch, resources, status_code=404, errors=errors)
        assert action_search._fql_search("email") == resources

    def test_fql_search_404_without_resources_returns_none(self, monkeypatch):
        """A 404 with no resources is a real failure — caller should retry/fallback."""
        _mock_client(monkeypatch, [], status_code=404)
        assert action_search._fql_search("nomatch") is None


class TestSearchActions:
    """Test the high-level search entry point."""

    def test_search_returns_fql_results(self, monkeypatch):
        resources = [{"id": "1", "name": "Contain Host", "vendor": "CrowdStrike"}]
        _mock_client(monkeypatch, resources)
        results = action_search.search_actions("Contain")
        assert results == resources

    def test_search_empty_falls_through_to_empty(self, tmp_path, monkeypatch):
        # FQL returns [] and the client-side fallback also finds nothing.
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        _mock_client(monkeypatch, [])
        results = action_search.search_actions("zzznomatch")
        assert results == []


class TestGetActionDetails:
    """Test single-action lookup."""

    def test_found(self, monkeypatch):
        action = {"id": "abc", "name": "Contain Host"}
        _mock_client(monkeypatch, [action])
        assert action_search.get_action_details("abc") == action

    def test_not_found(self, monkeypatch):
        _mock_client(monkeypatch, [])
        assert action_search.get_action_details("missing") is None


class TestListActions:
    """Test paginated listing."""

    def test_returns_resources_and_total(self, monkeypatch):
        resources = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
        _mock_client(monkeypatch, resources, total=42)
        items, total = action_search.list_actions(limit=25, offset=0)
        assert items == resources
        assert total == 42


class TestSearchByVendor:
    """Test vendor filtering via FQL."""

    def test_returns_vendor_actions(self, monkeypatch):
        resources = [{"id": "1", "name": "Okta Suspend", "vendor": "Okta"}]
        _mock_client(monkeypatch, resources)
        results = action_search.search_by_vendor("Okta")
        assert results == resources


def _mock_client_raising(monkeypatch, exc):
    """Patch get_client to a client whose search_activities raises *exc*."""
    client = MagicMock()
    client.search_activities.side_effect = exc
    monkeypatch.setattr(action_search, "get_client", lambda: client)
    return client


class TestFqlSearchErrorPaths:
    """Cover the FQL exception and pagination branches."""

    def test_connection_error_returns_none(self, monkeypatch):
        _mock_client_raising(monkeypatch, ConnectionError("boom"))
        assert action_search._fql_search("Contain") is None

    def test_runtime_error_returns_none(self, monkeypatch):
        _mock_client_raising(monkeypatch, RuntimeError("boom"))
        assert action_search._fql_search("Contain") is None

    def test_paginates_across_multiple_pages(self, monkeypatch):
        # Two full pages then an empty page; total drives the loop.
        pages = [
            [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}],
            [{"id": "3", "name": "C"}],
        ]
        client = MagicMock()
        state = {"n": 0}

        def _search(*_args, **_kwargs):
            i = state["n"]
            state["n"] += 1
            page = pages[i] if i < len(pages) else []
            return {
                "status_code": 200,
                "body": {"resources": page, "meta": {"pagination": {"total": 3}}},
            }

        client.search_activities.side_effect = _search
        monkeypatch.setattr(action_search, "get_client", lambda: client)
        results = action_search._fql_search("x")
        assert [r["id"] for r in results] == ["1", "2", "3"]

    def test_fql_vendor_delegates(self, monkeypatch):
        resources = [{"id": "1", "name": "A", "vendor": "Okta"}]
        _mock_client(monkeypatch, resources)
        assert action_search._fql_vendor("Okta") == resources


class TestCacheAge:
    """Cover cache age and load edge cases."""

    def test_age_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / "nope.json"))
        assert action_search._cache_age_seconds() is None

    def test_age_oserror_returns_none(self, tmp_path, monkeypatch):
        cache_file = str(tmp_path / ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        monkeypatch.setattr(action_search.os.path, "isfile", lambda _p: True)

        def _boom(_p):
            raise OSError("stat failed")

        monkeypatch.setattr(action_search.os.path, "getmtime", _boom)
        assert action_search._cache_age_seconds() is None

    def test_expired_cache_prints_refresh_notice(self, tmp_path, monkeypatch, capsys):
        cache_file = str(tmp_path / ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"ts": 0, "resources": [{"id": "old"}]}, f)
        old = time.time() - (action_search._CACHE_TTL + 100)
        os.utime(cache_file, (old, old))

        assert action_search._load_cache(progress=True) is None
        assert "auto-refreshing" in capsys.readouterr().out

    def test_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        cache_file = str(tmp_path / ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert action_search._load_cache() is None

    def test_cache_missing_key_returns_none(self, tmp_path, monkeypatch):
        cache_file = str(tmp_path / ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"ts": 0}, f)  # no "resources"
        assert action_search._load_cache() is None

    def test_save_cache_swallows_oserror(self, tmp_path, monkeypatch):
        # Point at a directory that cannot be opened as a file.
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path))
        # Should not raise.
        action_search._save_cache([{"id": "a"}])


class TestFetchPageWithRetry:
    """Cover the retry logic in _fetch_page_with_retry."""

    def test_success_first_try(self, monkeypatch):
        client = MagicMock()
        client.search_activities.return_value = {
            "status_code": 200,
            "body": {"resources": [{"id": "1"}]},
        }
        body = action_search._fetch_page_with_retry(client, 0)
        assert body == {"resources": [{"id": "1"}]}

    def test_retries_then_succeeds(self, monkeypatch, capsys):
        monkeypatch.setattr(action_search.time, "sleep", lambda _s: None)
        client = MagicMock()
        calls = {"n": 0}

        def _search(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient")
            return {"status_code": 200, "body": {"resources": []}}

        client.search_activities.side_effect = _search
        body = action_search._fetch_page_with_retry(client, 0, progress=True)
        assert body == {"resources": []}
        assert "retrying (1/3)" in capsys.readouterr().out

    def test_non_200_raises_and_eventually_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(action_search.time, "sleep", lambda _s: None)
        client = MagicMock()
        client.search_activities.return_value = {"status_code": 500, "body": {}}
        body = action_search._fetch_page_with_retry(
            client, 100, max_retries=2, progress=True
        )
        assert body is None
        assert "Failed after 2 retries" in capsys.readouterr().out


class TestPaginateAll:
    """Cover the full-catalog pagination and caching."""

    def test_uses_cache_when_fresh(self, tmp_path, monkeypatch, capsys):
        cache_file = str(tmp_path / ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        action_search._save_cache([{"id": "cached", "name": "X"}])
        result = action_search._paginate_all(progress=True)
        assert result == [{"id": "cached", "name": "X"}]
        assert "Using cached catalog" in capsys.readouterr().out

    def test_fetches_and_saves_when_no_cache(self, tmp_path, monkeypatch, capsys):
        cache_file = str(tmp_path / ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        resources = [{"id": "1", "name": "A"}]
        _mock_client(monkeypatch, resources, total=1)
        result = action_search._paginate_all(progress=True)
        assert result == resources
        # Verify it was persisted.
        assert action_search._load_cache() == resources

    def test_incomplete_scan_not_cached(self, tmp_path, monkeypatch, capsys):
        cache_file = str(tmp_path/ ".c.json")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        monkeypatch.setattr(action_search.time, "sleep", lambda _s: None)
        # First page returns 2 of 10, then all subsequent fetches fail.
        client = MagicMock()
        state = {"n": 0}

        def _search(*_args, **_kwargs):
            state["n"] += 1
            if state["n"] == 1:
                return {
                    "status_code": 200,
                    "body": {
                        "resources": [{"id": "1"}, {"id": "2"}],
                        "meta": {"pagination": {"total": 10}},
                    },
                }
            raise ConnectionError("dropped")

        client.search_activities.side_effect = _search
        monkeypatch.setattr(action_search, "get_client", lambda: client)
        result = action_search._paginate_all(progress=True)
        assert len(result) == 2  # returned for immediate use
        assert action_search._load_cache() is None  # NOT persisted
        assert "scan incomplete" in capsys.readouterr().out


class TestListVendors:
    """Cover the vendor aggregation."""

    def test_aggregates_by_vendor(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        resources = [
            {"id": "1", "name": "A", "vendor": "CrowdStrike",
             "use_cases": ["Detection"], "has_permission": True},
            {"id": "2", "name": "B", "vendor": "CrowdStrike",
             "use_cases": ["Response"], "has_permission": True},
            {"id": "3", "name": "C", "vendor": "Okta",
             "use_cases": ["Identity"], "has_permission": False},
            {"id": "4", "name": "D"},  # no vendor -> Unknown
        ]
        _mock_client(monkeypatch, resources, total=4)
        vendors = action_search.list_vendors()
        assert vendors["CrowdStrike"]["count"] == 2
        assert vendors["CrowdStrike"]["use_cases"] == {"Detection", "Response"}
        assert vendors["Okta"]["has_permission"] is False
        assert "Unknown" in vendors


class TestClientSideSearch:
    """Cover the client-side substring scan and multi-word search path."""

    def test_client_side_matches_substring(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        resources = [
            {"id": "1", "name": "Contain Host", "vendor": "CrowdStrike"},
            {"id": "2", "name": "Send Email", "vendor": "CrowdStrike"},
        ]
        _mock_client(monkeypatch, resources, total=2)
        results = action_search._client_side_search("email")
        assert [r["id"] for r in results] == ["2"]

    def test_client_side_vendor_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        resources = [
            {"id": "1", "name": "Suspend User", "vendor": "Okta"},
            {"id": "2", "name": "Suspend Device", "vendor": "CrowdStrike"},
        ]
        _mock_client(monkeypatch, resources, total=2)
        results = action_search._client_side_search("suspend", vendor_filter="Okta")
        assert [r["id"] for r in results] == ["1"]

    def test_multi_word_narrows_via_longest_word(self, monkeypatch):
        # FQL returns 0 for the full multi-word query, then a set for the
        # longest word which we filter client-side.
        client = MagicMock()
        state = {"n": 0}

        def _search(*_args, **kwargs):
            state["n"] += 1
            fql = kwargs.get("filter", "")
            # First call: full query "detection details" -> 0 results.
            if state["n"] == 1:
                return {"status_code": 200,
                        "body": {"resources": [], "meta": {"pagination": {"total": 0}}}}
            # Second call: longest word "detection" -> a small set.
            if "detection" in fql:
                page = [
                    {"id": "1", "name": "Detection Details"},
                    {"id": "2", "name": "Detection Summary"},
                ]
                return {"status_code": 200,
                        "body": {"resources": page,
                                 "meta": {"pagination": {"total": 2}}}}
            return {"status_code": 200,
                    "body": {"resources": [], "meta": {"pagination": {"total": 0}}}}

        client.search_activities.side_effect = _search
        monkeypatch.setattr(action_search, "get_client", lambda: client)
        results = action_search.search_actions("detection details")
        assert [r["id"] for r in results] == ["1"]


class TestSearchByVendorFallback:
    """Cover the client-side fallback when FQL returns None."""

    def test_falls_back_to_client_scan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        monkeypatch.setattr(action_search.time, "sleep", lambda _s: None)
        # FQL path fails (non-200), then _paginate_all scans the catalog.
        client = MagicMock()
        state = {"n": 0}

        def _search(*_args, **_kwargs):
            state["n"] += 1
            if state["n"] == 1:
                return {"status_code": 500, "body": {}}  # FQL fails -> None
            page = ([{"id": "1", "name": "A", "vendor": "Okta"},
                     {"id": "2", "name": "B", "vendor": "CrowdStrike"}]
                    if state["n"] == 2 else [])
            return {"status_code": 200,
                    "body": {"resources": page,
                             "meta": {"pagination": {"total": 2}}}}

        client.search_activities.side_effect = _search
        monkeypatch.setattr(action_search, "get_client", lambda: client)
        results = action_search.search_by_vendor("Okta")
        assert [r["id"] for r in results] == ["1"]


class TestSearchByUseCase:
    """Cover use-case filtering."""

    def test_matches_use_case_substring(self, tmp_path, monkeypatch):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        resources = [
            {"id": "1", "name": "A", "use_cases": ["Identity Management"]},
            {"id": "2", "name": "B", "use_cases": ["Detection"]},
        ]
        _mock_client(monkeypatch, resources, total=2)
        results = action_search.search_by_use_case("identity")
        assert [r["id"] for r in results] == ["1"]


class TestListActionsVendorFilter:
    """Cover the vendor-filtered listing branch."""

    def test_vendor_filter_paginates(self, monkeypatch):
        resources = [{"id": str(i), "name": f"A{i}", "vendor": "Okta"}
                     for i in range(5)]
        _mock_client(monkeypatch, resources)
        page, total = action_search.list_actions(limit=2, offset=1,
                                                  vendor_filter="Okta")
        assert total == 5
        assert [r["id"] for r in page] == ["1", "2"]


class TestFormatDetailsExtra:
    """Cover remaining format_action_details branches."""

    def test_use_cases_line(self):
        action = {"id": "x", "name": "A", "vendor": "CrowdStrike",
                  "use_cases": ["Detection", "Response"], "properties": {}}
        output = action_search.format_action_details(action)
        assert "Use cases" in output
        assert "Detection, Response" in output

    def test_class_without_semantic_version_field(self):
        # "class" present but no "semantic_version" key at all -> ~0.
        action = {"id": "x", "name": "A", "vendor": "CrowdStrike",
                  "class": "generic", "properties": {}}
        output = action_search.format_action_details(action)
        assert "version_constraint : ~0" in output

    def test_property_description_shown(self):
        action = {
            "id": "x", "name": "A", "vendor": "CrowdStrike",
            "properties": {
                "field1": {"type": "string", "description": "a field", "required": False},
            },
        }
        output = action_search.format_action_details(action)
        assert "field1" in output
        assert "a field" in output


class TestPrintHelpers:
    """Cover the print/output helpers."""

    def test_print_results_json(self, capsys):
        action_search._print_results([{"id": "1"}], "'x'", as_json=True)
        out = capsys.readouterr().out
        assert json.loads(out) == [{"id": "1"}]

    def test_print_results_empty(self, capsys):
        action_search._print_results([], "'x'")
        out = capsys.readouterr().out
        assert "No actions matching" in out
        # Zero results should offer a helpful next step.
        assert "shorter or broader" in out

    def test_print_results_summary(self, capsys):
        action_search._print_results(
            [{"id": "1", "name": "Contain", "vendor": "CrowdStrike"}], "'contain'"
        )
        out = capsys.readouterr().out
        assert "Found 1 action(s)" in out
        assert "Contain" in out

    def test_print_paginated_json(self, capsys):
        action_search._print_paginated([{"id": "1"}], 1, 0, as_json=True)
        assert json.loads(capsys.readouterr().out) == {
            "resources": [{"id": "1"}], "total": 1}

    def test_print_paginated_more_hint(self, capsys):
        items = [{"id": "1", "name": "A", "vendor": "CrowdStrike"}]
        action_search._print_paginated(items, total=10, offset=0)
        out = capsys.readouterr().out
        assert "showing 1 of 10" in out
        assert "--offset 1" in out


def _run_main(monkeypatch, argv):
    """Run action_search.main() with the given argv (excluding prog name)."""
    monkeypatch.setattr(sys, "argv", ["action_search.py"] + argv)
    action_search.main()


class TestMain:
    """Cover the CLI entry point and all handlers."""

    def test_no_args_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["action_search.py"])
        with pytest.raises(SystemExit):
            action_search.main()
        assert "is required" in capsys.readouterr().err

    def test_clear_cache_present(self, tmp_path, monkeypatch, capsys):
        cache_file = str(tmp_path / ".c.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write("{}")
        monkeypatch.setattr(action_search, "_CACHE_FILE", cache_file)
        _run_main(monkeypatch, ["--clear-cache"])
        assert "Cache cleared." in capsys.readouterr().out

    def test_clear_cache_absent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / "nope.json"))
        _run_main(monkeypatch, ["--clear-cache"])
        assert "No cache file found." in capsys.readouterr().out

    def test_search(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [{"id": "1", "name": "Contain Host",
                                    "vendor": "CrowdStrike"}])
        _run_main(monkeypatch, ["--search", "Contain"])
        assert "Contain Host" in capsys.readouterr().out

    def test_search_with_use_case_filter(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [
            {"id": "1", "name": "Contain Host", "vendor": "CrowdStrike",
             "use_cases": ["Response"]},
        ])
        _run_main(monkeypatch, ["--search", "Contain", "--use-case", "response"])
        assert "Contain Host" in capsys.readouterr().out

    def test_details_json(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [{"id": "abc", "name": "A"}])
        _run_main(monkeypatch, ["--details", "abc", "--json"])
        assert json.loads(capsys.readouterr().out)["id"] == "abc"

    def test_details_found(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [{"id": "abc", "name": "Contain",
                                    "vendor": "CrowdStrike", "properties": {}}])
        _run_main(monkeypatch, ["--details", "abc"])
        assert "Action details" in capsys.readouterr().out

    def test_details_not_found_exits(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [])
        monkeypatch.setattr(sys, "argv", ["action_search.py", "--details", "missing"])
        with pytest.raises(SystemExit) as exc:
            action_search.main()
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().out

    def test_vendors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        _mock_client(monkeypatch, [
            {"id": "1", "name": "A", "vendor": "CrowdStrike",
             "use_cases": ["Detection"]},
        ], total=1)
        _run_main(monkeypatch, ["--vendors"])
        assert "Available integrations" in capsys.readouterr().out

    def test_vendors_json_with_use_case_filter(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        _mock_client(monkeypatch, [
            {"id": "1", "name": "A", "vendor": "Okta", "use_cases": ["Identity"]},
            {"id": "2", "name": "B", "vendor": "CrowdStrike",
             "use_cases": ["Detection"]},
        ], total=2)
        _run_main(monkeypatch, ["--vendors", "--use-case", "identity", "--json"])
        raw = capsys.readouterr().out
        # list_vendors prints scan-progress lines before the JSON payload.
        out = json.loads(raw[raw.index("{"):])
        assert "Okta" in out
        assert "CrowdStrike" not in out

    def test_list(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [{"id": "1", "name": "A", "vendor": "CrowdStrike"}],
                     total=1)
        _run_main(monkeypatch, ["--list"])
        assert "Actions (showing" in capsys.readouterr().out

    def test_list_with_use_case(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        _mock_client(monkeypatch, [
            {"id": "1", "name": "A", "vendor": "Okta", "use_cases": ["Identity"]},
        ], total=1)
        _run_main(monkeypatch, ["--list", "--use-case", "identity", "--vendor", "Okta"])
        assert "showing" in capsys.readouterr().out

    def test_standalone_use_case(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        _mock_client(monkeypatch, [
            {"id": "1", "name": "A", "vendor": "Okta", "use_cases": ["Identity"]},
        ], total=1)
        _run_main(monkeypatch, ["--use-case", "identity"])
        assert "use case 'identity'" in capsys.readouterr().out

    def test_standalone_use_case_vendor_filter(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(action_search, "_CACHE_FILE", str(tmp_path / ".c.json"))
        _mock_client(monkeypatch, [
            {"id": "1", "name": "A", "vendor": "Okta", "use_cases": ["Identity"]},
            {"id": "2", "name": "B", "vendor": "CrowdStrike",
             "use_cases": ["Identity"]},
        ], total=2)
        _run_main(monkeypatch, ["--use-case", "identity", "--vendor", "Okta"])
        out = capsys.readouterr().out
        assert "Found 1 action(s)" in out

    def test_standalone_vendor(self, monkeypatch, capsys):
        _mock_client(monkeypatch, [
            {"id": "1", "name": "Okta Suspend", "vendor": "Okta"},
        ])
        _run_main(monkeypatch, ["--vendor", "Okta"])
        assert "vendor 'Okta'" in capsys.readouterr().out
