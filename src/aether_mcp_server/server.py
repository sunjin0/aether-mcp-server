from mcp.server.fastmcp import FastMCP

from .prompts import greet
from .resources import welcome
from .tools import current_time, echo

mcp = FastMCP("Aether MCP Server")
mcp.tool()(echo)
mcp.tool()(current_time)
mcp.resource("example://welcome")(welcome)
mcp.prompt()(greet)
