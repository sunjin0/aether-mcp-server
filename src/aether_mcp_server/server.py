import functools
import json
import logging
import tempfile
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import JavaDelegationVerifier
from .credentials import CredentialTokenError, decrypt_email_credential, decrypt_connector_credential
from .prompts import greet
from .resources import welcome
from .tools import current_time, echo, process_document, send_email, prometheus_query, grafana_query, kubernetes_get_pods
from .artifact import ArtifactGenerationResult, generate_artifact
from .idempotency import IdempotencyStore, decode_run_id, execute_idempotently
from .telemetry import OTelMiddleware, configure_tracing, shutdown_tracing

logger = logging.getLogger(__name__)
delegated_token: ContextVar[str | None] = ContextVar("delegated_token", default=None)
email_credential: ContextVar[dict[str, str] | None] = ContextVar("email_credential", default=None)
connector_credential: ContextVar[dict[str, object] | None] = ContextVar("connector_credential", default=None)
trace_parent: ContextVar[str | None] = ContextVar("trace_parent", default=None)
IDEMPOTENCY_STORE = IdempotencyStore()


class DelegatedToolScopeMiddleware:
    """在 MCP 工具请求分发前校验 Java 委派 JWT 的工具权限。

    FastMCP 的 Streamable HTTP 会话工作线程运行在认证上下文之外，因此工具函数内
    无法使用 ``get_access_token()``。在 ASGI 边界校验 JSON-RPC 请求，能使令牌及
    其工具权限始终绑定到原始 HTTP 请求。
    """

    def __init__(self, app: Any, verifier: JavaDelegationVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        if not headers.get("content-type", "").lower().startswith("application/json"):
            # multipart 上传由对应 REST 端点授权；在此读取会将二进制文件误解析为 JSON。
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        try:
            payload = json.loads(body)
            method = payload.get("method") if isinstance(payload, dict) else None
            tool_name = payload.get("params", {}).get("name") if method == "tools/call" else None
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            tool_name = None

        token_context = None
        credential_context = None
        connector_context = None
        trace_context = None
        incoming_trace = headers.get("traceparent", "")
        if incoming_trace and __import__("re").fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-0[1-9a-f]", incoming_trace):
            trace_context = trace_parent.set(incoming_trace)
        if tool_name:
            scheme, _, raw_token = headers.get("authorization", "").partition(" ")
            access_token = await self.verifier.verify_token(raw_token) if scheme.lower() == "bearer" and raw_token else None
            if access_token is None or tool_name not in access_token.scopes:
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id") if isinstance(payload, dict) else None,
                    "error": {"code": -32604, "message": "Delegated token is not authorized for tool: " + str(tool_name)},
                }
                encoded = json.dumps(response).encode("utf-8")
                await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(encoded)).encode())]})
                await send({"type": "http.response.body", "body": encoded})
                return
            # ContextVar 会复制到 FastMCP 工作任务。工具不会接收用户可控的身份信息，
            # 只会转发这里已验证的 JWT。
            token_context = delegated_token.set(raw_token)
            connector_token = headers.get("x-aether-connector-credential", "")
            if connector_token:
                try:
                    claims = __import__("jwt").decode(raw_token, self.verifier._delegation_secret, algorithms=["HS256"])
                    connector_context = connector_credential.set(
                        decrypt_connector_credential(connector_token, claims, tool_name)
                    )
                except (CredentialTokenError, ValueError, TypeError, json.JSONDecodeError):
                    if token_context is not None:
                        delegated_token.reset(token_context)
                        token_context = None
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32604, "message": "连接器临时凭据无效或已过期"}}
                    encoded = json.dumps(response).encode("utf-8")
                    await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(encoded)).encode())]})
                    await send({"type": "http.response.body", "body": encoded})
                    return
            if tool_name == "send_email":
                try:
                    claims = __import__("jwt").decode(raw_token, self.verifier._delegation_secret, algorithms=["HS256"])
                    raw_credentials = headers.get("x-aether-email-credentials", "")
                    token_map = json.loads(raw_credentials) if raw_credentials else {}
                    if not isinstance(token_map, dict) or not token_map:
                        raise CredentialTokenError("缺少邮件凭据")
                    decrypted = [decrypt_email_credential(value, claims)
                                 for key, value in token_map.items() if isinstance(key, str) and isinstance(value, str)]
                    # 当前用户每次运行只允许使用自己的默认邮箱配置，模型无法选择或替换凭据。
                    if len(decrypted) != 1:
                        raise CredentialTokenError("邮件凭据令牌无效")
                    credential_context = email_credential.set(decrypted[0])
                except (CredentialTokenError, ValueError, TypeError, json.JSONDecodeError):
                    if token_context is not None:
                        delegated_token.reset(token_context)
                        token_context = None
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32604, "message": "邮件临时凭据无效或已过期"}}
                    encoded = json.dumps(response).encode("utf-8")
                    await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(encoded)).encode())]})
                    await send({"type": "http.response.body", "body": encoded})
                    return

        delivered = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        try:
            await self.app(scope, replay_receive, send)
        finally:
            if token_context is not None:
                delegated_token.reset(token_context)
            if credential_context is not None:
                email_credential.reset(credential_context)
            if connector_context is not None:
                connector_credential.reset(connector_context)
            if trace_context is not None:
                trace_parent.reset(trace_context)


