from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import anydoc

from aether_mcp_server.tools import (
    DocumentProcessingResult,
    _is_internal_url,
    _ocr_deskew_enabled,
    _needs_ocr,
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

    def test_public_url_is_passed_directly_to_anydoc(self) -> None:
        source = "https://example.com/doc.pdf"
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
            patch("aether_mcp_server.tools._is_internal_url", return_value=False),
        ):
            mock_anydoc.to_markdown.return_value = "# Title"
            process_document(source=source)

        mock_anydoc.to_markdown.assert_called_once_with(source)

    def test_internal_url_is_downloaded_and_temp_file_is_removed(self, tmp_path: Path) -> None:
        temporary_file = tmp_path / "document.pdf"
        temporary_file.write_bytes(b"PDF")
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
            patch("aether_mcp_server.tools._is_internal_url", return_value=True),
            patch("aether_mcp_server.tools._download_to_temp", return_value=temporary_file),
        ):
            mock_anydoc.to_markdown.return_value = "# Title"
            process_document(source="http://10.0.0.1/document.pdf")

        mock_anydoc.to_markdown.assert_called_once_with(str(temporary_file))
        assert not temporary_file.exists()

    def test_returns_markdown_by_default(self) -> None:
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
        ):
            mock_anydoc.to_markdown.return_value = "# Title\n\nHello world."
            result = process_document(source="test.pdf")

        assert isinstance(result, DocumentProcessingResult)
        assert result.markdown == "# Title\n\nHello world."

    def test_includes_metadata(self) -> None:
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
        ):
            mock_anydoc.to_markdown.return_value = "# Title"
            result = process_document(source="test.pdf")

        assert isinstance(result.metadata, dict)
        assert result.metadata["source"] == "test.pdf"
        assert result.metadata["format"] == "markdown"
        assert result.metadata["ocr_applied"] is False
        assert result.metadata["max_concurrency"] == 1

    def test_handles_unsupported_format(self) -> None:
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
        ):
            mock_anydoc.to_markdown.side_effect = anydoc.UnsupportedError("Unsupported")
            result = process_document(source="test.xyz")

        assert result.markdown is None
        assert result.metadata["error"] == "unsupported_format"

    def test_handles_malformed_document(self) -> None:
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
        ):
            mock_anydoc.to_markdown.side_effect = anydoc.MalformedError("Malformed")
            result = process_document(source="test.pdf")

        assert result.markdown is None
        assert result.metadata["error"] == "malformed_document"


class TestOCR:
    def test_deskew_is_disabled_unless_explicitly_enabled(self, monkeypatch) -> None:
        monkeypatch.delenv("AETHER_OCR_DESKEW", raising=False)
        assert _ocr_deskew_enabled() is False
        monkeypatch.setenv("AETHER_OCR_DESKEW", "true")
        assert _ocr_deskew_enabled() is True

    def test_needs_ocr_returns_true_for_empty_pdf(self, tmp_path: Path) -> None:
        # 创建一个简单的 PDF（模拟扫描件）
        pdf_path = tmp_path / "scan.pdf"
        # 写入一个最小的 PDF 内容（无文本层）
        pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n206\n%%EOF"
        pdf_path.write_bytes(pdf_content)

        # 由于 pypdf 可能无法解析这个伪造的 PDF，_needs_ocr 会返回 True
        assert _needs_ocr(pdf_path) is True

    def test_ocr_parameter_is_passed(self) -> None:
        with (
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
            patch("aether_mcp_server.tools._is_internal_url", return_value=False),
        ):
            mock_anydoc.to_markdown.return_value = "# OCR Result"
            result = process_document(source="scan.pdf", ocr=True)

        assert isinstance(result, DocumentProcessingResult)
        assert result.metadata["ocr_applied"] is False  # 非本地文件不触发 OCR

    def test_local_scanned_pdf_runs_ocr_before_conversion(self, tmp_path: Path) -> None:
        source = tmp_path / "scan.pdf"
        source.write_bytes(b"PDF")
        ocr_output = tmp_path / "scan_ocr.pdf"
        with (
            patch("aether_mcp_server.tools._needs_ocr", return_value=True),
            patch("aether_mcp_server.tools._apply_ocr", return_value=ocr_output),
            patch("aether_mcp_server.tools.anydoc") as mock_anydoc,
        ):
            mock_anydoc.to_markdown.return_value = "# OCR Result"
            result = process_document(source=str(source), ocr=True)

        mock_anydoc.to_markdown.assert_called_once_with(str(ocr_output))
        assert result.metadata["ocr_applied"] is True
