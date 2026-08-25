import unittest

from ping_utils import validate_target


class PingTargetValidationTests(unittest.TestCase):
    def test_accepts_ipv4_and_ipv6_addresses(self):
        self.assertEqual(validate_target("192.168.1.1"), (True, "192.168.1.1"))
        self.assertEqual(validate_target("2001:db8::1"), (True, "2001:db8::1"))

    def test_accepts_common_hostname_formats(self):
        self.assertEqual(validate_target("switch.local"), (True, "switch.local"))
        self.assertEqual(validate_target("AV-RACK-01"), (True, "AV-RACK-01"))
        self.assertEqual(validate_target("host.example.com."), (True, "host.example.com"))

    def test_rejects_malformed_or_option_like_hostnames(self):
        invalid_targets = (
            "-t",
            "host name",
            "host_name",
            ".example.com",
            "example..com",
            "example.com/path",
            "a" * 64 + ".local",
        )
        for target in invalid_targets:
            with self.subTest(target=target):
                valid, message = validate_target(target)
                self.assertFalse(valid)
                self.assertIn("IP address or hostname", message)

    def test_rejects_empty_target(self):
        self.assertEqual(validate_target("  "), (False, "IP address or hostname is required."))


if __name__ == "__main__":
    unittest.main()
