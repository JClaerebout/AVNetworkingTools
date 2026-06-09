import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import history
from history import load_history, save_history_entry
from nic_utils import (
    clean_dns,
    normalize_dhcp_status,
    prefix_to_subnet,
    release_dhcp,
    renew_dhcp,
    sort_nics,
    subnet_to_prefix,
)
from wifi_utils import _analyze_conflicts, _recommend_24ghz_channel


class HelperTests(unittest.TestCase):
    def test_prefix_to_subnet(self):
        self.assertEqual(prefix_to_subnet(24), "255.255.255.0")
        self.assertEqual(prefix_to_subnet(23), "255.255.254.0")
        self.assertEqual(prefix_to_subnet(32), "255.255.255.255")
        self.assertEqual(prefix_to_subnet(0), "0.0.0.0")
        self.assertEqual(prefix_to_subnet(33), "")
        self.assertEqual(prefix_to_subnet(None), "")

    def test_subnet_to_prefix(self):
        self.assertEqual(subnet_to_prefix("255.255.255.0"), 24)
        self.assertEqual(subnet_to_prefix("255.255.254.0"), 23)
        self.assertEqual(subnet_to_prefix("255.255.255.255"), 32)
        self.assertEqual(subnet_to_prefix("0.0.0.0"), 0)
        self.assertIsNone(subnet_to_prefix("255.0.255.0"))
        self.assertIsNone(subnet_to_prefix("255.255.255.999"))

    def test_clean_dns(self):
        self.assertEqual(clean_dns("1.1.1.1, 8.8.8.8"), ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(clean_dns("1.1.1.1;8.8.8.8 9.9.9.9"), ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
        self.assertEqual(clean_dns(""), [])

    def test_normalize_dhcp_status(self):
        self.assertEqual(normalize_dhcp_status("Enabled"), "dhcp")
        self.assertEqual(normalize_dhcp_status("True"), "dhcp")
        self.assertEqual(normalize_dhcp_status("1"), "dhcp")
        self.assertEqual(normalize_dhcp_status(True), "dhcp")
        self.assertEqual(normalize_dhcp_status("Disabled"), "static")
        self.assertEqual(normalize_dhcp_status(False), "static")
        self.assertEqual(normalize_dhcp_status(["Disabled", "Enabled"]), "dhcp")

    def test_sort_nics_disconnected_last(self):
        nics = [
            {"name": "Z Adapter", "status": "disconnected"},
            {"name": "B Adapter", "status": "dhcp"},
            {"name": "A Adapter", "status": "static"},
        ]
        sorted_names = [nic["name"] for nic in sort_nics(nics)]
        self.assertEqual(sorted_names, ["A Adapter", "B Adapter", "Z Adapter"])

    @patch("nic_utils.is_interface_connected", return_value=False)
    def test_release_renew_refuse_disconnected_adapters(self, _mock_connected):
        success, message = release_dhcp("Ethernet")
        self.assertFalse(success)
        self.assertIn("disconnected", message.lower())

        success, message = renew_dhcp("Ethernet")
        self.assertFalse(success)
        self.assertIn("disconnected", message.lower())

    def test_history_reapplied_static_config_moves_to_top_without_duplicate(self):
        original_history_file = history.HISTORY_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                history.HISTORY_FILE = Path(tmp) / "nic_history.json"
                first = {
                    "timestamp": "2026-05-31 10:00:00",
                    "interface": "Ethernet",
                    "ip": "192.168.1.10",
                    "subnet": "255.255.255.0",
                    "gateway": "192.168.1.1",
                    "dns1": "1.1.1.1",
                    "dns2": "8.8.8.8",
                }
                second = {
                    "timestamp": "2026-05-31 10:05:00",
                    "interface": "Ethernet",
                    "ip": "192.168.1.20",
                    "subnet": "255.255.255.0",
                    "gateway": "192.168.1.1",
                    "dns1": "1.1.1.1",
                    "dns2": "8.8.8.8",
                }

                save_history_entry(first)
                save_history_entry(second)
                save_history_entry({**first, "timestamp": "2026-05-31 10:10:00"})

                saved = load_history()
                self.assertEqual(len(saved), 2)
                self.assertEqual(saved[0]["ip"], "192.168.1.10")
                self.assertEqual(saved[1]["ip"], "192.168.1.20")
        finally:
            history.HISTORY_FILE = original_history_file

    def test_wifi_conflict_analysis_flags_same_and_adjacent_channels(self):
        results = [
            {"ssid": "Main", "bssid": "00:00:00:00:00:01", "band": "2.4GHz", "channel": "6", "signal_percent": 80, "signal_dbm": -60},
            {"ssid": "Neighbor", "bssid": "00:00:00:00:00:02", "band": "2.4GHz", "channel": "6", "signal_percent": 65, "signal_dbm": -68},
            {"ssid": "Overlap", "bssid": "00:00:00:00:00:03", "band": "2.4GHz", "channel": "4", "signal_percent": 50, "signal_dbm": -75},
        ]

        conflicts = _analyze_conflicts(results)
        main = next(item for item in conflicts if item["ssid"] == "Main")
        overlap = next(item for item in conflicts if item["ssid"] == "Overlap")

        self.assertEqual(main["severity"], "danger")
        self.assertEqual(main["severity_label"], "Conflict")
        self.assertIn("other AP(s) on channel 6", main["reason"])
        self.assertEqual(overlap["severity"], "warn")
        self.assertIn("not 1, 6 or 11", overlap["reason"])

    def test_wifi_recommendation_prefers_least_crowded_24ghz_channel(self):
        results = [
            {"band": "2.4GHz", "channel": "1", "signal_percent": 85},
            {"band": "2.4GHz", "channel": "6", "signal_percent": 60},
            {"band": "5GHz", "channel": "36", "signal_percent": 90},
        ]

        recommendation = _recommend_24ghz_channel(results)

        self.assertEqual(recommendation["channel"], 11)
        self.assertIn("Recommended 2.4GHz channel: 11", recommendation["message"])


if __name__ == "__main__":
    unittest.main()
