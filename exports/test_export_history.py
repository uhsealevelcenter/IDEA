import base64
import json
from pathlib import Path
import tempfile
import unittest

from export_history import export_records, snapshot_sql


class ExportTests(unittest.TestCase):
    def test_preserves_records_and_escapes_active_content(self):
        conv = dict(id="conv", title="<script>title</script>", created_at="2026-01-01")
        empty = dict(id="empty", title=None, created_at="2026-01-02")
        text = '<script>alert(1)</script>\n\\path\t"quoted" café'
        message = dict(id="message", conversation_id="conv", role="USER",
                       created_at="2026-01-01", content=text, message_type="MESSAGE", message_format=None)
        image = dict(message, id="image", message_type="IMAGE", message_format="BASE64_PNG",
                     content=base64.b64encode(b"example image bytes").decode())
        records = [{"kind": "user", "data": {"full_name": "Test", "email": "test@example.com"}},
                   {"kind": "conversation", "data": conv},
                   {"kind": "conversation", "data": empty},
                   {"kind": "message", "data": message}, {"kind": "message", "data": image}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = export_records(records, root)
            self.assertEqual((manifest["conversations"], manifest["messages"], manifest["extracted_images"]), (2, 2, 1))
            self.assertEqual(json.loads((root / "conversations/conv.json").read_text())["messages"], [message, image])
            self.assertEqual(json.loads((root / "conversations/empty.json").read_text())["messages"], [])
            page = (root / "conversations/conv.html").read_text()
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertEqual((root / "images/image.png").read_bytes(), b"example image bytes")

    def test_unknown_account_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "No account"):
                export_records([], Path(temp))

    def test_email_is_encoded_not_interpolated(self):
        email = "x'; DROP TABLE message; --@example.com"
        sql = snapshot_sql(email)
        self.assertNotIn(email, sql)
        self.assertIn("REPEATABLE READ READ ONLY", sql)


if __name__ == "__main__":
    unittest.main()
