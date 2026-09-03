# AGENTS.md

## Project overview

Python 3.11+ MCP server supporting stdio and Streamable HTTP transports. MCP is the tool authority for delegated calls; Java Admin signs short-lived JWTs containing `runId`, `userId`, `agentId` and `allowedTools`, and this service validates them when HTTP auth is enabled.

## Development and runtime

Use `uv`:

```powershell
uv sync
uv run pytest -q
uv run aether-mcp-server
uv run aether-mcp-server http --auth --host 0.0.0.0 --port 8000
```

The HTTP MCP endpoint is `/mcp`; document processing APIs are also exposed by the HTTP server. Docker deployment is `docker compose up -d --build aether-mcp`.

## Authentication and security

Production HTTP mode must use `--auth` and `AETHER_MCP_DELEGATION_SECRET`. Do not add static token allowlists or Secret Provider/Vault/Kubernetes integration. Never log or commit credentials. Preserve Java-signed delegation, expiration and `allowedTools` checks.

## MCP implementation

Register primitives in `server.py`; keep implementations in the dedicated tool/resource/prompt modules. Preserve explicit wire names, titles, descriptions, Pydantic schemas and output models because Java clients consume `inputSchema` and `outputSchema`.

## Configuration scope

Use environment variables for model, document, vision and delegation settings. Observability/OTel configuration has been retired; do not add OTLP exporters, Collector settings or Prometheus/Grafana deployment configuration.

## Verification and commits

Run `uv run pytest -q` and focused tests for changed behavior. Use Conventional Commits: `<type>(<scope>): <中文提交描述>` with types such as `feat`, `fix`, `refactor`, `test`, `build`, `docs`, and `chore`. The commit description must be in Chinese and focused on one change; the commit body must list what was changed, affected tools/protocol/configuration behavior, and verification results. Review `git diff`, and exclude `.env`, caches and generated files.
