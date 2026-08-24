import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app


class ConnectionExportTests(unittest.TestCase):
    @patch("routes.get_connection_status")
    def test_txt_export_formats_last_connection_session(self, get_connection_status):
        get_connection_status.return_value = {
            "output": [
                "[2026-08-24 16:00:00] Connected using TCP to 192.168.1.50:23",
                {
                    "time": "2026-08-24 16:00:01",
                    "direction": "TX",
                    "ascii": "power on\n",
                    "hex": "70 6F 77 65 72 20 6F 6E 0A",
                    "sent_as_hex": False,
                },
                {
                    "time": "2026-08-24 16:00:02",
                    "direction": "RX",
                    "ascii": "OK",
                    "hex": "4F 4B",
                },
            ]
        }

        response = app.test_client().get("/connection-test/export.txt")
        content = response.data.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment; filename=\"connection-session-", response.headers["Content-Disposition"])
        self.assertIn("Connected using TCP", content)
        self.assertIn("[2026-08-24 16:00:01] TX\r\npower on", content)
        self.assertIn("[2026-08-24 16:00:02] RX\r\nOK", content)

    @patch("routes.get_connection_status", return_value={"output": []})
    def test_txt_export_requires_a_connection_session(self, _get_connection_status):
        response = app.test_client().post("/connection-test/export.txt")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["success"])

    @patch("routes.get_connection_status", return_value={"output": ["Connected"]})
    def test_post_saves_connection_txt_in_downloads_folder(self, _get_connection_status):
        with tempfile.TemporaryDirectory() as temp_dir, patch("routes.DOWNLOADS_DIR", Path(temp_dir)):
            response = app.test_client().post("/connection-test/export.txt")
            data = response.get_json()
            saved_path = Path(data["path"])

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data["success"])
            self.assertTrue(saved_path.parent.samefile(temp_dir))
            self.assertIn("Connected", saved_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
