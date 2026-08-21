import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock


def load_renderer():
    path = Path(__file__).parents[1] / "sandbox-runner" / "generic_artifact.py"
    spec = importlib.util.spec_from_file_location("generic_artifact_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pdf_markdown_rendering_uses_content_without_an_extra_title(monkeypatch, tmp_path):
    renderer = load_renderer()
    html_renderer = Mock()
    monkeypatch.setitem(sys.modules, "weasyprint", type("WeasyPrint", (), {"HTML": html_renderer}))

    renderer.render_pdf("# 正文标题\n\n正文内容", tmp_path / "report.pdf")

    rendered_html = html_renderer.call_args.kwargs["string"]
    assert rendered_html.count("<h1>") == 1
    assert "<h1>正文标题</h1>" in rendered_html


def test_pdf_html_rendering_uses_content_without_an_extra_title(monkeypatch, tmp_path):
    renderer = load_renderer()
    html_renderer = Mock()
    monkeypatch.setitem(sys.modules, "weasyprint", type("WeasyPrint", (), {"HTML": html_renderer}))

    renderer.render_pdf("<h1>正文标题</h1><p>正文内容</p>", tmp_path / "report.pdf")

    rendered_html = html_renderer.call_args.kwargs["string"]
    assert rendered_html.count("<h1>") == 1
    assert "<h1>正文标题</h1>" in rendered_html
