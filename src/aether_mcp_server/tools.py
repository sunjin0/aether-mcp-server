import logging
import ipaddress
import os
import shutil
import socket
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse, urlunparse

from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling_core.types.doc import PictureItem
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EchoResult(BaseModel):
    message: str = Field(description="与请求参数一致的原始消息。")


class CurrentTimeResult(BaseModel):
    timestamp: str = Field(description="当前 UTC 时间的 ISO 8601 字符串。")


class DocumentProcessingResult(BaseModel):
    markdown: str | None = Field(
        default=None,
        description="Markdown 格式的文档内容。",
    )
    json_data: dict[str, Any] | None = Field(
        default=None,
        description="JSON 格式的文档结构。",
    )
    image_chunks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="文档内嵌图片经视觉模型生成的 RAG 语义块。",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="文档元数据（页数等）。",
    )


def echo(
    message: Annotated[str, Field(description="需要原样返回的文本内容。")],
) -> EchoResult:
    return EchoResult(message=message)


def current_time() -> CurrentTimeResult:
    return CurrentTimeResult(timestamp=datetime.now(UTC).isoformat())


def _download_to_temp(source: str) -> Path:
    """将 URL 下载到临时文件，供 Docling 作为本地文件读取。"""
    parsed = urlparse(source)
    logger.info("downloading %s ...", source)
    req = urllib.request.Request(source, headers={"User-Agent": "Aether-MCP/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        suffix = Path(parsed.path).suffix or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(resp, tmp)
            tmp_path = Path(tmp.name)
    logger.info("downloaded to %s (%d bytes)", tmp_path, tmp_path.stat().st_size)
    return tmp_path


def _resolve_admin_file_url(source: str) -> str:
    """将浏览器本机文件 URL 改写为 Docker 网络内的管理端地址。"""
    parsed = urlparse(source)
    admin_url = os.getenv("AETHER_ADMIN_INTERNAL_URL", "").rstrip("/")
    if (
        not admin_url
        or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        or not parsed.path.startswith("/api/file/")
    ):
        return source

    admin = urlparse(admin_url)
    if admin.scheme not in ("http", "https") or not admin.netloc:
        logger.warning("Ignoring invalid AETHER_ADMIN_INTERNAL_URL")
        return source
    # 仅替换协议和主机，保留原始文件接口路径、查询参数及片段。
    return urlunparse((admin.scheme, admin.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _is_internal_url(source: str) -> bool:
    """判断 HTTP(S) URL 是否解析到私有或本机地址。"""
    parsed = urlparse(source)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    try:
        addresses = {ipaddress.ip_address(parsed.hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(parsed.hostname, None)
            }
        except socket.gaierror:
            return False

    return any(
        address.is_private or address.is_loopback or address.is_link_local
        for address in addresses
    )


def _enhance_embedded_images(doc: Any, source: str, ocr_text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract Docling picture items and enrich each one without failing document OCR."""
    from .image_enhancement import enhance_image

    chunks: list[dict[str, Any]] = []
    pictures = 0
    failures = 0
    unavailable_reason: str | None = None
    for item, _level in doc.iterate_items():
        if not isinstance(item, PictureItem):
            continue
        pictures += 1
        image = item.get_image(doc)
        if image is None:
            failures += 1
            continue
        page = item.prov[0].page_no if item.prov else None
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temporary_image:
            temporary_image_path = Path(temporary_image.name)
        try:
            image.save(temporary_image_path, format="PNG")
            result = enhance_image(str(temporary_image_path), ocr_text=ocr_text, page=page)
        except (RuntimeError, ValueError):
            logger.warning("Embedded image enhancement failed for %s page %s", source, page, exc_info=True)
            failures += 1
            continue
        finally:
            temporary_image_path.unlink(missing_ok=True)
        if result.status == "unavailable":
            unavailable_reason = str(result.metadata.get("reason", "视觉模型未配置"))
            break
        for chunk in result.chunks:
            # The temporary PNG is an implementation detail; RAG citations point to its parent document.
            chunks.append(chunk.model_copy(update={"source_image": source}).model_dump(mode="json"))

    metadata: dict[str, Any] = {"embedded_images": pictures, "enhanced_images": len(chunks)}
    if failures:
        metadata["image_enhancement_failures"] = failures
    if unavailable_reason:
        metadata["image_enhancement_unavailable"] = unavailable_reason
    return chunks, metadata


def process_document(
    source: Annotated[str, Field(description="文档的 URL 地址。")],
    output_format: Annotated[
        Literal["markdown", "json", "both"],
        Field(default="markdown", description="输出格式：markdown / json / both。"),
    ] = "markdown",
    ocr: Annotated[
        bool,
        Field(default=False, description="是否启用 OCR（光学字符识别）。"),
    ] = False,
    extract_tables: Annotated[
        bool,
        Field(default=True, description="是否提取表格结构。"),
    ] = True,
    enhance_images: Annotated[
        bool,
        Field(default=True, description="发现文档内嵌图片时，是否调用视觉模型生成 RAG 图片语义块。"),
    ] = True,
) -> DocumentProcessingResult:
    source = _resolve_admin_file_url(source)
    logger.info("process_document called: source=%s output_format=%s ocr=%s extract_tables=%s enhance_images=%s", source, output_format, ocr, extract_tables, enhance_images)

    # Docling 对内网地址先下载到本地，避免转换器在容器网络外重复请求受限地址。
    local_path = _download_to_temp(source) if _is_internal_url(source) else None
    document_source = local_path or source

    try:
        pipeline_options = PdfPipelineOptions(
            do_ocr=ocr,
            do_table_structure=extract_tables,
        )
        format_options = {
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        # 图片与 PDF 共用标准版面和 OCR 管线，确保 ocr 参数对图片也生效。
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
        converter = DocumentConverter(format_options=format_options)
        logger.info("DocumentConverter created, starting convert...")
        conv_result = converter.convert(document_source)
        doc = conv_result.document
        logger.info("Convert finished, pages=%s", len(doc.pages) if doc.pages else 0)

        markdown_output = doc.export_to_markdown() if output_format in ("markdown", "both") else None
        json_output = doc.model_dump() if output_format in ("json", "both") else None
        image_chunks: list[dict[str, Any]] = []
        metadata = {"pages": len(doc.pages) if doc.pages else 0}
        if enhance_images:
            # Use the document's text as OCR context even when callers only request JSON output.
            image_chunks, enhancement_metadata = _enhance_embedded_images(
                doc, source, markdown_output or doc.export_to_markdown()
            )
            metadata.update(enhancement_metadata)

        result = DocumentProcessingResult(
            markdown=markdown_output,
            json_data=json_output,
            image_chunks=image_chunks,
            metadata=metadata,
        )
        logger.info("process_document returning, markdown_len=%s has_json=%s", len(markdown_output) if markdown_output else 0, json_output is not None)
        return result
    finally:
        if local_path is not None:
            # 仅清理由本次调用创建的临时文件，调用方传入的本地路径不受影响。
            local_path.unlink(missing_ok=True)