async def require_request_scope(request: Request, tool_name: str, verifier: JavaDelegationVerifier) -> JSONResponse | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        return JSONResponse({"detail": "未授权"}, status_code=401)
    access_token = await verifier.verify_token(raw_token)
    if access_token is None or tool_name not in access_token.scopes:
        return JSONResponse({"detail": "未授权"}, status_code=401)
    return None


def authorized_echo(message: str, ctx: Context) -> object:
    return echo(message)


def authorized_current_time(ctx: Context) -> object:
    return current_time()


def authorized_process_document(
    source: str,
    output_format: str = "markdown",
    ocr: bool = False,
    ctx: Context | None = None,
) -> object:
    return process_document(source, output_format, ocr)


def authorized_send_email(to: list[str], subject: str, text_body: str, cc: list[str] | None = None,
                          bcc: list[str] | None = None, html_body: str | None = None, ctx: Context | None = None) -> object:
    credential = email_credential.get()
    if credential is None:
        raise ValueError("邮件临时凭据无效或已过期")
    return send_email(credential, to, subject, text_body, cc or [], bcc or [], html_body)


def authorized_prometheus_query(query: str, ctx: Context | None = None) -> object:
    scoped = connector_credential.get()
    if not scoped or not isinstance(scoped.get("credential"), dict):
        raise ValueError("Prometheus 临时凭据无效或已过期")
    return prometheus_query(scoped["credential"], query)


def authorized_grafana_query(query: str, ctx: Context | None = None) -> object:
    scoped = connector_credential.get()
    if not scoped or not isinstance(scoped.get("credential"), dict):
        raise ValueError("Grafana 临时凭据无效或已过期")
    return grafana_query(scoped["credential"], query)


def authorized_kubernetes_get_pods(namespace: str = "", label_selector: str = "", ctx: Context | None = None) -> object:
    scoped = connector_credential.get()
    if not scoped or not isinstance(scoped.get("credential"), dict):
        raise ValueError("Kubernetes 临时凭据无效或已过期")
    return kubernetes_get_pods(scoped["credential"], namespace, label_selector)


def authorized_generate_artifact(title: str, content: str, format: str, file_name: str | None = None,
                                document: dict[str, Any] | None = None,
                                aether_delegation: str | None = None, ctx: Context | None = None) -> object:
    # Java 的同步 MCP 执行器显式传入令牌。Deep Agent 调用则使用中间件保存的、
    # 已验证 HTTP Authorization 令牌，避免要求模型提供任何密钥。
    delegation = aether_delegation or delegated_token.get()
    if not delegation:
        raise ValueError("缺少已验证的委派执行令牌")
    # 同一运行内重试相同参数的工具调用直接返回已确认的产物执行结果，
    # 避免重复提交渲染任务。跨重启的幂等由 Deep Agent 检查点/outbox 恢复负责。
    run_id = decode_run_id(delegation)
    arguments = {"title": title, "content": content, "format": format,
                 "file_name": file_name, "document": document}
    def submit_artifact() -> dict[str, Any]:
        generated = generate_artifact(title, content, format, file_name, document, delegation)
        return generated.model_dump() if hasattr(generated, "model_dump") else generated

    result = execute_idempotently(IDEMPOTENCY_STORE, run_id, "generate_artifact", arguments, submit_artifact)
    if run_id and result.get("run_id"):
        logger.info("generate_artifact idempotent action: runId=%s executionId=%s", run_id, result.get("execution_id"))
    if "execution_id" not in result or "run_id" not in result:
        return result
    return ArtifactGenerationResult(**result)


class ProcessDocumentRequest(BaseModel):
    """文档处理 REST 接口的请求体。"""

    source: str = Field(description="文档的 URL 地址。")
    output_format: str = Field(default="markdown", pattern="^markdown$")
    ocr: bool = Field(default=False, description="是否对扫描件 PDF 启用 OCR。")


