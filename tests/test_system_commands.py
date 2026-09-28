import subprocess
import unittest
from unittest.mock import Mock, patch

import nic_utils
import system_utils
import wifi_utils


class CommandDeadlineTests(unittest.TestCase):
    @patch("system_utils.subprocess.run", side_effect=subprocess.TimeoutExpired(["tool"], 10))
    def test_default_command_timeout_is_bounded_and_reported(self, run):
        code, stdout, stderr = system_utils.run_cmd(["tool"])
        self.assertEqual((code, stdout), (124, ""))
        self.assertIn("10 seconds", stderr)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    @patch("system_utils.run_cmd", return_value=(1, "", "Access denied"))
    def test_powershell_has_own_deadline_and_preserves_stderr(self, run):
        self.assertEqual(system_utils.run_powershell("Get-Thing"), (1, "", "Access denied"))
        self.assertEqual(run.call_args.kwargs["timeout"], 8)

    @patch("system_utils.subprocess.run")
    def test_windows_output_uses_oem_encoding(self, run):
        run.return_value = Mock(returncode=1, stdout="", stderr="Accès refusé")
        with patch.object(system_utils.sys, "platform", "win32"):
            self.assertEqual(system_utils.run_cmd(["tool"])[2], "Accès refusé")
        self.assertEqual(run.call_args.kwargs["encoding"], "oem")


class WifiCommandTests(unittest.TestCase):
    @patch("wifi_utils.run_cmd", return_value=(1, "", "WLAN service unavailable"))
    def test_netsh_error_is_visible(self, run):
        with self.assertRaisesRegex(RuntimeError, "WLAN service unavailable"):
            wifi_utils._run_netsh()
        self.assertEqual(run.call_args.kwargs["timeout"], 8)

    @patch("wifi_utils.run_cmd", return_value=(124, "", "Command timed out after 8 seconds."))
    def test_netsh_timeout_is_visible(self, _run):
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            wifi_utils._run_netsh()

    def test_scan_keeps_failure_message_when_stopped(self):
        old_message, old_running = wifi_utils._wifi_message, wifi_utils._wifi_running
        wifi_utils._wifi_stop.clear()
        def fail_and_stop():
            wifi_utils._wifi_stop.set()
            raise RuntimeError("WLAN service unavailable")
        try:
            with patch.object(wifi_utils, "_run_netsh", side_effect=fail_and_stop):
                wifi_utils._scan_loop()
            self.assertIn("WLAN service unavailable", wifi_utils._wifi_message)
        finally:
            wifi_utils._wifi_message, wifi_utils._wifi_running = old_message, old_running
            wifi_utils._wifi_stop.clear()

    def test_dutch_and_french_wifi_labels(self):
        output = """SSID 1 : Atelier
Authenticatie : WPA2-Personal
Versleuteling : CCMP
BSSID 1 : 00:11:22:33:44:55
Signaal : 80%
Radiotype : 802.11ac
Kanaal : 6
SSID 2 : Salle
Authentification : WPA2-Personal
Chiffrement : CCMP
BSSID 1 : 00:11:22:33:44:66
Signal : 60%
Type de radio : 802.11ax
Canal : 36
"""
        result = wifi_utils._parse_netsh(output)
        self.assertEqual([(item["ssid"], item["signal_percent"], item["channel"]) for item in result],
                         [("Atelier", 80, "6"), ("Salle", 60, "36")])


class MetricCacheTests(unittest.TestCase):
    @patch("nic_utils.run_powershell", return_value=(0, '{"InterfaceIndex":12,"InterfaceMetric":25,"AutomaticMetric":1}', ""))
    def test_cached_metrics_avoid_repeated_powershell_and_invalidate_after_change(self, run):
        with nic_utils._metrics_cache_lock:
            nic_utils._metrics_cache["until"] = 0
        self.assertIn(12, nic_utils.get_interface_metrics(cached=True))
        self.assertIn(12, nic_utils.get_interface_metrics(cached=True))
        self.assertEqual(run.call_count, 1)
        self.assertTrue(nic_utils.set_interface_metric(12, "auto")[0])
        self.assertIn(12, nic_utils.get_interface_metrics(cached=True))
        self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
