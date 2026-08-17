import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aether_mcp_server.image_enhancement import enhance_image


def test_enhance_image_returns_unavailable_without_vision_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AETHER_VISION_API_BASE_URL", raising=False)
    monkeypatch.delenv("AETHER_VISION_MODEL", raising=False)
    monkeypatch.delenv("AETHER_VISION_API_KEY", raising=False)

    result = enhance_image("image.png")

    assert result.status == "unavailable"
    assert result.chunks == []
    assert "视觉模型未配置" in result.metadata["reason"]


def test_enhance_image_builds_rag_chunk_from_vision_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(b"png-data")
    monkeypatch.setenv("AETHER_VISION_API_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("AETHER_VISION_MODEL", "vision-test")
    monkeypatch.setenv("AETHER_VISION_API_KEY", "secret")
    response = MagicMock()
    response.read.return_value = json.dumps({"choices": [{"message": {"content": json.dumps({
        "image_type": "chart",
        "description": "季度销售额柱状图。",
        "key_entities": ["华东", "Q3"],
        "chart_summary": "华东在第三季度最高。",
        "confidence": 0.85,
        "warnings": ["未标注的数值为视觉估计。"],
    })}}]}).encode()
    response.__enter__.return_value = response
    with patch("aether_mcp_server.image_enhancement.urllib.request.urlopen", return_value=response) as urlopen:
        result = enhance_image(str(image), ocr_text="销售额", page=3)

    assert result.status == "completed"
    assert result.metadata["model"] == "vision-test"
    assert result.chunks[0].chunk_type == "image_description"
    assert result.chunks[0].image_type == "chart"
    assert result.chunks[0].page == 3
    assert result.chunks[0].ocr_text == "销售额"
    assert "图表摘要：华东在第三季度最高。" in result.chunks[0].text
    assert result.chunks[0].confidence == 0.85
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://vision.example/v1/chat/completions"


def test_enhance_image_rejects_non_json_model_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image = tmp_path / "photo.png"
    image.write_bytes(b"png-data")
    monkeypatch.setenv("AETHER_VISION_API_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("AETHER_VISION_MODEL", "vision-test")
    monkeypatch.setenv("AETHER_VISION_API_KEY", "secret")
    response = MagicMock()
    response.read.return_value = b'{"choices": [{"message": {"content": "not json"}}]}'
    response.__enter__.return_value = response
    with patch("aether_mcp_server.image_enhancement.urllib.request.urlopen", return_value=response):
        with pytest.raises(ValueError, match="JSON"):
            enhance_image(str(image))


def test_enhance_image_rewrites_browser_attachment_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AETHER_VISION_API_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("AETHER_VISION_MODEL", "vision-test")
    monkeypatch.setenv("AETHER_VISION_API_KEY", "secret")
    monkeypatch.setenv("AETHER_ADMIN_INTERNAL_URL", "http://aether-admin:8080")
    response = MagicMock()
    response.read.return_value = json.dumps({"choices": [{"message": {"content": json.dumps({
        "image_type": "photo", "description": "产品照片", "confidence": 0.8,
    })}}]}).encode()
    response.__enter__.return_value = response
    image_response = MagicMock()
    image_response.headers.get_content_type.return_value = "image/png"
    image_response.read.return_value = b"png-data"
    image_response.__enter__.return_value = image_response
    with patch(
        "aether_mcp_server.image_enhancement.urllib.request.urlopen",
        side_effect=[image_response, response],
    ) as urlopen:
        result = enhance_image("http://localhost:8001/api/file/chat/preview?objectKey=chart.png")

    assert result.status == "completed"
    assert urlopen.call_args_list[0].args[0].full_url.startswith("http://aether-admin:8080/api/file/")
