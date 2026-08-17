from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from docling.datamodel.base_models import InputFormat

from aether_mcp_server.tools import (
    DocumentProcessingResult,
    _is_internal_url,
    _resolve_admin_file_url,
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
    def test_enhances_embedded_pictures_but_not_the_document_image(self, monkeypatch) -> None:
        class FakeImage:
            def save(self, path: str, format: str) -> None:
                Path(path).write_bytes(b"png")

        class FakePicture:
            prov = [MagicMock(page_no=2)]

            def get_image(self, _doc: object) -> FakeImage:
                return FakeImage()

        picture = FakePicture()
        document = MagicMock()
        document.pages = [MagicMock(), MagicMock()]
        document.export_to_markdown.return_value = "# 报告\n图表说明"
        document.iterate_items.return_value = [(picture, 0)]
        monkeypatch.setattr("aether_mcp_server.tools.PictureItem", FakePicture)
        enhanced_chunk = {
            "chunk_type": "image_description",
            "text": "销售趋势图",
            "image_type": "chart",
            "confidence": 0.9,
            "page": 2,
            "source_image": "temporary.png",
            "ocr_text": "图表说明",
            "warnings": [],
        }
        monkeypatch.setattr(
            "aether_mcp_server.image_enhancement.enhance_image",
            lambda *_args, **_kwargs: MagicMock(status="completed", chunks=[MagicMock(
                model_copy=lambda **_kwargs: MagicMock(model_dump=lambda **_kwargs: enhanced_chunk)
            )]),
        )
        with patch("aether_mcp_server.tools.DocumentConverter") as mock_cls:
            mock_cls.return_value.convert.return_value.document = document
            result = process_document(source="report.pdf", ocr=True)

        assert result.image_chunks == [enhanced_chunk]
        assert result.metadata["embedded_images"] == 1
        assert result.metadata["enhanced_images"] == 1

    def test_does_not_enhance_when_document_has_no_embedded_pictures(self, monkeypatch) -> None:
        document = MagicMock()
        document.pages = [MagicMock()]
        document.export_to_markdown.return_value = "# 图片 OCR 文本"
        document.iterate_items.return_value = []
        with patch("aether_mcp_server.tools.DocumentConverter") as mock_cls:
            mock_cls.return_value.convert.return_value.document = document
            result = process_document(source="image.png", ocr=True)

        assert result.image_chunks == []
        assert result.metadata["embedded_images"] == 0
        assert result.metadata["enhanced_images"] == 0

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

    def test_rewrites_browser_local_file_url_to_admin_service(self, monkeypatch) -> None:
        monkeypatch.setenv("AETHER_ADMIN_INTERNAL_URL", "http://aether-admin:8080")

        result = _resolve_admin_file_url(
            "http://localhost:8001/api/file/chat/preview?objectKey=chat%2Freport.pdf"
        )

        assert result == "http://aether-admin:8080/api/file/chat/preview?objectKey=chat%2Freport.pdf"

    def test_does_not_rewrite_non_file_or_external_urls(self, monkeypatch) -> None:
        monkeypatch.setenv("AETHER_ADMIN_INTERNAL_URL", "http://aether-admin:8080")

        assert _resolve_admin_file_url("http://localhost:8001/health") == "http://localhost:8001/health"
        assert _resolve_admin_file_url("https://example.com/api/file/preview") == "https://example.com/api/file/preview"

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
        assert result.image_chunks == []

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
