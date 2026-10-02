"""Offline checks for native macOS adapters; never alter host configuration."""
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from platform_backend.macos import capture, nic, wifi
import scan_utils


class MacBackendTests(unittest.TestCase):
    def test_pcap_link_layers_feed_ipv4_parser(self):
        ip = b"\x45" + b"\x00" * 19
        ethernet = b"\x00" * 12 + b"\x08\x00" + ip
        vlan = b"\x00" * 12 + b"\x81\x00\x00\x01\x08\x00" + ip
        self.assertEqual(capture._ipv4_frame(ethernet, 1), ip)
        self.assertEqual(capture._ipv4_frame(vlan, 1), ip)
        self.assertIsNone(capture._ipv4_frame(b"\x00" * 12 + b"\x86\xdd" + ip, 1))
        self.assertEqual(capture._ipv4_frame(ip, 101), ip)

    def test_service_parser_preserves_service_order(self):
        output = """An asterisk (*) denotes that a network service is disabled.
(1) Wi-Fi
(Hardware Port: Wi-Fi, Device: en0)

(2) USB LAN
(Hardware Port: USB LAN, Device: en4)

(3) VPN
(Hardware Port: com.example.vpn, Device: )
"""
        with patch.object(nic, "_run", return_value=(0, output, "")):
            self.assertEqual(nic._services(), [(1, "Wi-Fi", "en0", False), (2, "USB LAN", "en4", False)])
            self.assertEqual(nic._ordered_services()[-1], (3, "VPN", "", False))

    def test_service_order_includes_device_less_services_and_preserves_spaced_names(self):
        services = [(1, "Wi-Fi", "en0", False),
                    (2, "USB 10/100/1000 LAN 2", "en18", False),
                    (3, "VPN", "", False)]
        with patch.object(nic, "_ordered_services", return_value=services), \
             patch.object(nic, "_all_service_names", return_value=[item[1] for item in services]), \
             patch.object(nic, "_apply", return_value=(True, "Applied")) as apply:
            self.assertTrue(nic.set_interface_metric(2, 1)[0])
        apply.assert_called_once_with([
            "-ordernetworkservices", "USB 10/100/1000 LAN 2", "Wi-Fi", "VPN"
        ])

    def test_service_order_rejects_incomplete_list_without_changes(self):
        with patch.object(nic, "_ordered_services", return_value=[(1, "Wi-Fi", "en0", False)]), \
             patch.object(nic, "_all_service_names", return_value=["Wi-Fi", "VPN"]), \
             patch.object(nic, "_apply") as apply:
            self.assertFalse(nic.set_interface_metric(1, 1)[0])
        apply.assert_not_called()

    def test_privileged_command_quotes_service_as_one_argument(self):
        with patch.object(nic, "run_cmd", return_value=(0, "", "")) as run:
            nic._run(["/usr/sbin/networksetup", "-setdhcp", "Wi-Fi; echo no"], privileged=True)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("'Wi-Fi; echo no'", command[2])

    def test_arp_cache_only_uses_selected_interface_and_unicast(self):
        output = """? (192.168.1.10) at a:1:b:2:c:3 on en0 ifscope [ethernet]
? (192.168.1.11) at 1:0:5e:0:0:fb on en0 ifscope [ethernet]
? (192.168.1.12) at a:1:b:2:c:4 on en4 ifscope [ethernet]
? (192.168.1.13) at (incomplete) on en0 [ethernet]
"""
        with patch.object(scan_utils, "run_cmd", return_value=(0, output, "")):
            self.assertEqual(scan_utils._macos_arp_entries("en0"),
                             {"192.168.1.10": "0A:01:0B:02:0C:03"})

    def test_dhcp_and_dns_try_without_authorization(self):
        with patch.object(nic, "_known", return_value=(True, "")), \
             patch.object(nic, "_remember"), \
             patch.object(nic, "_run", return_value=(0, "", "")) as run:
            self.assertTrue(nic.set_dhcp("Wi-Fi")[0])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(run.call_args.kwargs.get("privileged", False))
        script = run.call_args.args[0][2]
        self.assertIn("-setdhcp", script)
        self.assertIn("-setdnsservers", script)

    def test_dhcp_authorizes_only_after_privilege_error(self):
        with patch.object(nic, "_known", return_value=(True, "")), \
             patch.object(nic, "_remember"), \
             patch.object(nic, "_run", side_effect=[
                 (1, "", "** Error: Command requires admin privileges."),
                 (0, "", ""),
             ]) as run:
            self.assertTrue(nic.set_dhcp("Wi-Fi")[0])
        self.assertEqual(run.call_count, 2)
        self.assertFalse(run.call_args_list[0].kwargs.get("privileged", False))
        self.assertTrue(run.call_args_list[1].kwargs["privileged"])

    def test_renew_resets_ipv4_service_when_no_lease_exists(self):
        services = [(1, "USB LAN", "en18", False)]
        with patch.object(nic, "_services", return_value=services), \
             patch.object(nic, "_ipv4", return_value={"_dhcp": True, "IP address": "none"}), \
             patch.object(nic, "_apply", return_value=(True, "")) as apply, \
             patch.object(nic, "_apply_batch", return_value=(True, "")) as batch:
            self.assertTrue(nic.renew_dhcp("USB LAN")[0])
        apply.assert_called_once_with(["-setv4off", "USB LAN"])
        batch.assert_called_once_with([["-setdhcp", "USB LAN"], ["-setdnsservers", "USB LAN", "Empty"]])

    def test_renew_uses_scutil_when_lease_exists(self):
        services = [(1, "USB LAN", "en18", False)]
        with patch.object(nic, "_services", return_value=services), \
             patch.object(nic, "_ipv4", return_value={"_dhcp": True, "IP address": "192.168.1.50"}), \
             patch.object(nic, "_run", side_effect=[
                 (1, "", "permission denied"), (0, "", ""),
             ]) as run:
            self.assertTrue(nic.renew_dhcp("USB LAN")[0])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/sbin/scutil", "--renew", "en18"])
        self.assertFalse(run.call_args_list[0].kwargs.get("privileged", False))
        self.assertTrue(run.call_args_list[1].kwargs["privileged"])

    def test_configuration_error_does_not_trigger_password_prompt(self):
        with patch.object(nic, "_run", return_value=(1, "", "Invalid service")) as run:
            self.assertFalse(nic._apply(["-setdhcp", "Unknown"])[0])
        run.assert_called_once()

    def test_wifi_denied_location_has_actionable_error(self):
        fake = SimpleNamespace(
            CLLocationManager=SimpleNamespace(authorizationStatus=lambda: 2),
            kCLAuthorizationStatusNotDetermined=0,
            kCLAuthorizationStatusAuthorizedWhenInUse=4,
            kCLAuthorizationStatusAuthorizedAlways=3,
        )
        with patch.dict(sys.modules, {
            "CoreLocation": fake,
            "PyObjCTools": SimpleNamespace(AppHelper=Mock()),
        }):
            with self.assertRaisesRegex(RuntimeError, "System Settings"):
                wifi._check_location_permission()

    def test_inactive_link_is_disconnected_even_with_running_flag(self):
        output = "en7: flags=8b63<UP,RUNNING>\nether 00:11:22:33:44:55\n\tstatus: inactive\n"
        with patch.object(nic, "_run", return_value=(0, output, "")):
            self.assertEqual(nic._link("en7"), (False, "00:11:22:33:44:55"))

    def test_only_connected_macos_services_appear_as_nic_cards(self):
        services = [(1, "Wi-Fi", "en0", False), (2, "Old USB LAN", "en7", False),
                    (3, "Disabled LAN", "en8", True), (4, "VPN", "", False)]
        def link(device):
            return (device == "en0", "")
        with patch.object(nic, "_ordered_services", return_value=services), \
             patch.object(nic, "_link", side_effect=link), \
             patch.object(nic, "_ipv4", return_value={"_dhcp": True}) as ipv4, \
             patch.object(nic, "_dns", return_value=[]):
            cards = nic.get_nics()
        self.assertEqual([card["name"] for card in cards], ["Wi-Fi"])
        self.assertEqual(cards[0]["order_count"], 4)
        ipv4.assert_called_once_with("Wi-Fi")

    def test_wifi_scan_uses_cached_results_after_refresh_error(self):
        network = SimpleNamespace(
            wlanChannel=lambda: None, rssiValue=lambda: -60,
            ssid=lambda: "Office", bssid=lambda: "00:11:22:33:44:55",
            supportsSecurity_=lambda _: True,
        )
        interface = SimpleNamespace(
            scanForNetworksWithName_error_=lambda *_: (None, "scan denied"),
            cachedScanResults=lambda: [network], ssid=lambda: "Office",
        )
        fake = SimpleNamespace(
            CWWiFiClient=SimpleNamespace(sharedWiFiClient=lambda: SimpleNamespace(interface=lambda: interface)),
            kCWSecurityWPAPersonal=1,
        )
        with patch.dict(sys.modules, {"CoreWLAN": fake}), patch.object(wifi, "_check_location_permission"):
            with self.assertLogs(wifi.__name__, level="WARNING"):
                items, cached = wifi.scan()
        self.assertTrue(cached)
        self.assertEqual(items[0]["ssid"], "Office")

    def test_delete_route_is_gone(self):
        from app import app
        response = app.test_client().post(
            "/nics/delete-service", data={"_action_token": app.config["ACTION_TOKEN"]})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
