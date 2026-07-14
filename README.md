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

## 可用的 MCP 原语

- 工具：`echo(message)` 和 `current_time()`。
- 资源：`example://welcome`。
- 提示词：`greet(name)`。
