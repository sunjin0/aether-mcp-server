import importlib.util
from pathlib import Path


def load_renderer():
    path = Path(__file__).parents[1] / "sandbox-runner" / "generic_artifact.py"
    spec = importlib.util.spec_from_file_location("generic_artifact_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_recognizes_only_matching_leading_markdown_h1_as_duplicate_title():
    renderer = load_renderer()

    assert renderer.is_duplicate_title_heading("# 客户 A 续费风险评估报告", "客户 A 续费风险评估报告")
    assert not renderer.is_duplicate_title_heading("## 客户 A 续费风险评估报告", "客户 A 续费风险评估报告")
    assert not renderer.is_duplicate_title_heading("# 风险结论", "客户 A 续费风险评估报告")


def test_removes_only_identical_leading_html_h1():
    renderer = load_renderer()

    assert renderer.strip_duplicate_html_title("<h1>报告</h1><p>正文</p>", "报告") == "<p>正文</p>"
    assert renderer.strip_duplicate_html_title("<h1>其他标题</h1><p>正文</p>", "报告").startswith("<h1>其他标题</h1>")
