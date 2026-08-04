import functools
import json
import logging
import tempfile
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
from .prompts import greet
from .resources import welcome
from .tools import current_time, echo, process_document

logger = logging.getLogger(__name__)


class DelegatedToolScopeMiddleware:
    """Validate Java delegation JWT scopes before an MCP tool request is dispatched.

    FastMCP's streamable HTTP session worker runs outside the authentication
    context, so ``get_access_token()`` is unavailable inside tool functions.
    Checking the JSON-RPC request at the ASGI boundary keeps the token and its
    per-tool scope bound to the original HTTP request.
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
            # Multipart uploads are authorized by their REST endpoint. Reading
            # them here would try to parse binary file content as JSON.
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

        delivered = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


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
    extract_tables: bool = True,
    ctx: Context | None = None,
) -> object:
    return process_document(source, output_format, ocr, extract_tables)


class ProcessDocumentRequest(BaseModel):
    """文档处理 REST 接口的请求体。"""

    source: str = Field(description="文档的 URL 地址。")
    output_format: str = Field(default="markdown", pattern="^(markdown|json|both)$")
    ocr: bool = False
    extract_tables: bool = True


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
        description="从 URL 下载文档并使用 Docling 进行格式转换与分析（支持 PDF/DOCX/PPTX 等格式）。",
    )(authorized_process_document)

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

        return JSONResponse(result.model_dump(mode="json"))

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
        ocr = form.get("ocr", "false") in ("true", "True", True)
        extract_tables = form.get("extract_tables", "true") in ("true", "True", True)

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
                    extract_tables=extract_tables,
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

    return server


async def run_http_server(server: FastMCP, verifier: JavaDelegationVerifier | None, host: str, port: int) -> None:
    """Run the MCP HTTP transport behind the Java-delegated tool-scope gateway."""
    app: Any = server.streamable_http_app()
    if verifier is not None:
        # 必须作为 Uvicorn 的最外层 ASGI 应用运行，不能使用 FastMCP 内部
        # Starlette middleware；后者在当前版本不会包裹 /mcp 传输处理器。
        app = DelegatedToolScopeMiddleware(app, verifier)
    await uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level=server.settings.log_level.lower())
    ).serve()


mcp = create_server()
