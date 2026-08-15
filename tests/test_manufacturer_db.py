import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import manufacturer_db
import scan_utils


class ManufacturerDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.original_prefixes = manufacturer_db._prefixes

    def tearDown(self):
        manufacturer_db._prefixes = self.original_prefixes

    def test_longest_prefix_match_prefers_ma_s_then_ma_m_then_ma_l(self):
        manufacturer_db._prefixes = {
            24: {"001122": "Large"},
            28: {"0011223": "Medium"},
            36: {"001122334": "Small"},
        }

        self.assertEqual(manufacturer_db.lookup_local_manufacturer("00:11:22:33:44:55"), "Small")
        self.assertEqual(manufacturer_db.lookup_local_manufacturer("00:11:22:3F:44:55"), "Medium")
        self.assertEqual(manufacturer_db.lookup_local_manufacturer("00:11:22:A0:44:55"), "Large")

    def test_randomized_local_mac_has_no_manufacturer(self):
        manufacturer_db._prefixes = {24: {"021122": "Incorrect"}, 28: {}, 36: {}}
        self.assertEqual(manufacturer_db.lookup_local_manufacturer("02:11:22:33:44:55"), "")

    def test_parse_ieee_csv(self):
        text = "Registry,Assignment,Organization Name,Organization Address\nMA-L,001122,Example Corp,Address\n"
        self.assertEqual(manufacturer_db._parse_ieee_csv(text, 24), {"001122": "Example Corp"})

    @patch("scan_utils.urllib.request.urlopen")
    @patch("scan_utils.lookup_local_manufacturer", return_value="Local Vendor")
    def test_scan_lookup_uses_local_database_before_online_api(self, _local_lookup, urlopen):
        self.assertEqual(scan_utils.lookup_manufacturer("00:11:22:33:44:55"), "Local Vendor")
        urlopen.assert_not_called()

    @patch("scan_utils.urllib.request.urlopen")
    def test_randomized_mac_does_not_call_online_api(self, urlopen):
        self.assertEqual(scan_utils.lookup_manufacturer("02:11:22:33:44:55"), "Unknown")
        urlopen.assert_not_called()

    @patch("scan_utils.time.sleep")
    @patch("scan_utils.urllib.request.urlopen")
    @patch("scan_utils.lookup_local_manufacturer", return_value="")
    def test_online_fallback_is_disabled_by_default(self, _local_lookup, urlopen, sleep):
        self.assertEqual(scan_utils.lookup_manufacturer("00:11:22:33:44:55"), "Unknown")
        sleep.assert_not_called()
        urlopen.assert_not_called()

    @patch("manufacturer_db._download_csv")
    def test_failed_update_preserves_existing_database(self, download_csv):
        download_csv.side_effect = OSError("offline")
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "database.json"
            target.write_text("existing", encoding="utf-8")

            with self.assertRaises(OSError):
                manufacturer_db.update_manufacturer_database(target, force=True)

            self.assertEqual(target.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
