from http_test_support import authorized_client
import csv
import io
import ipaddress
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

        response = authorized_client(app).get("/ip-scan/export.csv")
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("attachment; filename=", response.headers["Content-Disposition"])
        self.assertEqual(rows[0], ["IP", "MAC", "Manufacturer", "Hostname", "Status"])
        self.assertEqual(rows[1][2], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[1][4], "This PC")
        self.assertEqual(rows[2][2], "Example, Inc.")
        self.assertEqual(rows[2][4], "Possible IP conflict")

    @patch("routes._save_download")
    def test_full_subnet_visible_export_and_limit(self, save_download):
        from pathlib import Path
        save_download.return_value = Path('/tmp/ip-scan.csv')
        results = [{"ip": str(ipaddress.IPv4Address(0xA9FE0001 + i))} for i in range(65534)]
        client = authorized_client(app)
        response = client.post('/ip-scan/export.csv', json={"results": results})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['count'], 65534)
        rows = list(csv.reader(io.StringIO(save_download.call_args.args[1].lstrip('\ufeff'))))
        self.assertEqual(len(rows), 65535)
        save_download.reset_mock()
        response = client.post('/ip-scan/export.csv', json={"results": results + [{}]})
        self.assertEqual(response.status_code, 400)
        save_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
