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


if __name__ == "__main__":
    unittest.main()
