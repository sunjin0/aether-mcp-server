import logging
import ipaddress
import json
import os
import shutil
import socket
import smtplib
import tempfile
import threading
import urllib.request
import urllib.parse
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse, urlunparse

import anydoc
from anydoc import UnsupportedError, MalformedError
import ocrmypdf
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _document_max_concurrency() -> int:
    """读取文档转换并发上限；无效配置降级为单任务执行。"""
    try:
        return max(1, int(os.getenv("AETHER_DOCUMENT_MAX_CONCURRENCY", "1")))
    except ValueError:
        logger.warning("Invalid AETHER_DOCUMENT_MAX_CONCURRENCY; falling back to 1")
        return 1


DOCUMENT_CONVERSION_SEMAPHORE = threading.BoundedSemaphore(_document_max_concurrency())


def _ocr_deskew_enabled() -> bool:
    return os.getenv("AETHER_OCR_DESKEW", "false").strip().lower() in {"1", "true", "yes", "on"}


class EchoResult(BaseModel):
    message: str = Field(description="与请求参数一致的原始消息。")


class CurrentTimeResult(BaseModel):
    timestamp: str = Field(description="当前 UTC 时间的 ISO 8601 字符串。")


class EmailSendResult(BaseModel):
    smtp_accepted: bool = Field(description="SMTP 服务是否已接受本次提交；这不代表邮件已进入发件箱或已投递至收件箱。")
    delivery_status: Literal["accepted_by_smtp", "submission_failed"] = Field(
        description="仅表示 SMTP 提交结果；最终投递状态未知，需由收件服务商、退信或投递回执确认。"
    )
    sender: str = Field(description="脱敏后的发件人邮箱。")
    recipient_count: int = Field(description="实际投递的收件人数量。")
    security: str = Field(description="使用的 SMTP 加密方式。")
    error_code: str | None = Field(default=None, description="安全的失败分类，不包含 SMTP 原始错误。")


class DocumentProcessingResult(BaseModel):
    markdown: str | None = Field(
        default=None,
        description="Markdown 格式的文档内容。",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="文档元数据（格式、页数等）。",
    )


class PrometheusQueryResult(BaseModel):
    status: str = Field(description="Prometheus API 返回状态。")
    data: dict[str, Any] = Field(description="Prometheus 查询结果数据。")


class KubernetesListResult(BaseModel):
    items: list[dict[str, Any]] = Field(description="Kubernetes Pod 摘要列表。")
    resource_version: str | None = Field(default=None, description="Kubernetes 资源版本。")


def kubernetes_get_pods(credential: dict[str, str], namespace: Annotated[str, Field(default="", max_length=253, description="可选 Kubernetes 命名空间。")]= "",
                        label_selector: Annotated[str, Field(default="", max_length=512, description="可选标签选择器。")]= "") -> KubernetesListResult:
    """只读列出 Pod；不允许模型指定 API 地址、Token 或任意资源路径。"""
    endpoint = credential.get("endpoint", "").rstrip("/")
    token = credential.get("token", "")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not token or any(c in namespace + label_selector for c in "\r\n"):
        raise ValueError("Kubernetes 连接器凭据或查询参数无效")
    if namespace and (namespace in {".", ".."} or any(c in namespace for c in "/\\")):
        raise ValueError("Kubernetes 命名空间无效")
    path = "/api/v1/pods" if not namespace else "/api/v1/namespaces/" + urllib.parse.quote(namespace, safe="") + "/pods"
    query = urllib.parse.urlencode({"labelSelector": label_selector}) if label_selector else ""
    request = urllib.request.Request(endpoint + path + ("?" + query if query else ""),
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read(2 * 1024 * 1024))
    except Exception as error:
        raise ValueError("Kubernetes 查询失败") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Kubernetes 返回结果无效")
    summaries = []
    for item in payload["items"]:
        if isinstance(item, dict):
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            summaries.append({"name": metadata.get("name"), "namespace": metadata.get("namespace"),
                              "phase": status.get("phase"), "labels": metadata.get("labels", {})})
    return KubernetesListResult(items=summaries, resource_version=payload.get("metadata", {}).get("resourceVersion")
                                if isinstance(payload.get("metadata"), dict) else None)


def grafana_query(credential: dict[str, str], query: Annotated[str, Field(min_length=1, max_length=4096, description="PromQL 查询表达式。")]) -> PrometheusQueryResult:
    """通过 Grafana 数据源代理执行只读 PromQL 查询。"""
    endpoint = credential.get("endpoint", "").rstrip("/")
    token = credential.get("token", "")
    datasource_uid = credential.get("datasource_uid", "")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not token or not datasource_uid \
            or any(char in datasource_uid for char in "\r\n/\\?"):
        raise ValueError("Grafana 连接器凭据无效")
    request = urllib.request.Request(
        endpoint + "/api/datasources/uid/" + urllib.parse.quote(datasource_uid, safe="")
        + "/resources/api/v1/query?query=" + urllib.parse.quote(query, safe=""),
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read(1024 * 1024))
    except Exception as error:
        raise ValueError("Grafana 查询失败") from error
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise ValueError("Grafana 返回结果无效")
    return PrometheusQueryResult(status="success", data=payload["data"])


