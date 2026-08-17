"""受控产物 MCP 操作，仅转发已验证的委派令牌。"""
import json
import os
import urllib.error
import urllib.request
from typing import Any, Annotated

from pydantic import BaseModel, Field


class ArtifactGenerationResult(BaseModel):
    execution_id: str = Field(description="平台冻结的产物执行 ID。")
    run_id: str = Field(description="关联的 Agent 运行 ID。")
    status: str = Field(description="执行状态；当前为 queued。")


def generate_artifact(
    title: Annotated[str, Field(description="文档标题。")],
    content: Annotated[str, Field(description="待生成的完整 Markdown、正文或 HTML 内容。仅当 format=pdf 时，平台会识别 HTML 并在无网络隔离环境中按安全白名单渲染；脚本、外部资源和事件属性会被移除。")],
    format: Annotated[str, Field(description="输出格式，仅支持 docx、xlsx 或 pdf。")],
    file_name: Annotated[str | None, Field(description="可选文件名；未提供时由平台根据标题生成。 ")] = None,
    document: Annotated[dict[str, Any] | None, Field(description="可选结构化文档计划，用于表格或多工作表。 ")] = None,
    delegation_token: str | None = None,
) -> ArtifactGenerationResult:
    if not delegation_token:
        raise ValueError("缺少已验证的委派执行令牌")
    base_url = os.environ.get("AETHER_ADMIN_INTERNAL_URL", "http://aether-admin:8080").rstrip("/")
    payload = json.dumps({"title": title, "content": content, "format": format, "fileName": file_name, "document": document or {}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/api/internal/sandbox/requests", data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Aether-Delegation": delegation_token},
    )
    try:
        # 令牌只经内部服务头转发，产物渲染权限仍由管理端统一裁决。
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("message") or detail
        except json.JSONDecodeError:
            pass
        raise ValueError(f"产物执行申请失败: {detail}") from error
    except Exception as error:
        raise ValueError("产物执行申请失败") from error
    if body.get("code") not in (0, 200) or not body.get("data"):
        raise ValueError(body.get("message") or "产物执行申请失败")
    data = body["data"]
    return ArtifactGenerationResult(execution_id=data["executionId"], run_id=data["runId"], status=data["status"])
