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

    @patch("scan_utils.lookup_manufacturer", return_value="Vendor")
    def test_monitor_new_devices_remain_new_until_monitor_stops(self, _vendor):
        self._set_completed_quick_scan()
        existing = scan_utils._scan_results[0]
        self.assertFalse(scan_utils._monitor_update_device(existing["ip"], existing["mac"], True))
        self.assertFalse(existing.get("monitor_new", False))
        self.assertTrue(scan_utils._monitor_update_device("192.168.1.3", "AA:BB:CC:DD:EE:FF", True))
        added = scan_utils._scan_results[-1]
        self.assertTrue(added["monitor_new"])
        scan_utils._monitor_update_device(added["ip"], added["mac"], True)
        self.assertTrue(added["monitor_new"])
        with patch.object(scan_utils, "_monitor_stop") as stop:
            stop.is_set.return_value = True
            with patch.object(scan_utils, "_monitor_running"), patch.object(scan_utils, "_monitor_paused"), patch.object(scan_utils, "_scan_message"):
                scan_utils._monitor_worker()
        self.assertFalse(added["monitor_new"])

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
            "mac": "00:BB:CC:DD:EE:FF",
            "manufacturer": "Example vendor",
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
        self.assertEqual(scan_utils._scan_results[0]["manufacturer"], "Example vendor")
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

    @patch("scan_utils._get_arp_entries", return_value={"10.0.0.25": "00:BB:CC:DD:EE:FF"})
    @patch("scan_utils._ping_host_reliable", return_value=True)
    @patch("scan_utils._arp_probe")
    def test_remote_device_uses_ping_fallback(self, arp_probe, ping_host, _arp_entries):
        alive, mac = scan_utils._discover_host(
            "192.168.0.10",
            "10.0.0.25",
            use_arp=False,
        )

        self.assertTrue(alive)
        self.assertEqual(mac, "00:BB:CC:DD:EE:FF")
        arp_probe.assert_not_called()
        ping_host.assert_called_once()

    @patch("scan_utils.sys.platform", "darwin")
    @patch("scan_utils.time.sleep")
    @patch("scan_utils._macos_arp_entries", side_effect=[{}, {"192.168.0.25": "00:BB:CC:DD:EE:FF"}])
    @patch("scan_utils._ping_host_reliable", return_value=True)
    def test_macos_discovery_waits_for_mac_before_reporting_host(self, _ping, arp_entries, sleep):
        self.assertEqual(
            scan_utils._discover_host("192.168.0.10", "192.168.0.25", True,
                                      device="en0"),
            (True, "00:BB:CC:DD:EE:FF"),
        )
        self.assertEqual(arp_entries.call_count, 2)
        sleep.assert_called_once_with(0.1)

    @patch("scan_utils.lookup_local_manufacturer", return_value="Example vendor")
    @patch("scan_utils.urllib.request.urlopen")
    @patch("scan_utils.sys.platform", "darwin")
    @patch("scan_utils._lookup_worker")
    @patch("scan_utils._macos_arp_entries", return_value={"192.168.0.2": "00:BB:CC:DD:EE:FF"})
    @patch("scan_utils._discover_host", return_value=(True, "00:BB:CC:DD:EE:FF"))
    @patch("scan_utils._find_nic", return_value=(True, {
        "ip": "192.168.0.1", "mac": "00:11:22:33:44:55",
        "network": "192.168.0.0/30", "device": "en0",
    }))
    def test_macos_quick_scan_has_manufacturer_without_details_or_network_lookup(
        self, _nic, discover, _arp, lookup, urlopen, local_manufacturer
    ):
        scan_utils._scan_results = []
        scan_utils._scan_stop.clear()
        scan_utils._scan_worker_impl("Ethernet", quick_scan=True)

        device = next(item for item in scan_utils._scan_results if item["ip"] == "192.168.0.2")
        self.assertEqual(device["mac"], "00:BB:CC:DD:EE:FF")
        self.assertEqual(device["manufacturer"], "Example vendor")
        self.assertEqual(device["hostname"], "-")
        self.assertFalse(scan_utils._lookup_running)
        self.assertTrue(scan_utils._last_scan_context["completed"])
        self.assertIn("Quick scan complete", scan_utils._scan_message)
        discover.assert_called_once()
        lookup.assert_not_called()
        urlopen.assert_not_called()
        local_manufacturer.assert_called()

    @patch("scan_utils.lookup_local_manufacturer", return_value="Example vendor")
    def test_later_arp_result_fills_missing_mac_and_manufacturer(self, _local):
        scan_utils._scan_results = []
        scan_utils._add_result({"ip": "192.168.0.25", "mac": "", "seen_macs": [],
                                "manufacturer": "Looking up...", "hostname": "Looking up..."})
        scan_utils._add_result({"ip": "192.168.0.25", "mac": "00:BB:CC:DD:EE:FF",
                                "seen_macs": ["00:BB:CC:DD:EE:FF"], "manufacturer": "-", "hostname": "-"})
        self.assertEqual(len(scan_utils._scan_results), 1)
        self.assertEqual(scan_utils._scan_results[0]["mac"], "00:BB:CC:DD:EE:FF")
        self.assertEqual(scan_utils._scan_results[0]["seen_macs"], ["00:BB:CC:DD:EE:FF"])
        self.assertEqual(scan_utils._scan_results[0]["manufacturer"], "Example vendor")

    @patch("scan_utils.lookup_local_manufacturer", side_effect=["First vendor", "Replacement vendor"])
    @patch("scan_utils.urllib.request.urlopen")
    def test_quick_monitor_sets_manufacturer_and_refreshes_changed_mac(self, urlopen, _local):
        scan_utils._scan_results = []
        scan_utils._monitor_log = []
        self.assertTrue(scan_utils._monitor_update_device("192.168.0.2", "00:11:22:33:44:55", True))
        self.assertEqual(scan_utils._scan_results[0]["manufacturer"], "First vendor")
        self.assertFalse(scan_utils._monitor_update_device("192.168.0.2", "00:11:22:33:44:66", True))
        self.assertEqual(scan_utils._scan_results[0]["manufacturer"], "Replacement vendor")
        self.assertEqual(scan_utils._scan_results[0]["hostname"], "-")
        urlopen.assert_not_called()

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
