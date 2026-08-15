import unittest
from unittest.mock import patch

from app import app


class PingExportTests(unittest.TestCase):
    @patch("routes.get_ping_status")
    def test_txt_export_contains_current_ping_output(self, get_ping_status):
        get_ping_status.return_value = {
            "running": False,
            "target": "",
            "output": [
                "Started continuous ping to 192.168.1.1 at 2026-08-15 14:00:00",
                "[2026-08-15 14:00:01] Reply from 192.168.1.1: bytes=32 time<1ms TTL=64",
                "Ping stopped at 2026-08-15 14:00:02",
            ],
            "history": ["192.168.1.1"],
        }

        response = app.test_client().get("/ping/export.txt")
        content = response.data.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/plain")
        self.assertIn("attachment; filename=\"ping-result-", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Started continuous ping to 192.168.1.1", content)
        self.assertIn("Reply from 192.168.1.1", content)
        self.assertTrue(content.endswith("\r\n"))

    @patch("routes.get_ping_status", return_value={"output": []})
    def test_txt_export_handles_empty_output(self, _get_ping_status):
        response = app.test_client().get("/ping/export.txt")

        self.assertEqual(response.data.decode("utf-8"), "No ping output available.\r\n")


if __name__ == "__main__":
    unittest.main()
