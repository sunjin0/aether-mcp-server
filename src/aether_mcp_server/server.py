import functools
import json
import tempfile
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.responses import JSONResponse, Response

from .auth import StaticTokenVerifier
from .prompts import greet
from .resources import welcome
from .tools import current_time, echo, process_document


class ProcessDocumentRequest(BaseModel):
    """文档处理 REST 接口的请求体。"""

    source: str = Field(description="文档的 URL 地址。")
    output_format: str = Field(default="markdown", pattern="^(markdown|json|both)$")
    ocr: bool = False
    extract_tables: bool = True


def create_server(
    token_verifier: StaticTokenVerifier | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    auth = None
    if token_verifier is not None:
        resource_server_url = f"http://{host}:{port}/mcp"
        auth = AuthSettings(
            issuer_url=resource_server_url,
            resource_server_url=resource_server_url,
        )
    server = FastMCP(
        "Aether MCP Server",
        auth=auth,
        token_verifier=token_verifier,
        stateless_http=True,
        json_response=True,
    )
    server.tool(
        name="echo_message",
        title="消息回显",
        description="接收一段文本并原样返回。",
    )(echo)
    server.tool(
        name="get_current_time",
        title="获取当前 UTC 时间",
        description="返回当前 UTC 时间的 ISO 8601 字符串。",
    )(current_time)
    server.tool(
        name="process_document",
        title="文档处理",
        description="从 URL 下载文档并使用 Docling 进行格式转换与分析（支持 PDF/DOCX/PPTX 等格式）。",
    )(process_document)

    @server.custom_route("/api/convert-file", methods=["POST"])
    async def convert_file_http(request: Request) -> JSONResponse:
        """接受文件上传，处理后返回转换结果。"""
        if token_verifier is not None:
            authorization = request.headers.get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token or await token_verifier.verify_token(token) is None:
                return JSONResponse({"detail": "未授权"}, status_code=401)

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


mcp = create_server()
