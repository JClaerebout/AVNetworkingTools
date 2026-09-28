import io
import json
import unittest
from unittest.mock import Mock, patch

import app
import app_lifecycle


class StartupTests(unittest.TestCase):
    def test_health_identifies_exact_app_instance(self):
        first, second = app.create_app(), app.create_app()
        self.assertNotEqual(first.config["INSTANCE_ID"], second.config["INSTANCE_ID"])
        self.assertEqual(first.test_client().get("/_instance_health").json["instance_id"], first.config["INSTANCE_ID"])

    def test_wait_rejects_another_listener(self):
        thread = Mock(is_alive=Mock(return_value=True))
        response = io.BytesIO(json.dumps({"instance_id": "other"}).encode())
        with patch.object(app.urllib.request, "urlopen", return_value=response), patch.object(app.time, "sleep"), patch.object(app.time, "monotonic", side_effect=[0, 1, 2]):
            self.assertFalse(app.wait_for_flask("ours", thread, timeout=1))

    def test_busy_port_does_not_open_window(self):
        with patch.object(app, "create_desktop_server", side_effect=OSError("busy")), patch.object(app.webview, "create_window") as window:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                app.run_desktop()
            window.assert_not_called()

    def test_exclusive_listener_rejects_second_server(self):
        first = app.create_desktop_server(port=0)
        try:
            with self.assertRaises(OSError):
                app.create_desktop_server(port=int(first.effective_port))
        finally:
            first.close()

    def test_window_close_stops_tasks_and_server(self):
        server = Mock()
        with patch.object(app, "create_desktop_server", return_value=server), patch.object(app, "wait_for_flask", return_value=True), patch.object(app, "stop_background_tasks") as stop, patch.object(app, "start_manufacturer_database_update"), patch.object(app.webview, "create_window"), patch.object(app.webview, "start"):
            app.run_desktop()
        stop.assert_called_once()
        server.close.assert_called_once()


class ShutdownTests(unittest.TestCase):
    def test_each_task_stops_even_if_one_fails(self):
        with patch("scan_utils.stop_scan", side_effect=RuntimeError("failed")), patch("scan_utils.stop_monitor") as monitor, patch("ping_utils.stop_ping") as ping, patch("connection_utils.stop_connection") as connection, patch("script_utils.stop_script") as script, patch("wifi_utils.stop_wifi_scan") as wifi, patch("multicast_utils.stop_multicast_test") as capture, patch("app_lifecycle.logging.exception"):
            app_lifecycle.stop_background_tasks()
        for stop in (monitor, ping, connection, script, wifi, capture):
            stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
