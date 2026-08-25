import socket
import struct
import unittest

import multicast_utils


def ipv4_packet(source, destination, protocol, payload):
    total_length = 20 + len(payload)
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total_length, 1, 0, 1, protocol, 0,
        socket.inet_aton(source), socket.inet_aton(destination),
    )
    return header + payload


class MulticastParsingTests(unittest.TestCase):
    def setUp(self):
        with multicast_utils._lock:
            multicast_utils._state.clear()
            multicast_utils._state.update(multicast_utils._fresh_state("Ethernet", "192.168.10.54", 7))

    def tearDown(self):
        with multicast_utils._lock:
            multicast_utils._state["running"] = False

    def test_parses_v2_query_and_querier(self):
        igmp = bytes([0x11, 100, 0, 0]) + socket.inet_aton("0.0.0.0")
        multicast_utils._process_ipv4_packet(
            ipv4_packet("192.168.10.1", "224.0.0.1", 2, igmp), timestamp=1000.0
        )
        with multicast_utils._lock:
            self.assertIn("192.168.10.1", multicast_utils._state["queriers"])
            self.assertIn("v2", multicast_utils._state["igmp_versions"])
            self.assertEqual(multicast_utils._state["igmp_counts"]["query"], 1)
            self.assertEqual(multicast_utils._state["igmp_counts"]["general_query"], 1)

    def test_parses_v3_report_groups(self):
        record = bytes([1, 0, 0, 0]) + socket.inet_aton("239.69.1.12")
        payload = bytes([0x22, 0, 0, 0, 0, 0, 0, 1]) + record
        version, groups, event = multicast_utils._igmp_version_and_groups(payload)
        self.assertEqual((version, groups, event), ("v3", ["239.69.1.12"], "report"))

    def test_join_output_is_scoped_to_interface(self):
        output = """
Interface 7: Ethernet
0 0 Yes 224.0.0.251
0 1 Yes 239.69.1.12
Interface 9: WiFi
0 1 Yes 239.69.1.13
"""
        self.assertEqual(
            multicast_utils._parse_join_output(output, 7),
            {"224.0.0.251", "239.69.1.12"},
        )

    def test_high_rate_unjoined_group_flags_flooding(self):
        packet = ipv4_packet("192.168.10.20", "239.69.1.12", 17, b"x" * 1200)
        for _ in range(600):
            multicast_utils._process_ipv4_packet(packet, timestamp=1000.0)
        with multicast_utils._lock:
            multicast_utils._state["started_at"] = 995.0
            multicast_utils._state["membership_checked_at"] = 10**12
            multicast_utils._state["membership_available"] = True
        original_time = multicast_utils.time.time
        multicast_utils.time.time = lambda: 1000.0
        try:
            status = multicast_utils.get_multicast_status()
        finally:
            multicast_utils.time.time = original_time
        self.assertTrue(status["groups"][0]["suspected_flood"])
        self.assertIn("snooping", {item["code"] for item in status["warnings"]})

    def test_elapsed_time_freezes_when_capture_stops(self):
        with multicast_utils._lock:
            multicast_utils._state["started_at"] = 1000.0
            multicast_utils._state["stopped_at"] = 1012.5
            multicast_utils._state["running"] = False
            multicast_utils._state["membership_checked_at"] = 10**12
        original_time = multicast_utils.time.time
        multicast_utils.time.time = lambda: 2000.0
        try:
            status = multicast_utils.get_multicast_status()
        finally:
            multicast_utils.time.time = original_time
        self.assertEqual(status["elapsed_seconds"], 12.5)

    def test_querier_age_and_rates_freeze_when_capture_stops(self):
        packet = ipv4_packet("192.168.10.20", "239.69.1.12", 17, b"x" * 200)
        for _ in range(20):
            multicast_utils._process_ipv4_packet(packet, timestamp=1011.0)
        with multicast_utils._lock:
            multicast_utils._state["started_at"] = 1000.0
            multicast_utils._state["stopped_at"] = 1012.5
            multicast_utils._state["running"] = False
            multicast_utils._state["membership_checked_at"] = 10**12
            multicast_utils._state["queriers"]["192.168.10.1"] = {
                "last_seen": 1010.0,
                "intervals": multicast_utils.deque([60.0], maxlen=5),
            }
        original_time = multicast_utils.time.time
        multicast_utils.time.time = lambda: 2000.0
        try:
            status = multicast_utils.get_multicast_status()
        finally:
            multicast_utils.time.time = original_time
        self.assertEqual(status["queriers"][0]["last_query_seconds"], 2.5)
        self.assertGreater(status["groups"][0]["packets_per_second"], 0)


if __name__ == "__main__":
    unittest.main()
