from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from docling.datamodel.base_models import InputFormat

from aether_mcp_server.tools import (
    DocumentProcessingResult,
    _is_internal_url,
    current_time,
    echo,
    process_document,
)


def test_echo_returns_the_supplied_message() -> None:
    assert echo("Hello MCP").message == "Hello MCP"


def test_current_time_returns_a_utc_iso_timestamp() -> None:
    value = current_time().timestamp
    parsed = datetime.fromisoformat(value)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


class TestProcessDocument:
    def test_configures_image_pipeline(self) -> None:
        with patch("aether_mcp_server.tools.DocumentConverter") as mock_cls:
            mock_cls.return_value.convert.return_value.document.export_to_markdown.return_value = "# Title"
            mock_cls.return_value.convert.return_value.document.pages = [MagicMock()]
            process_document(source="image.png", ocr=True)

        format_options = mock_cls.call_args.kwargs["format_options"]
        assert InputFormat.IMAGE in format_options

    def test_identifies_internal_addresses(self) -> None:
        assert _is_internal_url("http://10.0.0.1/report.pdf")
        assert _is_internal_url("http://127.0.0.1/report.pdf")
        assert not _is_internal_url("https://8.8.8.8/report.pdf")

    def test_public_url_is_passed_directly_to_docling(self) -> None:
        source = "https://example.com/doc.pdf"
        with (
            patch("aether_mcp_server.tools.DocumentConverter") as mock_cls,
            patch("aether_mcp_server.tools._is_internal_url", return_value=False),
        ):
            mock_cls.return_value.convert.return_value.document.export_to_markdown.return_value = "# Title"
            mock_cls.return_value.convert.return_value.document.pages = [MagicMock()]
            process_document(source=source)

        mock_cls.return_value.convert.assert_called_once_with(source)

    def test_internal_url_is_downloaded_and_temp_file_is_removed(self, tmp_path: Path) -> None:
        temporary_file = tmp_path / "document.pdf"
        temporary_file.write_bytes(b"PDF")
        with (
            patch("aether_mcp_server.tools.DocumentConverter") as mock_cls,
            patch("aether_mcp_server.tools._is_internal_url", return_value=True),
            patch("aether_mcp_server.tools._download_to_temp", return_value=temporary_file),
        ):
            mock_cls.return_value.convert.return_value.document.export_to_markdown.return_value = "# Title"
            mock_cls.return_value.convert.return_value.document.pages = [MagicMock()]
            process_document(source="http://10.0.0.1/document.pdf")

        mock_cls.return_value.convert.assert_called_once_with(temporary_file)
        assert not temporary_file.exists()

    def test_returns_markdown_by_default(self) -> None:
        with (
            patch("aether_mcp_server.tools.DocumentConverter") as mock_cls,
        ):
            mock_cls.return_value.convert.return_value.document.export_to_markdown.return_value = (
                "# Title\n\nHello world."
            )
            mock_cls.return_value.convert.return_value.document.pages = [MagicMock()]
            result = process_document(source="test.pdf")

        assert isinstance(result, DocumentProcessingResult)
        assert result.markdown == "# Title\n\nHello world."
        assert result.json_data is None

    def test_returns_json(self) -> None:
        doc_model = {"content": "Hello", "pages": [{"page": 1}]}
        with (
            patch("aether_mcp_server.tools.DocumentConverter") as mock_cls,
        ):
            mock_cls.return_value.convert.return_value.document.model_dump.return_value = (
                doc_model
            )
            mock_cls.return_value.convert.return_value.document.pages = [MagicMock()]
            result = process_document(
                source="test.pdf",
                output_format="json",
            )

        assert result.markdown is None
        assert result.json_data == doc_model

    def test_returns_both(self) -> None:
        doc_model = {"content": "Hello"}
        with (
            patch("aether_mcp_server.tools.DocumentConverter") as mock_cls,
        ):
            mock_cls.return_value.convert.return_value.document.export_to_markdown.return_value = (
                "# Title\n\nHello world."
            )
            mock_cls.return_value.convert.return_value.document.model_dump.return_value = (
                doc_model
            )
            mock_cls.return_value.convert.return_value.document.pages = [MagicMock()]
            result = process_document(
                source="test.pdf",
                output_format="both",
            )

        assert result.markdown == "# Title\n\nHello world."
        assert result.json_data == doc_model

    def test_includes_metadata(self) -> None:
        with (
            patch("aether_mcp_server.tools.DocumentConverter") as mock_cls,
        ):
            mock_cls.return_value.convert.return_value.document.export_to_markdown.return_value = (
                "# Title"
            )
            mock_cls.return_value.convert.return_value.document.pages = [
                MagicMock(),
                MagicMock(),
            ]
            result = process_document(source="test.pdf")

        assert isinstance(result.metadata, dict)
        assert result.metadata["pages"] == 2
