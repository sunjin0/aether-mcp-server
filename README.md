# Aether MCP Server

Aether 的 Python MCP 工具服务，支持 stdio 与 Streamable HTTP。Deep Agent 通过它调用受平台权限约束的工具；工具权限由 Java Admin 在每次运行时签发的短期委派 JWT 管理。

## 开发

要求 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync

# 本地 stdio 模式
uv run aether-mcp-server

# Streamable HTTP 模式
uv run aether-mcp-server http --host 127.0.0.1 --port 8000
```

HTTP MCP 端点为 `http://127.0.0.1:8000/mcp`。使用 HTTP 模式时还提供文档处理接口：

```text
POST /api/process-document
```

## 委派认证与工具权限

生产 HTTP 服务必须使用认证模式：

```powershell
$env:AETHER_MCP_DELEGATION_SECRET = "replace-with-a-long-random-secret"
uv run aether-mcp-server http --auth --host 0.0.0.0 --port 8000
```

Java Admin 为每个 Agent Run 签发 JWT，其中包含 `runId`、`userId`、`agentId` 与 `allowedTools`。Deep Agent 仅透传该令牌；MCP 使用 `AETHER_MCP_DELEGATION_SECRET` 校验签名、有效期和工具范围。服务不维护静态 Token 白名单，未被委派的工具调用会被拒绝。

stdio 模式不使用 HTTP Bearer 认证。HTTP 模式未启用 `--auth` 时仅适合本地调试。

## 工具与文档处理

当前提供示例工具、资源和提示词，并通过 Docling 支持 PDF、DOCX 等文档的结构化提取。`/api/process-document` 的请求参数与 `process_document` 工具一致，返回 Markdown、结构化数据和元数据。

## Docker

```powershell
docker compose up -d --build aether-mcp
```

Docker Compose 会持久化 Hugging Face 与 Docling 缓存，避免重复下载模型。完整平台部署请使用 Java 项目的 `docker-compose.all.yml`。
