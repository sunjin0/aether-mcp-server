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

## 受控产物 Sandbox

`generate_artifact` 是平台托管的 MCP 工具，不提供任意命令执行。它只能申请执行当前 Agent 已安装、已发布 Skill 的冻结入口。Admin 冻结资源、输入和执行策略后，由独立 Runner 启动一次性无网络容器。

先构建平台托管运行时与 Runner：

```powershell
docker compose --profile build build sandbox-runtime-python sandbox-runtime-node sandbox-runner
docker compose up -d sandbox-runner
```

Skill 的入口脚本只需读取以下路径，不应尝试下载依赖或访问网络：

| 环境变量 | 含义 |
| --- | --- |
| `AETHER_INPUT_JSON` | 冻结输入 JSON 字符串 |
| `AETHER_INPUT_FILE` | 只读输入 JSON 文件路径 |
| `AETHER_RESOURCE_DIR` | 只读 Skill 资源目录（模板、脚本等） |
| `AETHER_OUTPUT_DIR` | 唯一可写的输出目录 |

运行容器将资源卷和输入卷分别以只读方式挂载，只给输出卷写权限；平台维护 Python（WeasyPrint、python-docx、openpyxl、pybars3）及 Node（PDFKit、docx、xlsx、Handlebars）运行时。脚本只能在输出目录生成已声明的 PDF、DOCX、XLSX；Runner 会校验数量、大小、SHA-256 和 MIME 后创建 Artifact。
