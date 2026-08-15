import csv
import io
import unittest
from unittest.mock import patch

from app import app


class IpScanExportTests(unittest.TestCase):
    @patch("routes.get_scan_status")
    def test_csv_export_contains_scan_results_and_safe_values(self, get_scan_status):
        get_scan_status.return_value = {
            "results": [
                {
                    "ip": "192.168.1.10",
                    "mac": "00:11:22:33:44:55",
                    "manufacturer": '=HYPERLINK("bad")',
                    "hostname": "office-pc",
                    "is_local": True,
                },
                {
                    "ip": "192.168.1.20",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "manufacturer": "Example, Inc.",
                    "hostname": "printer",
                    "duplicate_ip": True,
                },
            ]
        }

        response = app.test_client().get("/ip-scan/export.csv")
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("attachment; filename=", response.headers["Content-Disposition"])
        self.assertEqual(rows[0], ["IP", "MAC", "Manufacturer", "Hostname", "Status"])
        self.assertEqual(rows[1][2], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[1][4], "This PC")
        self.assertEqual(rows[2][2], "Example, Inc.")
        self.assertEqual(rows[2][4], "Duplicate IP")


if __name__ == "__main__":
    unittest.main()
