import unittest
from unittest.mock import MagicMock, patch

import scan_utils


class ScanLookupTests(unittest.TestCase):
    def setUp(self):
        self.original_context = dict(scan_utils._last_scan_context)
        self.original_results = scan_utils._scan_results
        self.original_scan_running = scan_utils._scan_running
        self.original_lookup_running = scan_utils._lookup_running
        self.original_large_scan = scan_utils._large_scan_quick_only
        self.original_monitor_log = scan_utils._monitor_log

    def tearDown(self):
        scan_utils._last_scan_context.clear()
        scan_utils._last_scan_context.update(self.original_context)
        scan_utils._scan_results = self.original_results
        scan_utils._scan_running = self.original_scan_running
        scan_utils._lookup_running = self.original_lookup_running
        scan_utils._large_scan_quick_only = self.original_large_scan
        scan_utils._monitor_log = self.original_monitor_log

    def _set_completed_quick_scan(self):
        scan_utils._last_scan_context.update({
            "interface": "Ethernet",
            "source_ip": "192.168.1.1",
            "network": "192.168.1.0/24",
            "hosts": ["192.168.1.1", "192.168.1.2"],
            "quick_scan": True,
            "completed": True,
        })
        scan_utils._scan_results = [{
            "ip": "192.168.1.2",
            "mac": "AA:BB:CC:DD:EE:FF",
            "manufacturer": "-",
            "hostname": "-",
            "missing": False,
            "is_local": False,
        }]
        scan_utils._scan_running = False
        scan_utils._lookup_running = False
        scan_utils._large_scan_quick_only = False

    @patch("scan_utils.threading.Thread")
    def test_lookup_button_path_enriches_existing_quick_scan(self, thread_class):
        self._set_completed_quick_scan()
        thread = MagicMock()
        thread_class.return_value = thread

        success, message = scan_utils.start_lookup()

        self.assertTrue(success)
        self.assertIn("started", message.lower())
        self.assertEqual(scan_utils._scan_results[0]["hostname"], "Looking up...")
        thread_class.assert_called_once_with(
            target=scan_utils._lookup_worker,
            args=(True,),
            daemon=True,
        )
        thread.start.assert_called_once_with()

    @patch("scan_utils.threading.Thread")
    def test_start_scan_always_clears_quick_scan_results(self, thread_class):
        self._set_completed_quick_scan()
        scan_utils._monitor_log = ["[2026-08-24 16:00:00] Previous monitoring event."]
        thread = MagicMock()
        thread_class.return_value = thread

        success, _message = scan_utils.start_scan("Ethernet", quick_scan=False)

        self.assertTrue(success)
        self.assertEqual(scan_utils._scan_results, [])
        self.assertEqual(scan_utils._monitor_log, [])
        self.assertFalse(scan_utils.get_scan_status()["monitor_log_available"])
        thread_class.assert_called_once_with(
            target=scan_utils._scan_worker,
            args=("Ethernet", "", False),
            daemon=True,
        )
        thread.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
