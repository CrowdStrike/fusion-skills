"""Tests for export_workflow.py — export decoding, sanitize flag, error
envelope handling, and the CLI file/stdout paths."""

from unittest.mock import MagicMock

import pytest

import export_workflow


class TestExportDefinition:
    """Test export_definition against a mocked client."""

    def test_returns_decoded_bytes(self, monkeypatch):
        """A successful export returns raw YAML bytes, decoded to str."""
        mock_client = MagicMock()
        mock_client.export_definition.return_value = b"name: Example\ntrigger: {}\n"
        monkeypatch.setattr(export_workflow, "get_client", lambda: mock_client)

        result = export_workflow.export_definition("abc123")

        assert result == "name: Example\ntrigger: {}\n"
        mock_client.export_definition.assert_called_once_with(
            id="abc123", sanitize=True
        )

    def test_sanitize_flag_passed_through(self, monkeypatch):
        """--no-sanitize surfaces as sanitize=False on the API call."""
        mock_client = MagicMock()
        mock_client.export_definition.return_value = b"name: Raw\n"
        monkeypatch.setattr(export_workflow, "get_client", lambda: mock_client)

        export_workflow.export_definition("abc123", sanitize=False)

        mock_client.export_definition.assert_called_once_with(
            id="abc123", sanitize=False
        )

    def test_dict_body_with_bytes(self, monkeypatch):
        """A dict envelope whose body is bytes is decoded."""
        mock_client = MagicMock()
        mock_client.export_definition.return_value = {
            "status_code": 200,
            "body": b"name: FromDict\n",
        }
        monkeypatch.setattr(export_workflow, "get_client", lambda: mock_client)

        assert export_workflow.export_definition("abc123") == "name: FromDict\n"

    def test_error_envelope_raises(self, monkeypatch):
        """A JSON error envelope raises RuntimeError with the status and errors."""
        mock_client = MagicMock()
        mock_client.export_definition.return_value = {
            "status_code": 404,
            "body": {"errors": [{"code": 404, "message": "not found"}]},
        }
        monkeypatch.setattr(export_workflow, "get_client", lambda: mock_client)

        with pytest.raises(RuntimeError) as exc:
            export_workflow.export_definition("missing")
        assert "404" in str(exc.value)
        assert "not found" in str(exc.value)

    def test_non_bytes_non_dict_stringified(self, monkeypatch):
        """An unexpected response type is coerced to str rather than crashing."""
        mock_client = MagicMock()
        mock_client.export_definition.return_value = "already a string"
        monkeypatch.setattr(export_workflow, "get_client", lambda: mock_client)

        assert export_workflow.export_definition("abc123") == "already a string"


class TestMainCLI:
    """Test the CLI entry point: stdout, file output, and error exit."""

    def test_stdout_output(self, monkeypatch, capsys):
        """With no --output, the YAML is printed to stdout."""
        monkeypatch.setattr(
            export_workflow, "export_definition", lambda did, sanitize=True: "name: X\n"
        )
        monkeypatch.setattr("sys.argv", ["export_workflow.py", "abc123"])

        export_workflow.main()

        assert "name: X" in capsys.readouterr().out

    def test_file_output(self, monkeypatch, capsys, tmp_path):
        """With --output, the YAML is written to the file and a note is printed."""
        monkeypatch.setattr(
            export_workflow, "export_definition", lambda did, sanitize=True: "name: Y\n"
        )
        out = tmp_path / "wf.yaml"
        monkeypatch.setattr(
            "sys.argv", ["export_workflow.py", "abc123", "-o", str(out)]
        )

        export_workflow.main()

        assert out.read_text(encoding="utf-8") == "name: Y\n"
        assert str(out) in capsys.readouterr().out

    def test_no_sanitize_note(self, monkeypatch, capsys, tmp_path):
        """--no-sanitize adds a '(raw, not sanitized)' note to the file message."""
        monkeypatch.setattr(
            export_workflow, "export_definition", lambda did, sanitize=False: "name: Z\n"
        )
        out = tmp_path / "raw.yaml"
        monkeypatch.setattr(
            "sys.argv",
            ["export_workflow.py", "abc123", "--no-sanitize", "-o", str(out)],
        )

        export_workflow.main()

        assert "not sanitized" in capsys.readouterr().out

    def test_error_exits_nonzero(self, monkeypatch, capsys):
        """A RuntimeError from export surfaces as exit code 1."""
        def _raise(did, sanitize=True):
            raise RuntimeError("Export failed (status 404): boom")

        monkeypatch.setattr(export_workflow, "export_definition", _raise)
        monkeypatch.setattr("sys.argv", ["export_workflow.py", "missing"])

        with pytest.raises(SystemExit) as exc:
            export_workflow.main()
        assert exc.value.code == 1
        assert "Export failed" in capsys.readouterr().err
