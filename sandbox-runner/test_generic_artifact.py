import importlib.util
from pathlib import Path
import unittest


_SPEC = importlib.util.spec_from_file_location("generic_artifact", Path(__file__).with_name("generic_artifact.py"))
generic_artifact = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(generic_artifact)


class GenericArtifactHtmlTest(unittest.TestCase):
    def test_detects_document_html_only_for_supported_document_tags(self):
        self.assertTrue(generic_artifact.looks_like_html("<h1>报告</h1><p>正文</p>"))
        self.assertTrue(generic_artifact.looks_like_html("<!doctype html><html><body>报告</body></html>"))
        self.assertFalse(generic_artifact.looks_like_html("# Markdown 标题"))

    def test_sanitizes_active_html_and_external_resource_css(self):
        rendered = generic_artifact.sanitize_html('<h1 onclick="alert(1)">报告</h1><script>secret()</script><img src="file:///etc/passwd"><p style="color:red;background:url(https://bad.example/x)">正文</p>')
        self.assertIn("<h1>报告</h1>", rendered)
        self.assertIn('<p style="color:red">正文</p>', rendered)
        self.assertNotIn("script", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn("file://", rendered)
        self.assertNotIn("url(", rendered)

    def test_preserves_model_html_layout_and_safe_css(self):
        rendered = generic_artifact.sanitize_html(
            '<style>.resume{display:grid;grid-template-columns:1fr 2fr;gap:12px;color:#123456}'
            '@page{margin:12mm}</style><main class="resume"><section>内容</section></main>'
        )
        self.assertIn('<style>.resume{display:grid;grid-template-columns:1fr 2fr;gap:12px;color:#123456}@page{margin:12mm}</style>', rendered)
        self.assertIn('<main class="resume"><section>内容</section></main>', rendered)

    def test_preserves_style_from_complete_html_document_head(self):
        rendered = generic_artifact.sanitize_html(
            '<!DOCTYPE html><html><head><meta charset="UTF-8"><style>'
            '@page{size:A4;margin:0}@media print{.resume{padding:12mm 14mm}}'
            '*{box-sizing:border-box}.resume{min-height:297mm}'
            '</style></head><body><main class="resume">内容</main></body></html>'
        )
        self.assertIn('<head><style>@page{size:A4;margin:0}@media print{.resume{padding:12mm 14mm}}*{box-sizing:border-box}.resume{min-height:297mm}</style></head>', rendered)
        self.assertIn('<main class="resume">内容</main>', rendered)

    def test_infers_resume_pdf_name_from_title_and_intent(self):
        self.assertEqual(
            generic_artifact.inferred_pdf_name("孙进", "求职意向：AI应用开发工程师（优先）／后端开发工程师"),
            "孙进-简历-AI应用开发工程师",
        )

    def test_decodes_json_escaped_html_before_css_sanitization(self):
        rendered = generic_artifact.sanitize_html(
            '<style>\\n.resume { display: flex; color: #1f3a5f; }\\n</style>\\n<div class=\\"resume\\">内容</div>'
        )
        self.assertIn('.resume{display:flex;color:#1f3a5f}', rendered)
        self.assertIn('<div class="resume">内容</div>', rendered)


if __name__ == "__main__":
    unittest.main()
