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

HTTP 模式默认不使用认证。需要保护 MCP 端点时，使用 `--auth` 启用 Bearer Token 认证。`AETHER_MCP_TOKENS` 使用逗号分隔多个 Token，并会自动去除每项首尾空格。

```powershell
$env:AETHER_MCP_TOKENS = "replace-with-a-secret"
uv run aether-mcp-server http --auth --host 127.0.0.1 --port 8000
```

启用认证后，客户端请求需要携带 `Authorization: Bearer <token>`；未配置有效令牌时服务会拒绝启动。未使用 `--auth` 时，不读取该变量且 HTTP 请求不需要认证。stdio 模式不受影响。

## 可用的 MCP 原语

- 工具：`echo(message)` 和 `current_time()`。
- 资源：`example://welcome`。
- 提示词：`greet(name)`。
