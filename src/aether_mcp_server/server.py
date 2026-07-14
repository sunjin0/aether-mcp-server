from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings

from .auth import StaticTokenVerifier
from .prompts import greet
from .resources import welcome
from .tools import current_time, echo


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
    server.resource("example://welcome")(welcome)
    server.prompt()(greet)
    return server


mcp = create_server()
