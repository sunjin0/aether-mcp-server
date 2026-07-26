import logging
import ipaddress
import shutil
import socket
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
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
) -> DocumentProcessingResult:
    logger.info("process_document called: source=%s output_format=%s ocr=%s extract_tables=%s", source, output_format, ocr, extract_tables)

    local_path = _download_to_temp(source) if _is_internal_url(source) else None
    document_source = local_path or source

    try:
        pipeline_options = PdfPipelineOptions(
            do_ocr=ocr,
            do_table_structure=extract_tables,
        )
        format_options = {
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
        converter = DocumentConverter(format_options=format_options)
        logger.info("DocumentConverter created, starting convert...")
        conv_result = converter.convert(document_source)
        doc = conv_result.document
        logger.info("Convert finished, pages=%s", len(doc.pages) if doc.pages else 0)

        markdown_output = doc.export_to_markdown() if output_format in ("markdown", "both") else None
        json_output = doc.model_dump() if output_format in ("json", "both") else None

        result = DocumentProcessingResult(
            markdown=markdown_output,
            json_data=json_output,
            metadata={"pages": len(doc.pages) if doc.pages else 0},
        )
        logger.info("process_document returning, markdown_len=%s has_json=%s", len(markdown_output) if markdown_output else 0, json_output is not None)
        return result
    finally:
        if local_path is not None:
            local_path.unlink(missing_ok=True)
