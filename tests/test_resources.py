from aether_mcp_server.resources import welcome


def test_welcome_resource_describes_the_server() -> None:
    assert welcome() == "Welcome to Aether MCP Server."