def prometheus_query(credential: dict[str, str], query: Annotated[str, Field(min_length=1, max_length=4096, description="PromQL 查询表达式。")]) -> PrometheusQueryResult:
    """使用请求作用域连接器凭据查询 Prometheus，不接受模型提供 endpoint 或 token。"""
    endpoint = credential.get("endpoint", "").rstrip("/")
    token = credential.get("token", "")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or "\n" in endpoint or "\r" in endpoint or not token:
        raise ValueError("Prometheus 连接器凭据无效")
    request = urllib.request.Request(
        endpoint + "/api/v1/query?query=" + urllib.parse.quote(query, safe=""),
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read(1024 * 1024))
    except Exception as error:
        raise ValueError("Prometheus 查询失败") from error
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise ValueError("Prometheus 返回结果无效")
    return PrometheusQueryResult(status="success", data=payload["data"])
def echo(
    message: Annotated[str, Field(description="需要原样返回的文本内容。")],
) -> EchoResult:
    return EchoResult(message=message)


def current_time() -> CurrentTimeResult:
    return CurrentTimeResult(timestamp=datetime.now(UTC).isoformat())


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return (local[:1] + "***" if local else "***") + "@" + domain


def _validate_addresses(values: list[str], field_name: str, required: bool = False) -> list[str]:
    if (required and not values) or any(not isinstance(value, str) or "@" not in value or "\n" in value or "\r" in value for value in values):
        raise ValueError(f"{field_name} 必须包含有效邮箱地址")
    return values


def send_email(
    credential: dict[str, str],
    to: Annotated[list[str], Field(min_length=1, description="收件人邮箱列表。")],
    subject: Annotated[str, Field(min_length=1, max_length=998, description="邮件主题。")],
    text_body: Annotated[str, Field(description="纯文本邮件正文。")],
    cc: Annotated[list[str], Field(default_factory=list, description="抄送邮箱列表。")] = [],
    bcc: Annotated[list[str], Field(default_factory=list, description="密送邮箱列表。")] = [],
    html_body: Annotated[str | None, Field(default=None, description="可选 HTML 邮件正文；支持常见换行符（\\n、\\r\\n、\\r）与制表符。")]= None,
) -> EmailSendResult:
    """用 Admin 注入的一次性邮箱配置发送邮件；绝不记录授权码或 SMTP 连接信息。"""
    sender = credential.get("sender_email", "")
    authorization_code = credential.get("smtp_authorization_code", "")
    smtp_host = credential.get("smtp_host", "")
    security = credential.get("security", "")
    try:
        smtp_port = int(credential.get("smtp_port", ""))
    except (TypeError, ValueError):
        smtp_port = 0
    if not sender or not authorization_code or not smtp_host or security not in ("ssl", "starttls") or not 1 <= smtp_port <= 65535 or "@" not in sender:
        raise ValueError("临时邮件凭据无效")
    # 正文会由 EmailMessage 编码为 MIME 内容，可安全保留格式化换行；仅邮件头相关字段禁止 CRLF 注入。
    if any("\n" in item or "\r" in item for item in (smtp_host, subject)):
        raise ValueError("邮件头字段不能包含换行内容")
    recipients = _validate_addresses(to, "to", required=True) + _validate_addresses(cc, "cc") + _validate_addresses(bcc, "bcc")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")
    try:
        if security == "ssl":
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as client:
                client.login(sender, authorization_code)
                client.send_message(message, from_addr=sender, to_addrs=recipients)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as client:
                client.ehlo()
                client.starttls()
                client.ehlo()
                client.login(sender, authorization_code)
                client.send_message(message, from_addr=sender, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError:
        return EmailSendResult(smtp_accepted=False, delivery_status="submission_failed", sender=_mask_email(sender), recipient_count=len(recipients), security=security, error_code="authentication_failed")
    except (TimeoutError, OSError, smtplib.SMTPException):
        return EmailSendResult(smtp_accepted=False, delivery_status="submission_failed", sender=_mask_email(sender), recipient_count=len(recipients), security=security, error_code="delivery_failed")
    return EmailSendResult(smtp_accepted=True, delivery_status="accepted_by_smtp", sender=_mask_email(sender), recipient_count=len(recipients), security=security)


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
            skip_text=True,         # 已有文本层的页面不重复 OCR
            deskew=_ocr_deskew_enabled(),
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
    source_path = Path(source)
    local_document = local_path or (source_path if source_path.is_file() else None)
    document_source = local_document or source
    ocr_path = None

    try:
        # AnyDoc 与 OCR 都是 CPU 密集任务。闸门避免多个大文件同时耗尽实例 CPU。
        with DOCUMENT_CONVERSION_SEMAPHORE:
            # 如果启用 OCR 且是本地 PDF 文件，先检测是否需要 OCR
            if ocr and local_document and local_document.suffix.lower() == ".pdf":
                if _needs_ocr(local_document):
                    logger.info("Scanned PDF detected, applying OCR...")
                    ocr_path = _apply_ocr(local_document)
                    document_source = ocr_path

            # AnyDoc 转换为 Markdown
            if isinstance(document_source, Path):
                markdown_output = anydoc.to_markdown(str(document_source))
            else:
                markdown_output = anydoc.to_markdown(document_source)

            metadata = {
                "source": source,
                "format": "markdown",
                "ocr_applied": ocr_path is not None,
                "max_concurrency": _document_max_concurrency(),
            }
            result = DocumentProcessingResult(markdown=markdown_output, metadata=metadata)
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
