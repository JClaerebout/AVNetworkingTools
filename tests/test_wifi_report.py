import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from http_test_support import authorized_client
from app import app
from wifi_utils import _analyze_conflicts, _channel_relationship, _finalize_scan_items, _group_results, summarize_wifi_conflicts


class WifiReportTests(unittest.TestCase):
    def radio(self, ssid, bssid, channel, band="2.4GHz", width=""):
        return dict(ssid=ssid, bssid=bssid, channel=str(channel), band=band,
                    signal_percent=80, signal_dbm=-60, channel_width=width)

    def test_channel_boundaries_and_bands(self):
        a = self.radio("A", "a", 1)
        self.assertIsNone(_channel_relationship(a, self.radio("B", "b", 6)))
        self.assertEqual(_channel_relationship(a, self.radio("B", "b", 5)), "Adjacent overlap")
        self.assertIsNone(_channel_relationship(a, self.radio("B", "b", 1, "6GHz")))
        self.assertIsNone(_channel_relationship(self.radio("A", "a", 36, "5GHz"), self.radio("B", "b", 40, "5GHz")))
        self.assertEqual(_channel_relationship(self.radio("A", "a", 36, "5GHz", "80 MHz"), self.radio("B", "b", 44, "5GHz")), "Possible wide-channel overlap")

    def test_peers_and_group_assessment(self):
        radios = _finalize_scan_items([self.radio("Office", "a", 6), self.radio("Guest", "b", 4)])
        conflicts = _analyze_conflicts(radios)
        self.assertEqual(next(item for item in conflicts if item["ssid"] == "Office")["conflicting_networks"][0]["ssid"], "Guest")
        self.assertEqual(_group_results(radios)[0]["status"], "danger")
        self.assertEqual(len(_analyze_conflicts([radios[0], dict(radios[0])])), 1)

    def test_report_download_and_native_save(self):
        radios = _finalize_scan_items([self.radio("Office", "a", 6), self.radio("Guest", "b", 4)])
        status = dict(results=_group_results(radios), conflicts=_analyze_conflicts(radios), message="Scan stopped.")
        with patch("routes.get_wifi_status", return_value=status):
            response = authorized_client(app).get("/wifi-scan/export.txt")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(response.text.count("Adjacent overlap:"), 1)
            self.assertIn("Guest [b]", response.text)
            with tempfile.TemporaryDirectory() as directory, patch("export_utils.choose_save_path", side_effect=lambda name: Path(directory)/name):
                response = authorized_client(app).post("/wifi-scan/export.txt")
                self.assertEqual(response.status_code, 200)
                self.assertIn("Office", Path(response.json["path"]).read_text())

    def test_summary_keeps_each_pair_once_including_three_way_overlap(self):
        radios = _finalize_scan_items([self.radio("Office", "a", 1), self.radio("Guest", "b", 1), self.radio("Cast", "c", 1)])
        conflicts = _analyze_conflicts(radios)
        summary = summarize_wifi_conflicts(conflicts)
        pairs = [(item["bssid"], peer["bssid"]) for item in summary for peer in item["conflicting_networks"]]
        self.assertEqual(len(pairs), 3)
        self.assertEqual(len({frozenset(pair) for pair in pairs}), 3)
        self.assertEqual(sum(len(item["conflicting_networks"]) for item in conflicts), 6)

    def test_empty_report_rejected(self):
        with patch("routes.get_wifi_status", return_value={"results": []}):
            self.assertEqual(authorized_client(app).get("/wifi-scan/export.txt").status_code, 409)
