import unittest
from unittest.mock import MagicMock, patch

from app import app
from scan_utils import probe_web_services


class IpScanWebTests(unittest.TestCase):
    def test_ip_scan_context_menu_has_detail_copy_actions(self):
        response = app.test_client().get("/ip-scan")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="copyHostnameButton"', response.data)
        self.assertIn(b'id="copyManufacturerButton"', response.data)

    @patch("scan_utils.socket.socket")
    def test_probe_web_services_reports_only_open_ports(self, socket_class):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        socket_class.return_value = connection

        def connect(address):
            if address[1] == 443:
                raise OSError("closed")

        connection.connect.side_effect = connect

        services = probe_web_services("192.168.1.20", "192.168.1.10")

        self.assertEqual(services, ["http"])
        self.assertEqual(connection.settimeout.call_count, 2)
        self.assertEqual(connection.bind.call_count, 2)

    @patch("routes.webbrowser.open", return_value=True)
    @patch("routes.get_scan_status")
    def test_open_web_uses_detected_service(self, get_scan_status, browser_open):
        get_scan_status.return_value = {
            "results": [{"ip": "192.168.1.20", "web_services": ["https"]}]
        }

        response = app.test_client().post(
            "/ip-scan/open-web",
            json={"ip": "192.168.1.20", "scheme": "https"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        browser_open.assert_called_once_with("https://192.168.1.20/", new=2)

    @patch("routes.webbrowser.open")
    @patch("routes.get_scan_status")
    def test_open_web_rejects_service_not_found_by_scan(self, get_scan_status, browser_open):
        get_scan_status.return_value = {
            "results": [{"ip": "192.168.1.20", "web_services": []}]
        }

        response = app.test_client().post(
            "/ip-scan/open-web",
            json={"ip": "192.168.1.20", "scheme": "http"},
        )

        self.assertEqual(response.status_code, 409)
        browser_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
