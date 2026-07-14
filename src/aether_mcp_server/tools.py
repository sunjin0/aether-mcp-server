from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, Field


class EchoResult(BaseModel):
    message: str = Field(description="与请求参数一致的原始消息。")


class CurrentTimeResult(BaseModel):
    timestamp: str = Field(description="当前 UTC 时间的 ISO 8601 字符串。")


def echo(
    message: Annotated[str, Field(description="需要原样返回的文本内容。")],
) -> EchoResult:
    """接收一段文本并原样返回。"""
    return EchoResult(message=message)


def current_time() -> CurrentTimeResult:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return CurrentTimeResult(timestamp=datetime.now(UTC).isoformat())
