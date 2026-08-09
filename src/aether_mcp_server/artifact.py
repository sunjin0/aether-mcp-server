"""Managed artifact MCP action.  It forwards a verified delegation token only."""
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
    skill_code: Annotated[str, Field(description="当前 Agent 已安装且已发布的 Skill 编码。")],
    input: Annotated[dict[str, Any], Field(description="符合该 Skill 输入契约的 JSON 数据。")] = {},
    delegation_token: str | None = None,
) -> ArtifactGenerationResult:
    if not delegation_token:
        raise ValueError("缺少已验证的委派执行令牌")
    base_url = os.environ.get("AETHER_ADMIN_INTERNAL_URL", "http://aether-admin:8080").rstrip("/")
    payload = json.dumps({"skillCode": skill_code, "input": input}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url + "/api/internal/sandbox/requests", data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Aether-Delegation": delegation_token},
    )
    try:
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
