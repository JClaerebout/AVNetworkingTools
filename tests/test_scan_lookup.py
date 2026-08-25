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

    @patch("scan_utils._ping_host_reliable", return_value=False)
    @patch("scan_utils._arp_probe", return_value="8C:16:45:E6:7D:E7")
    def test_on_link_device_is_discovered_when_ping_is_blocked(self, arp_probe, ping_host):
        alive, mac = scan_utils._discover_host(
            "192.168.0.10",
            "192.168.0.25",
            use_arp=True,
        )

        self.assertTrue(alive)
        self.assertEqual(mac, "8C:16:45:E6:7D:E7")
        arp_probe.assert_called_once_with("192.168.0.10", "192.168.0.25")
        ping_host.assert_not_called()

    @patch("scan_utils._get_arp_entries", return_value={"10.0.0.25": "AA:BB:CC:DD:EE:FF"})
    @patch("scan_utils._ping_host_reliable", return_value=True)
    @patch("scan_utils._arp_probe")
    def test_remote_device_uses_ping_fallback(self, arp_probe, ping_host, _arp_entries):
        alive, mac = scan_utils._discover_host(
            "192.168.0.10",
            "10.0.0.25",
            use_arp=False,
        )

        self.assertTrue(alive)
        self.assertEqual(mac, "AA:BB:CC:DD:EE:FF")
        arp_probe.assert_not_called()
        ping_host.assert_called_once()

    @patch("scan_utils._discover_host", return_value=(True, "8C:16:45:E6:7D:E7"))
    def test_monitor_uses_arp_capable_discovery_for_local_devices(self, discover_host):
        stop_event = scan_utils.threading.Event()

        discovered = scan_utils._monitor_probe_round(
            "192.168.0.10",
            ["192.168.0.10", "192.168.0.25"],
            scan_utils.ipaddress.ip_network("192.168.0.0/24"),
            stop_event,
        )

        self.assertEqual(discovered, {"192.168.0.25": "8C:16:45:E6:7D:E7"})
        discover_host.assert_called_once_with(
            "192.168.0.10",
            "192.168.0.25",
            True,
            2,
            stop_event,
        )


if __name__ == "__main__":
    unittest.main()
