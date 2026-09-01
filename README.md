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

## OpenTelemetry

HTTP tracing 与日志 OTLP 导出默认关闭。对接 Collector 时显式配置：

```powershell
$env:AETHER_OTLP_TRACES_ENABLED = "true"
$env:AETHER_OTLP_TRACES_URL = "http://otel-collector:4318/v1/traces"
$env:AETHER_OTLP_LOGS_URL = "http://otel-collector:4318/v1/logs"
$env:OTEL_SERVICE_NAME = "aether-mcp-server"
```

服务会继承入站 `traceparent`，并为 HTTP 请求导出 span；日志通过标准 OTLP Log exporter 导出，不记录凭据或请求体。

## 工具与文档处理

当前提供示例工具、资源和提示词，并通过 Docling 支持 PDF、DOCX 等文档的结构化提取。`/api/process-document` 的请求参数与 `process_document` 工具一致，返回 Markdown、结构化数据和元数据。

### 图像 RAG 语义增强

`enhance_image_for_rag`（REST：`POST /api/enhance-image`）可单独增强指定图片。文档处理工具 `process_document` 默认还会在 Docling 发现**文档内嵌图片**后自动调用同一增强流程，并将 RAG 语义块返回在 `image_chunks`；它不会因输入文件本身是一张图片而自动调用。通过 `enhance_images=false` 可关闭自动增强。图片语义块包含页码、来源、置信度和告警，用于图片、流程图和图表的语义检索，不会将视觉估算伪装为精确图表数据。

配置以下环境变量后启用：

```text
AETHER_VISION_API_BASE_URL=https://your-openai-compatible-endpoint/v1
AETHER_VISION_MODEL=your-vision-model
AETHER_VISION_API_KEY=your-secret
```

未配置时工具返回 `status: "unavailable"` 和原因，OCR/文档处理流程不受影响。认证 HTTP 模式中，Java 签发的委派 JWT 需要包含 `enhance_image_for_rag` 工具权限。

## Docker

```powershell
docker compose up -d --build aether-mcp
```

Docker Compose 会持久化 Hugging Face 与 Docling 缓存，避免重复下载模型。完整平台部署请使用 Java 项目的 `docker-compose.all.yml`。

### 本地 Connector 联调

Java 项目本地观测栈启动后，可用以下脚本在 MCP 运行时容器中验证 Prometheus、Grafana 和 Kubernetes 三类只读 Connector：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-local-connectors.ps1
```

脚本只访问本地测试端点，使用短时 `kubectl proxy`，不会写入 Kubernetes 资源；凭据仅为测试占位值，不得用于生产环境。

## 受控产物 Sandbox

`generate_artifact` 是平台托管的 MCP 工具，不提供任意命令执行，也不会选择或执行 Skill 脚本。它仅接收本轮已授权的文档内容与目标格式，由独立 Runner 调用平台渲染器生成文件。

先构建平台托管运行时与 Runner：

```powershell
docker compose --profile build build sandbox-runtime-python sandbox-runtime-node sandbox-runner
docker compose up -d sandbox-runner
```

Runner 仅接收平台冻结的文本、结构化文档计划和目标格式；平台维护渲染器，并校验文件数量、大小、SHA-256 和 MIME 后创建 Artifact。
