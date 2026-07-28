# Aether MCP Server

## Workflow

- Use Python 3.11+ and `uv`; initialize dependencies with `uv sync` and run checks with `uv run pytest -v`.
- Run a focused test with `uv run pytest tests/<file>.py -v`.
- The package entry point is `aether-mcp-server`, mapped to `src/aether_mcp_server/__main__.py`.

## Runtime Behavior

- `uv run aether-mcp-server` starts the stdio transport. `uv run aether-mcp-server http --host 127.0.0.1 --port 8000` starts Streamable HTTP at `/mcp`.
- HTTP authentication is opt-in: add `--auth` and configure `AETHER_MCP_DELEGATION_SECRET`. Java is the only authorization authority: it signs short-lived run JWTs containing `runId`, `userId`, `agentId`, and `allowedTools`; Python services only forward them and MCP verifies them. Do not add static token allowlists.
- Build authenticated HTTP servers through `create_server(...)` in `server.py`; it configures FastMCP `AuthSettings` together with the verifier.

## MCP Metadata

- Register MCP primitives in `server.py`; keep the pure implementations in `tools.py`, `resources.py`, and `prompts.py`.
- Tool wire names are `echo_message` and `get_current_time`, not the internal Python function names `echo` and `current_time`.
- Preserve explicit tool names, titles, and descriptions plus Pydantic field descriptions. Java clients read parameter and result text from `inputSchema` and `outputSchema`.
- `echo` and `current_time` return Pydantic models with `message` and `timestamp` fields; do not revert them to bare strings without changing MCP output schemas and tests.
