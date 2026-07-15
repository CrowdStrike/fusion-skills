"""Tests for get_lookup.py."""

import sys

import pytest

import get_lookup


class TestGetLookup:
    """Test response handling for different return types."""

    def test_handles_bytes_response(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "get_lookup_file": lambda self, **kw: b"ip,category\n10.0.0.1,c2\n"
        })()
        monkeypatch.setattr(get_lookup, "get_ngsiem_client", lambda: mock_client)
        result = get_lookup.get_lookup("test.csv")
        assert "ip,category" in result
        assert "10.0.0.1" in result

    def test_handles_string_response(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "get_lookup_file": lambda self, **kw: "ip,category\n10.0.0.1,c2\n"
        })()
        monkeypatch.setattr(get_lookup, "get_ngsiem_client", lambda: mock_client)
        result = get_lookup.get_lookup("test.csv")
        assert "ip,category" in result

    def test_handles_error_response(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "get_lookup_file": lambda self, **kw: {
                "body": {"errors": [{"message": "File not found"}]}
            }
        })()
        monkeypatch.setattr(get_lookup, "get_ngsiem_client", lambda: mock_client)
        with pytest.raises(SystemExit) as exc:
            get_lookup.get_lookup("missing.csv")
        assert exc.value.code == 1

    def test_handles_content_in_body(self, monkeypatch, fake_credentials):
        mock_client = type("Mock", (), {
            "get_lookup_file": lambda self, **kw: {
                "body": {"errors": [], "content": "a,b\n1,2\n"}
            }
        })()
        monkeypatch.setattr(get_lookup, "get_ngsiem_client", lambda: mock_client)
        result = get_lookup.get_lookup("test.csv")
        assert "a,b" in result


class TestMain:
    """Test the CLI entry point."""

    def test_main_prints_to_stdout(self, monkeypatch, fake_credentials, capsys):
        mock_client = type("Mock", (), {
            "get_lookup_file": lambda self, **kw: b"ip\n10.0.0.1\n"
        })()
        monkeypatch.setattr(get_lookup, "get_ngsiem_client", lambda: mock_client)
        monkeypatch.setattr(sys, "argv", ["get_lookup.py", "--name", "test.csv"])
        get_lookup.main()
        assert "10.0.0.1" in capsys.readouterr().out

    def test_main_saves_to_file(self, tmp_path, monkeypatch, fake_credentials, capsys):
        out_file = tmp_path / "out.csv"
        mock_client = type("Mock", (), {
            "get_lookup_file": lambda self, **kw: b"ip\n10.0.0.1\n"
        })()
        monkeypatch.setattr(get_lookup, "get_ngsiem_client", lambda: mock_client)
        monkeypatch.setattr(
            sys, "argv",
            ["get_lookup.py", "--name", "test.csv", "--output", str(out_file)],
        )
        get_lookup.main()
        assert out_file.read_text() == "ip\n10.0.0.1\n"
        assert "Saved to" in capsys.readouterr().out
