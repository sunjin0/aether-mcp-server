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


def test_pdf_html_rendering_keeps_model_authored_css(monkeypatch, tmp_path):
    renderer = load_renderer()
    html_renderer = Mock()
    monkeypatch.setitem(sys.modules, "weasyprint", type("WeasyPrint", (), {"HTML": html_renderer}))

    renderer.render_pdf(
        '<style>.resume{display:flex;gap:12px;color:#0f172a}</style><main class="resume"><p>正文</p></main>',
        tmp_path / "resume.pdf",
    )

    rendered_html = html_renderer.call_args.kwargs["string"]
    assert ".resume{display:flex;gap:12px;color:#0f172a}" in rendered_html
    assert '<main class="resume"><p>正文</p></main>' in rendered_html


def test_pdf_html_rendering_decodes_json_escaped_html(monkeypatch, tmp_path):
    renderer = load_renderer()
    html_renderer = Mock()
    monkeypatch.setitem(sys.modules, "weasyprint", type("WeasyPrint", (), {"HTML": html_renderer}))

    renderer.render_pdf('<style>\\n.resume { color: #1f3a5f; }\\n</style>\\n<div class=\\"resume\\">正文</div>', tmp_path / "resume.pdf")

    rendered_html = html_renderer.call_args.kwargs["string"]
    assert ".resume{color:#1f3a5f}" in rendered_html
    assert '<div class="resume">正文</div>' in rendered_html


def test_pdf_html_rendering_keeps_head_style_and_print_layout(monkeypatch, tmp_path):
    renderer = load_renderer()
    html_renderer = Mock()
    monkeypatch.setitem(sys.modules, "weasyprint", type("WeasyPrint", (), {"HTML": html_renderer}))

    renderer.render_pdf(
        '<!DOCTYPE html><html><head><style>@page{size:A4;margin:0}'
        '@media print{.resume{padding:12mm 14mm}}.resume{min-height:297mm}'
        '</style></head><body><main class="resume">正文</main></body></html>',
        tmp_path / "resume.pdf",
    )

    rendered_html = html_renderer.call_args.kwargs["string"]
    assert "@page{size:A4;margin:0}" in rendered_html
    assert "@media print{.resume{padding:12mm 14mm}}" in rendered_html
    assert ".resume{min-height:297mm}" in rendered_html
