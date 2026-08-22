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

import anydoc
from anydoc import UnsupportedError, MalformedError
import ocrmypdf
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
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="文档元数据（格式、页数等）。",
    )


def echo(
    message: Annotated[str, Field(description="需要原样返回的文本内容。")],
) -> EchoResult:
    return EchoResult(message=message)


def current_time() -> CurrentTimeResult:
    return CurrentTimeResult(timestamp=datetime.now(UTC).isoformat())


def _download_to_temp(source: str) -> Path:
    """将 URL 下载到临时文件，供 AnyDoc 作为本地文件读取。"""
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


def _needs_ocr(pdf_path: Path) -> bool:
    """检测 PDF 是否为扫描件（无可搜索文本层）。"""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        for page in reader.pages[:3]:  # 只检查前3页
            text = page.extract_text()
            if text and text.strip():
                return False  # 有文本，不需要 OCR
        return True  # 无文本，需要 OCR
    except Exception:
        return True  # 无法解析，默认需要 OCR


def _apply_ocr(pdf_path: Path) -> Path:
    """对扫描件 PDF 执行 OCR，生成可搜索 PDF。"""
    output_path = pdf_path.parent / f"{pdf_path.stem}_ocr{pdf_path.suffix}"
    try:
        ocrmypdf.ocr(
            str(pdf_path),
            str(output_path),
            language="chi_sim+eng",  # 中文简体 + 英文
            force_ocr=True,         # 强制 OCR
            skip_text=False,        # 不跳过已有文本层
            deskew=True,            # 自动校正倾斜
        )
        logger.info("OCR completed: %s -> %s", pdf_path, output_path)
        return output_path
    except ocrmypdf.exceptions.MissingDependencyError:
        logger.warning("OCR dependencies not installed, skipping OCR")
        return pdf_path
    except Exception as e:
        logger.warning("OCR failed: %s, falling back to original", e)
        return pdf_path


def process_document(
    source: Annotated[str, Field(description="文档的 URL 地址。")],
    output_format: Annotated[
        Literal["markdown"],
        Field(default="markdown", description="输出格式：markdown。"),
    ] = "markdown",
    ocr: Annotated[
        bool,
        Field(default=False, description="是否对扫描件 PDF 启用 OCR。"),
    ] = False,
) -> DocumentProcessingResult:
    source = _resolve_admin_file_url(source)
    logger.info("process_document called: source=%s output_format=%s ocr=%s", source, output_format, ocr)

    # AnyDoc 对内网地址先下载到本地，避免转换器在容器网络外重复请求受限地址。
    local_path = _download_to_temp(source) if _is_internal_url(source) else None
    document_source = local_path or source
    ocr_path = None

    try:
        # 如果启用 OCR 且是本地 PDF 文件，先检测是否需要 OCR
        if ocr and local_path and str(local_path).endswith(".pdf"):
            if _needs_ocr(local_path):
                logger.info("Scanned PDF detected, applying OCR...")
                ocr_path = _apply_ocr(local_path)
                document_source = ocr_path

        # AnyDoc 转换为 Markdown
        if isinstance(document_source, Path):
            markdown_output = anydoc.to_markdown(str(document_source))
        else:
            markdown_output = anydoc.to_markdown(document_source)

        metadata = {"source": source, "format": "markdown", "ocr_applied": ocr_path is not None}

        result = DocumentProcessingResult(
            markdown=markdown_output,
            metadata=metadata,
        )
        logger.info("process_document returning, markdown_len=%s", len(markdown_output) if markdown_output else 0)
        return result
    except UnsupportedError:
        logger.error("Unsupported document format: %s", source)
        return DocumentProcessingResult(
            markdown=None,
            metadata={"error": "unsupported_format", "source": source},
        )
    except MalformedError:
        logger.error("Malformed document: %s", source)
        return DocumentProcessingResult(
            markdown=None,
            metadata={"error": "malformed_document", "source": source},
        )
    finally:
        # 清理临时文件
        if ocr_path and ocr_path.exists():
            ocr_path.unlink(missing_ok=True)
        if local_path is not None:
            local_path.unlink(missing_ok=True)
