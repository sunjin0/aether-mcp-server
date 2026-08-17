"""Visual-model enrichment for images that have already entered the RAG pipeline."""

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .tools import _resolve_admin_file_url


class RagImageChunk(BaseModel):
    """A searchable, non-authoritative semantic representation of one image."""

    chunk_type: Literal["image_description"] = Field(
        default="image_description", description="供 RAG 索引区分内容类型的固定值。"
    )
    text: str = Field(description="适合写入向量库的图片语义描述。")
    image_type: str = Field(description="模型判断的图片类型，例如 chart、diagram 或 photo。")
    confidence: float = Field(ge=0, le=1, description="视觉模型对描述的置信度。")
    page: int | None = Field(default=None, description="图片所在的文档页码（如果调用方提供）。")
    source_image: str = Field(description="原始图片 URL 或本地来源标识。")
    ocr_text: str | None = Field(default=None, description="关联的 OCR 文本，便于检索和溯源。")
    warnings: list[str] = Field(default_factory=list, description="结果使用限制或不确定性说明。")


class ImageEnhancementResult(BaseModel):
    status: Literal["completed", "unavailable"] = Field(description="视觉增强是否实际执行。")
    chunks: list[RagImageChunk] = Field(default_factory=list, description="可直接进入 RAG 索引的图片语义块。")
    metadata: dict[str, Any] = Field(default_factory=dict, description="模型、输入和处理状态等元数据。")


def _vision_settings() -> tuple[str, str, str] | None:
    base_url = os.getenv("AETHER_VISION_API_BASE_URL", "").rstrip("/")
    model = os.getenv("AETHER_VISION_MODEL", "").strip()
    api_key = os.getenv("AETHER_VISION_API_KEY", "").strip()
    if not base_url or not model or not api_key:
        return None
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    return endpoint, model, api_key


def _read_image(source: str) -> tuple[bytes, str]:
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        request = urllib.request.Request(source, headers={"User-Agent": "Aether-MCP/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get_content_type()
            return response.read(), content_type

    path = Path(source)
    if not path.is_file():
        raise ValueError("图片来源必须是可访问的 HTTP(S) URL 或本地图片文件")
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png"


def _response_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("视觉模型返回了不兼容的响应格式") from error
    if not isinstance(content, str):
        raise ValueError("视觉模型未返回文本内容")
    return content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def _build_chunk(source: str, page: int | None, ocr_text: str | None, content: str) -> RagImageChunk:
    try:
        analysis = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("视觉模型未返回要求的 JSON 描述") from error
    if not isinstance(analysis, dict) or not isinstance(analysis.get("description"), str):
        raise ValueError("视觉模型响应缺少 description")

    image_type = str(analysis.get("image_type") or "unknown")
    chart_summary = analysis.get("chart_summary")
    text = analysis["description"].strip()
    if isinstance(chart_summary, str) and chart_summary.strip():
        text += "\n\n图表摘要：" + chart_summary.strip()
    entities = analysis.get("key_entities")
    if isinstance(entities, list):
        entity_text = "、".join(str(entity).strip() for entity in entities if str(entity).strip())
        if entity_text:
            text += "\n\n关键实体：" + entity_text
    if not text:
        raise ValueError("视觉模型返回了空描述")

    confidence = analysis.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5
    warnings = [str(item) for item in analysis.get("warnings", []) if str(item).strip()]
    warnings.append("图片语义由视觉模型生成；精确数值应以原始表格、OCR 标注或源数据复核。")
    return RagImageChunk(
        text=text,
        image_type=image_type,
        confidence=confidence,
        page=page,
        source_image=source,
        ocr_text=ocr_text or None,
        warnings=list(dict.fromkeys(warnings)),
    )


def enhance_image(
    source: str,
    ocr_text: str | None = None,
    page: int | None = None,
) -> ImageEnhancementResult:
    """Describe an image through an OpenAI-compatible vision endpoint for RAG retrieval."""
    settings = _vision_settings()
    if settings is None:
        return ImageEnhancementResult(
            status="unavailable",
            metadata={
                "reason": "视觉模型未配置。请设置 AETHER_VISION_API_BASE_URL、AETHER_VISION_MODEL 和 AETHER_VISION_API_KEY。"
            },
        )
    endpoint, model, api_key = settings
    # Browser attachment URLs use localhost; rewrite them before the container fetches bytes.
    resolved_source = _resolve_admin_file_url(source)
    image, media_type = _read_image(resolved_source)
    if not media_type.startswith("image/"):
        raise ValueError("仅支持图片输入；请先将 PDF 页面渲染为图片")

    context = ocr_text.strip() if ocr_text else "（未提供 OCR 文本）"
    prompt = (
        "你正在为 RAG 创建图片语义块。只描述图中可见事实，不推测未显示的信息；"
        "图表数值仅在明确标注时引用，其他数值趋势必须说明为视觉估计。"
        "仅返回 JSON 对象，字段为 image_type、description、key_entities、chart_summary、confidence、warnings。"
        "description 用中文，适合向量检索；key_entities 是字符串数组；confidence 是 0 到 1。"
        "\n\n关联 OCR 文本：\n" + context
    )
    data_url = "data:" + media_type + ";base64," + base64.b64encode(image).decode("ascii")
    request_body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=request_body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError) as error:
        raise RuntimeError("视觉模型调用失败") from error
    return ImageEnhancementResult(
        status="completed",
        chunks=[_build_chunk(source, page, ocr_text, _response_content(response_payload))],
        metadata={"model": model, "source": source},
    )
