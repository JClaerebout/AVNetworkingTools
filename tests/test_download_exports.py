import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app


class DownloadExportTests(unittest.TestCase):
    @patch("routes.get_ping_status")
    def test_ping_post_saves_txt_in_downloads_folder(self, get_ping_status):
        get_ping_status.return_value = {"output": ["Reply from 192.168.1.1"]}

        with tempfile.TemporaryDirectory() as temp_dir, patch("routes.DOWNLOADS_DIR", Path(temp_dir)):
            response = app.test_client().post("/ping/export.txt")
            data = response.get_json()
            saved_path = Path(data["path"])

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data["success"])
            self.assertTrue(saved_path.parent.samefile(temp_dir))
            self.assertIn("Reply from 192.168.1.1", saved_path.read_text(encoding="utf-8"))

    def test_ip_scan_post_saves_only_visible_results(self):
        visible_results = [{
            "ip": "192.168.1.20",
            "mac": "AA:BB:CC:DD:EE:FF",
            "manufacturer": "Visible Vendor",
            "hostname": "visible-device",
            "missing": False,
        }]

        with tempfile.TemporaryDirectory() as temp_dir, patch("routes.DOWNLOADS_DIR", Path(temp_dir)):
            response = app.test_client().post(
                "/ip-scan/export.csv",
                json={"results": visible_results},
            )
            data = response.get_json()
            saved_path = Path(data["path"])
            content = saved_path.read_text(encoding="utf-8-sig")

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data["success"])
            self.assertEqual(data["count"], 1)
            self.assertTrue(saved_path.parent.samefile(temp_dir))
            self.assertIn("192.168.1.20", content)
            self.assertIn("Visible Vendor", content)

    def test_ip_scan_post_rejects_invalid_results(self):
        response = app.test_client().post("/ip-scan/export.csv", json={"results": "invalid"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])

    @patch("routes.get_monitor_log")
    def test_monitor_log_post_saves_txt_in_downloads_folder(self, get_monitor_log):
        get_monitor_log.return_value = [
            "[2026-08-24 16:00:00] Monitoring started.",
            "[2026-08-24 16:01:00] MISSING 192.168.1.20 (AA:BB:CC:DD:EE:FF)",
        ]

        with tempfile.TemporaryDirectory() as temp_dir, patch("routes.DOWNLOADS_DIR", Path(temp_dir)):
            response = app.test_client().post("/ip-scan/monitor/export.txt")
            data = response.get_json()
            saved_path = Path(data["path"])

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data["success"])
            self.assertEqual(data["count"], 2)
            self.assertTrue(saved_path.parent.samefile(temp_dir))
            self.assertIn("MISSING 192.168.1.20", saved_path.read_text(encoding="utf-8"))

    @patch("routes.get_monitor_log", return_value=[])
    def test_monitor_log_post_requires_a_monitoring_session(self, _get_monitor_log):
        response = app.test_client().post("/ip-scan/monitor/export.txt")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
