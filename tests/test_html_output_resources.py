import sys
import unittest
from pathlib import Path


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils.output_sync import (  # noqa: E402
    discover_html_output_references,
    resolve_local_html_reference,
)


class HtmlOutputResourceTests(unittest.TestCase):
    def test_discovers_attributes_srcset_and_css_urls(self):
        html = b"""
        <html><head>
          <style>.hero { background: url("../shared/bg.png"); }</style>
        </head><body style="mask-image: url('masks/shape.svg')">
          <img src="images/plot%20one.png?size=large#detail">
          <script src="../shared/app.js"></script>
          <img srcset="small.png 1x, large.png 2x">
          <a href="/outputs/report/data.csv">Data</a>
        </body></html>
        """

        self.assertEqual(
            discover_html_output_references(
                html,
                "/outputs/report/index.html",
            ),
            {
                "/outputs/report/images/plot one.png",
                "/outputs/shared/app.js",
                "/outputs/report/small.png",
                "/outputs/report/large.png",
                "/outputs/shared/bg.png",
                "/outputs/report/masks/shape.svg",
                "/outputs/report/data.csv",
            },
        )

    def test_self_contained_and_external_resources_are_allowed(self):
        html = b"""
        <html><head>
          <style>.hero { background: url("data:image/png;base64,AAAA"); }</style>
          <link rel="stylesheet" href="https://example.com/site.css">
        </head><body>
          <img src="data:image/png;base64,AAAA">
          <script src="https://example.com/app.js"></script>
          <a href="#section">Jump</a>
        </body></html>
        """

        self.assertEqual(
            discover_html_output_references(
                html,
                "/outputs/report/index.html",
            ),
            set(),
        )

    def test_flags_server_routes_and_paths_outside_outputs(self):
        html = b"""
        <img src="../../etc/passwd">
        <img src="/api/v1/files/file-id/content">
        """

        self.assertEqual(
            discover_html_output_references(
                html,
                "/outputs/report/index.html",
            ),
            {
                "/etc/passwd",
                "/api/v1/files/file-id/content",
            },
        )

    def test_resolves_encoded_relative_reference(self):
        self.assertEqual(
            resolve_local_html_reference(
                "../shared/plot%20one.png?size=large#detail",
                "/outputs/report/index.html",
            ),
            (
                "/outputs/shared/plot one.png",
                "size=large",
                "detail",
            ),
        )


if __name__ == "__main__":
    unittest.main()
