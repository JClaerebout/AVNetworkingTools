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

    @patch("scan_utils.sys.platform", "win32")
    @patch(
        "scan_utils.run_cmd",
        return_value=(1, "Pinging DEVICE-25 [192.168.1.25] with 32 bytes of data:\nRequest timed out.", ""),
    )
    def test_windows_name_can_resolve_device_that_blocks_ping(self, run_cmd):
        self.assertEqual(scan_utils._windows_resolved_name("192.168.1.25"), "DEVICE-25")
        self.assertEqual(run_cmd.call_args.kwargs["timeout"], 2)

    def test_parse_snmp_sysname(self):
        response = bytes.fromhex(
            "302E02010104067075626C6963A221020400000001020100020100"
            "3013301106082B0601020101050004055044553031"
        )
        self.assertEqual(scan_utils._parse_snmp_sysname(response), "PDU01")

    @patch("scan_utils._snmp_name", return_value="PDU01")
    @patch("scan_utils._windows_resolved_name", return_value="")
    @patch("scan_utils._netbios_name", return_value="")
    @patch("scan_utils._reverse_dns", return_value="")
    def test_lookup_falls_back_to_snmp(self, _dns, _netbios, _windows, snmp):
        self.assertEqual(scan_utils.lookup_hostname("192.168.1.85", "192.168.1.10"), "PDU01")
        snmp.assert_called_once_with("192.168.1.85", "192.168.1.10")

    @patch("scan_utils._mdns_query")
    def test_mdns_discovery_prefers_device_hostname_over_service_label(self, mdns_query):
        service_type = "_spx-hmp._tcp.local"
        instance = "HMP350 - midden._spx-hmp._tcp.local"
        mdns_query.side_effect = [
            [("_services._dns-sd._udp.local", 12, service_type)],
            [
                (service_type, 12, instance),
                (instance, 33, "spx-hmp-001d50204116.local"),
                ("spx-hmp-001d50204116.local", 1, "192.168.0.143"),
            ],
        ]
        scan_utils._mdns_cache.update({"source_ip": "", "timestamp": 0.0, "names": {}})

        names = scan_utils._discover_mdns_names("192.168.0.165")

        self.assertEqual(names["192.168.0.143"], "spx-hmp-001d50204116")


if __name__ == "__main__":
    unittest.main()
