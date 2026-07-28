# Aether MCP Server

一个基于 Python 的 MCP 演示服务，通过 stdio 或 Streamable HTTP 提供工具、资源和提示词。

## 安装

```powershell
uv sync
```

## 运行

通过 stdio 运行，这是本地 MCP 客户端使用的默认传输方式：

```powershell
uv run aether-mcp-server
```

通过 Streamable HTTP 运行：

```powershell
uv run aether-mcp-server http --host 127.0.0.1 --port 8000
```

HTTP MCP 端点地址为 `http://127.0.0.1:8000/mcp`。

## REST 文档处理接口

使用相同的 HTTP 启动命令后，除 MCP 端点外，还会提供文档处理 REST 接口：

```text
POST http://127.0.0.1:8000/api/process-document
```

请求体与 `process_document` 工具参数一致：

```json
{
  "source": "https://example.com/document.pdf",
  "output_format": "markdown",
  "ocr": false,
  "extract_tables": true
}
```

接口返回 `markdown`、`json_data` 和 `metadata` 字段。启用 `--auth` 时，该接口同样需要在请求中携带 `Authorization: Bearer <token>`。

## 可选 HTTP 认证

HTTP 模式默认不使用认证。需要保护 MCP 端点时，使用 `--auth` 启用 Bearer Token 认证。权限由 Java 服务统一管理：Java 按本次 Agent Run 签发短期 JWT，写入 `runId`、`userId`、`agentId` 和 `allowedTools`；Deep Agent 只透传该令牌，MCP 仅使用共享签名密钥验证令牌及工具范围，不维护静态 Token 白名单。

```powershell
$env:AETHER_MCP_DELEGATION_SECRET = "replace-with-a-32-byte-or-longer-shared-secret"
uv run aether-mcp-server http --auth --host 127.0.0.1 --port 8000
```

启用认证后，客户端请求需要携带 Java 签发的 `Authorization: Bearer <token>`；未配置委托签名密钥时服务会拒绝启动。令牌仅允许调用 `allowedTools` 中列出的工具，未使用 `--auth` 时 HTTP 请求不需要认证。stdio 模式不受影响。

## 可用的 MCP 原语

- 工具：`echo(message)` 和 `current_time()`。
- 资源：`example://welcome`。
- 提示词：`greet(name)`。
