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
from wifi_utils import (
    _analyze_conflicts,
    _group_results,
    _parse_netsh,
    _recommend_24ghz_channel,
    _recommend_5ghz_channel,
)


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

        self.assertEqual(main["severity"], "warn")
        self.assertEqual(main["severity_label"], "Warning")
        self.assertIn("other AP radio(s) sharing channel 6", main["reason"])
        self.assertEqual(overlap["severity"], "danger")
        self.assertIn("not 1, 6 or 11", overlap["reason"])

    def test_wifi_conflict_analysis_does_not_merge_bssids_with_same_tail(self):
        results = [
            {"ssid": "Claerebout-Caus", "bssid": "d8:b3:70:b1:ad:4c", "band": "2.4GHz", "channel": "1", "signal_percent": 65, "signal_dbm": -68},
            {"ssid": "cc-devices", "bssid": "e2:b3:70:b1:ad:4c", "band": "2.4GHz", "channel": "1", "signal_percent": 62, "signal_dbm": -69},
            {"ssid": "Neighbor", "bssid": "00:11:22:33:44:55", "band": "2.4GHz", "channel": "1", "signal_percent": 72, "signal_dbm": -64},
        ]

        conflicts = _analyze_conflicts(results)
        caus = next(item for item in conflicts if item["ssid"] == "Claerebout-Caus")

        self.assertEqual(len(conflicts), 3)
        self.assertEqual(caus["severity"], "info")
        self.assertEqual(caus["severity_label"], "Weak")
        self.assertIn("2 other AP radio(s) sharing channel 1", caus["reason"])

    def test_wifi_recommendation_prefers_least_crowded_24ghz_channel(self):
        results = [
            {"band": "2.4GHz", "channel": "1", "signal_percent": 85},
            {"band": "2.4GHz", "channel": "6", "signal_percent": 60},
            {"band": "5GHz", "channel": "36", "signal_percent": 90},
        ]

        recommendation = _recommend_24ghz_channel(results)

        self.assertEqual(recommendation["channel"], 11)
        self.assertIn("Recommended 2.4GHz channel: 11", recommendation["message"])

    def test_wifi_recommendation_prefers_least_crowded_5ghz_channel(self):
        results = [
            {"band": "5GHz", "channel": "36", "signal_percent": 85},
            {"band": "5GHz", "channel": "44", "signal_percent": 70},
            {"band": "2.4GHz", "channel": "1", "signal_percent": 90},
        ]

        recommendation = _recommend_5ghz_channel(results)

        self.assertEqual(recommendation["channel"], 56)
        self.assertEqual(recommendation["band"], "U-NII-2A")
        self.assertEqual(recommendation["frequency_range"], "5250-5350 MHz")
        self.assertIn("Recommended 5GHz band: U-NII-2A", recommendation["message"])
        self.assertIn("channel 56", recommendation["message"])

    def test_wifi_parser_ignores_channel_utilization(self):
        output = """
SSID 1 : Office
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:55
         Signal             : 86%
         Radio type         : 802.11ac
         Channel            : 36
         Channel width      : 40 MHz
         Channel utilization : 37 (14 %)
"""

        results = _parse_netsh(output)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["channel"], "36")
        self.assertEqual(results[0]["band"], "5GHz")
        self.assertEqual(results[0]["channel_width"], "40 MHz")
        self.assertEqual(results[0]["channel_utilization_percent"], 14)
        self.assertEqual(results[0]["channel_load_percent"], 14)
        self.assertEqual(results[0]["channel_load_source"], "reported")
        self.assertIsInstance(results[0]["distance_m"], int)
        self.assertLessEqual(results[0]["distance_m"], 100)

    def test_wifi_parser_marks_high_channel_utilization(self):
        output = """
SSID 1 : Office
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:55
         Signal             : 70%
         Channel            : 6
         Channel utilization : 78%
"""

        results = _parse_netsh(output)

        self.assertEqual(results[0]["channel_utilization_percent"], 78)
        self.assertEqual(results[0]["channel_load_percent"], 78)
        self.assertEqual(results[0]["severity"], "danger")
        self.assertIn("high channel load", results[0]["reason"])

    def test_wifi_parser_reads_bss_load_channel_utilization(self):
        output = """
SSID 1 : Office
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 4                 : fa:e2:c6:e7:38:3e
         Signal             : 85%
         Radio type         : 802.11ax
         Band               : 2.4 GHz
         Channel            : 6
         Bss Load:
             Connected Stations:         0
             Channel Utilization:        121 (47 %)
             Medium Available Capacity:  31250 (1000000 us/s)
         QoS MSCS Supported    : 0
"""

        results = _parse_netsh(output)

        self.assertEqual(results[0]["band"], "2.4GHz")
        self.assertEqual(results[0]["channel_utilization_percent"], 47)
        self.assertEqual(results[0]["channel_load_percent"], 47)
        self.assertEqual(results[0]["channel_load_source"], "reported")
        self.assertEqual(results[0]["connected_stations"], 0)
        self.assertEqual(results[0]["medium_available_capacity"], "31250 (1000000 us/s)")

    def test_wifi_parser_estimates_channel_load_when_not_reported(self):
        output = """
SSID 1 : Office
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:55
         Signal             : 80%
         Channel            : 6
    BSSID 2                 : 00:11:22:33:44:56
         Signal             : 60%
         Channel            : 6
"""

        results = _parse_netsh(output)

        self.assertIsNone(results[0]["channel_utilization_percent"])
        self.assertEqual(results[0]["channel_load_source"], "estimated")
        self.assertIsNone(results[0]["channel_load_percent"])
        self.assertEqual(results[0]["channel_load_assessment"], "Shared")

    def test_wifi_parser_reuses_reported_load_for_same_channel(self):
        output = """
SSID 1 : Office
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:55
         Signal             : 80%
         Band               : 2.4 GHz
         Channel            : 6
         Bss Load:
             Connected Stations:         1
             Channel Utilization:        84 (32 %)
    BSSID 2                 : 00:11:22:33:44:56
         Signal             : 60%
         Band               : 2.4 GHz
         Channel            : 6
    BSSID 3                 : 00:11:22:33:44:57
         Signal             : 60%
         Band               : 5 GHz
         Channel            : 6
"""

        results = _parse_netsh(output)

        self.assertEqual(results[0]["channel_load_percent"], 32)
        self.assertEqual(results[0]["channel_load_source"], "reported")
        self.assertEqual(results[1]["channel_load_percent"], 32)
        self.assertEqual(results[1]["channel_load_source"], "channel_reported")
        self.assertNotEqual(results[2]["channel_load_source"], "channel_reported")

    def test_wifi_parser_resets_bssid_state_between_ssids(self):
        output = """
SSID 1 : First
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:55
         Signal             : 80%
         Channel            : 6
SSID 2 : Second
    Authentication          : WPA2-Personal
    Encryption              : CCMP
         Signal             : 20%
         Channel            : 11
    BSSID 1                 : 00:11:22:33:44:66
         Signal             : 30%
         Channel            : 1
"""

        results = _parse_netsh(output)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["ssid"], "First")
        self.assertEqual(results[0]["channel"], "6")
        self.assertEqual(results[0]["signal_percent"], 80)
        self.assertEqual(results[1]["ssid"], "Second")
        self.assertEqual(results[1]["channel"], "1")
        self.assertEqual(results[1]["signal_percent"], 30)

    def test_wifi_parser_handles_empty_hidden_ssid_header(self):
        output = """
SSID 1 : Phone Hotspot
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:55
         Signal             : 90%
         Channel            : 11
SSID 2 :
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:66
         Signal             : 80%
         Channel            : 6
SSID 3 : Neighbor
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 00:11:22:33:44:77
         Signal             : 70%
         Channel            : 1
"""

        grouped = _group_results(_parse_netsh(output))
        hotspot = next(group for group in grouped if group["ssid"] == "Phone Hotspot")

        self.assertEqual(hotspot["channel_summary"], "11")
        self.assertEqual(hotspot["radio_count"], 1)
        hidden = next(group for group in grouped if group["ssid"] == "Hidden network")
        self.assertEqual(hidden["channel_summary"], "6")
        self.assertEqual(hidden["radio_count"], 1)

    def test_wifi_group_reports_radio_count_and_estimated_load(self):
        results = [
            {"ssid": "Office", "bssid": "f4:e2:c6:e7:38:3e", "band": "2.4GHz", "channel": "6", "signal_percent": 80, "signal_dbm": -60, "severity": "ok", "severity_label": "OK", "reasons": [], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Shared", "channel_utilization_percent": None},
            {"ssid": "Office", "bssid": "f4:e2:c6:e7:38:3f", "band": "5GHz", "channel": "36", "signal_percent": 78, "signal_dbm": -61, "severity": "ok", "severity_label": "OK", "reasons": [], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Clear", "channel_utilization_percent": None},
            {"ssid": "Office", "bssid": "94:2a:6f:06:50:b6", "band": "2.4GHz", "channel": "6", "signal_percent": 70, "signal_dbm": -65, "severity": "ok", "severity_label": "OK", "reasons": [], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Shared", "channel_utilization_percent": None},
            {"ssid": "Office", "bssid": "94:2a:6f:06:50:b7", "band": "5GHz", "channel": "44", "signal_percent": 68, "signal_dbm": -66, "severity": "ok", "severity_label": "OK", "reasons": [], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Clear", "channel_utilization_percent": None},
        ]

        grouped = _group_results(results)

        self.assertEqual(grouped[0]["radio_count"], 4)
        self.assertIsNone(grouped[0]["max_channel_load_percent"])
        self.assertEqual(grouped[0]["channel_load_source"], "estimated")
        self.assertEqual(grouped[0]["channel_load_assessment"], "Shared")

    def test_wifi_group_only_marks_weak_when_all_radios_are_weak(self):
        results = [
            {"ssid": "Office", "bssid": "00:11:22:33:44:55", "band": "2.4GHz", "channel": "6", "signal_percent": 20, "signal_dbm": -90, "severity": "info", "severity_label": "Weak", "reasons": ["weak signal"], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Clear", "channel_utilization_percent": None},
            {"ssid": "Office", "bssid": "00:11:22:33:44:66", "band": "5GHz", "channel": "44", "signal_percent": 70, "signal_dbm": -65, "severity": "ok", "severity_label": "OK", "reasons": [], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Clear", "channel_utilization_percent": None},
        ]

        grouped = _group_results(results)

        self.assertEqual(grouped[0]["status"], "ok")
        self.assertEqual(grouped[0]["reason"], "OK")

    def test_wifi_group_marks_weak_when_every_radio_is_weak(self):
        results = [
            {"ssid": "Office", "bssid": "00:11:22:33:44:55", "band": "2.4GHz", "channel": "6", "signal_percent": 20, "signal_dbm": -90, "severity": "info", "severity_label": "Weak", "reasons": ["weak signal"], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Clear", "channel_utilization_percent": None},
            {"ssid": "Office", "bssid": "00:11:22:33:44:66", "band": "5GHz", "channel": "44", "signal_percent": 24, "signal_dbm": -88, "severity": "info", "severity_label": "Weak", "reasons": ["weak signal"], "channel_load_percent": None, "channel_load_source": "estimated", "channel_load_assessment": "Clear", "channel_utilization_percent": None},
        ]

        grouped = _group_results(results)

        self.assertEqual(grouped[0]["status"], "info")
        self.assertEqual(grouped[0]["status_label"], "Weak")


if __name__ == "__main__":
    unittest.main()