def create_server(
    token_verifier: JavaDelegationVerifier | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    # HTTP 认证会启用 FastMCP 的 DNS 重绑定防护。共享 Docker 网络中的
    # Deep Agent 通过服务名访问，必须将该服务名加入受信 Host 白名单。
    allowed_hosts = [
        f"{host}:{port}",
        f"localhost:{port}",
        f"127.0.0.1:{port}",
        f"aether-mcp:{port}",
        f"aether-mcp-server-aether-mcp-1:{port}",
    ]
    server = FastMCP(
        "Aether MCP Server",
        # 工具发现（initialize / ping / tools/list）只暴露公开元数据，交由
        # Java 管理端的 Dashboard 权限保护。实际 tools/call 由最外层
        # DelegatedToolScopeMiddleware 校验 Java 签发的短期 JWT 及工具范围。
        # 不将 verifier 交给 FastMCP，避免它对发现请求也强制静态 Bearer Token。
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        ),
    )
    server.tool(
        name="echo_message",
        title="消息回显",
        description="接收一段文本并原样返回。",
    )(authorized_echo)
    server.tool(
        name="get_current_time",
        title="获取当前 UTC 时间",
        description="返回当前 UTC 时间的 ISO 8601 字符串。",
    )(authorized_current_time)
    server.tool(
        name="process_document",
        title="文档处理",
        description="从 URL 下载文档并使用 AnyDoc 进行格式转换（支持 PDF/DOCX/PPTX/XLSX 等格式）。",
    )(authorized_process_document)
    server.tool(name="send_email", title="发送邮件", description="使用当前 Agent 已启用的 SMTP 邮箱提交邮件；每次发送均需平台确认。工具返回仅代表 SMTP 已接受提交，不能声明最终投递到收件箱或出现在发件箱。text_body 和 html_body 支持常见格式化换行（\\n、\\r\\n、\\r）及制表符；主题、邮箱地址和 SMTP 主机仍禁止换行。")(authorized_send_email)
    server.tool(
        name="prometheus_query",
        title="Prometheus 查询",
        description="使用平台委派的 Prometheus 连接器凭据执行只读 PromQL 查询。",
    )(authorized_prometheus_query)
    server.tool(
        name="grafana_query",
        title="Grafana 查询",
        description="使用平台委派的 Grafana 连接器凭据执行只读 PromQL 查询。",
    )(authorized_grafana_query)
    server.tool(
        name="kubernetes_get_pods",
        title="Kubernetes Pod 查询",
        description="使用平台委派的 Kubernetes 连接器凭据只读查询 Pod 摘要，不执行写入或远程命令。",
    )(authorized_kubernetes_get_pods)
    server.tool(
        name="generate_artifact",
        title="生成受控文件产物",
        description="根据已生成的正文或结构化计划，使用平台通用渲染器生成经沙箱校验的 PDF、DOCX 或 XLSX 文件。生成 PDF 时，context 中的 content 必须是模型生成的完整 HTML（含 <style>、布局和正文），不可使用 Markdown 或纯文本；平台保留安全布局和 CSS，不替换为固定主题。",
    )(authorized_generate_artifact)

    @server.custom_route("/api/process-document", methods=["POST"])
    async def process_document_http(request: Request) -> JSONResponse:
        """通过 REST API 处理 URL 文档。"""
        if token_verifier is not None:
            unauthorized = await require_request_scope(request, "process_document", token_verifier)
            if unauthorized is not None:
                return unauthorized

        try:
            arguments = ProcessDocumentRequest.model_validate(await request.json())
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
            return JSONResponse(
                {"detail": "请求参数无效", "errors": str(error)},
                status_code=422,
            )

        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(process_document, **arguments.model_dump())
            )
        except Exception:
            logger.exception("URL 文档处理失败")
            return JSONResponse({"detail": "文档处理失败"}, status_code=500)

        return JSONResponse({"markdown": result.markdown, "metadata": result.metadata})

    @server.custom_route("/api/convert-file", methods=["POST"])
    async def convert_file_http(request: Request) -> JSONResponse:
        """接受文件上传，处理后返回转换结果。"""
        if token_verifier is not None:
            unauthorized = await require_request_scope(request, "process_document", token_verifier)
            if unauthorized is not None:
                return unauthorized

        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            return JSONResponse({"detail": "缺少文件"}, status_code=422)

        output_format = form.get("output_format", "markdown")
        ocr = str(form.get("ocr", "false")).strip().lower() in {"1", "true", "yes", "on"}

        suffix = Path(file.filename or "file.bin").suffix or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(
                    process_document,
                    source=str(tmp_path),
                    output_format=output_format,
                    ocr=ocr,
                )
            )
        except Exception:
            logger.exception("上传文档处理失败")
            return JSONResponse({"detail": "文档处理失败"}, status_code=500)
        finally:
            tmp_path.unlink(missing_ok=True)

        return JSONResponse(result.model_dump(mode="json"))

    server.resource("example://welcome")(welcome)
    server.prompt()(greet)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/metrics", methods=["GET"])
    async def metrics(request: Request) -> JSONResponse:
        """动作幂等命中率等轻量运行指标，供运营侧对账工具幂等命中率。"""
        return JSONResponse(IDEMPOTENCY_STORE.stats())

    return server


async def run_http_server(server: FastMCP, verifier: JavaDelegationVerifier | None, host: str, port: int) -> None:
    """在 Java 委派工具权限网关之后运行 MCP HTTP 传输层。"""
    app: Any = server.streamable_http_app()
    configure_tracing()
    app = OTelMiddleware(app)
    if verifier is not None:
        # 必须作为 Uvicorn 的最外层 ASGI 应用运行，不能使用 FastMCP 内部
        # Starlette middleware；后者在当前版本不会包裹 /mcp 传输处理器。
        app = DelegatedToolScopeMiddleware(app, verifier)
    try:
        await uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level=server.settings.log_level.lower())
        ).serve()
    finally:
        shutdown_tracing()


mcp = create_server()
