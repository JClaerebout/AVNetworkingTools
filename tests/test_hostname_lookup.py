import unittest
from unittest.mock import patch

import scan_utils


class HostnameLookupTests(unittest.TestCase):
    @patch("scan_utils.sys.platform", "win32")
    @patch("scan_utils.run_cmd", return_value=(0, "device.local.", ""))
    def test_reverse_dns_has_strict_timeout(self, run_cmd):
        self.assertEqual(scan_utils._reverse_dns("192.168.1.10"), "device.local")
        self.assertEqual(run_cmd.call_args.kwargs["timeout"], 2)

    @patch("scan_utils.sys.platform", "win32")
    @patch("scan_utils.run_cmd", return_value=(124, "", "timed out"))
    def test_netbios_has_strict_timeout(self, run_cmd):
        self.assertEqual(scan_utils._netbios_name("192.168.1.10"), "")
        run_cmd.assert_called_once_with(["nbtstat", "-A", "192.168.1.10"], timeout=2)


if __name__ == "__main__":
    unittest.main()
