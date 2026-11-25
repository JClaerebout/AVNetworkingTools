import csv
import io
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from http_test_support import authorized_client
from network_probes import ProbeReply, Route, _parse_ping6, _windows_reply, parse_ipv4_response, parse_macos_route
import ping_utils as diagnostics


def ipv4(payload, destination="192.0.2.10"):
    return b"\x45" + bytes(8) + bytes([socket.IPPROTO_ICMP]) + bytes(6) + socket.inet_aton(destination) + payload


class ProbeTests(unittest.TestCase):
    def test_echo_requires_matching_id_sequence_payload_and_destination(self):
        token, marker = b"1234", b"ABCDEFGH"
        echo = ipv4(b"\x00\x00\x00\x00" + token + marker)
        self.assertEqual(parse_ipv4_response(echo, "192.0.2.10", "192.0.2.10", token, marker), "reply")
        for packet, address in [(echo, "192.0.2.1"), (echo[:-1], "192.0.2.10"), (b"junk", "192.0.2.10")]:
            self.assertIsNone(parse_ipv4_response(packet, address, "192.0.2.10", token, marker))

    def test_route_icmp_error_requires_matching_original_destination_and_echo(self):
        token = struct.pack("!HH", 5, 2)
        request = ipv4(b"\x08" + bytes(3) + token)
        for icmp_type, status in [(11, "hop"), (3, "unreachable")]:
            with self.subTest(icmp_type=icmp_type):
                packet = ipv4(bytes([icmp_type]) + bytes(7) + request)
                self.assertEqual(parse_ipv4_response(packet, "192.0.2.1", "192.0.2.10", token, b"payload"), status)
                self.assertIsNone(parse_ipv4_response(packet, "192.0.2.1", "192.0.2.11", token, b"payload"))
                self.assertIsNone(parse_ipv4_response(packet, "192.0.2.1", "192.0.2.10", b"1234", b"payload"))

    def test_windows_status_and_localized_ipv6_ping(self):
        self.assertEqual(_windows_reply(11013, "192.0.2.1", 0), ProbeReply("hop", "192.0.2.1", 0))
        self.assertEqual(_windows_reply(11010).kind, "timeout")
        self.assertEqual(_windows_reply(11003).kind, "unreachable")
        with self.assertRaisesRegex(OSError, "11050"):
            _windows_reply(11050)
        self.assertEqual(_parse_ping6("Réponse de ::1 : temps<1 ms"), ProbeReply("reply", "::1", 1.0))
        self.assertEqual(_parse_ping6("16 bytes from ::1, icmp_seq=0 hlim=64 time=0.222 ms").ms, .222)
        self.assertEqual(_parse_ping6("Minimum = 0ms, Maximum = 0ms, Average = 0ms").kind, "timeout")

    def test_macos_route_gateway_and_reject(self):
        self.assertEqual(parse_macos_route("interface: en4\n gateway: 192.0.2.1\n flags: <UP,GATEWAY>"),
                         Route("en4", "192.0.2.1"))
        self.assertEqual(parse_macos_route("interface: en4\n flags: <UP,HOST>"), Route("en4"))
        with self.assertRaisesRegex(OSError, "No suitable"):
            parse_macos_route("interface: en4\n flags: <UP,REJECT>")

    def test_win_source_rejects_a_route_from_another_adapter(self):
        from network_probes import WindowsIcmp
        with patch("network_probes.ctypes.WinDLL", create=True), \
             patch("network_probes._windows_route", return_value=Route("4")):
            probe = WindowsIcmp("192.0.2.10", "192.0.2.3", "2")
            with self.assertRaisesRegex(OSError, "No probe was sent"):
                probe.send_probe(1, 1000, threading.Event())
            probe.send.assert_not_called()


class DiagnosticModelTests(unittest.TestCase):
    def test_statistics_include_zero_ms_and_loss(self):
        stats = diagnostics.Statistics()
        for latency in (0, None, 12, None):
            stats.record(latency)
        self.assertEqual(stats.snapshot(), dict(sent=4, received=2, last=None, avg=6, best=0, worst=12, loss=50))

    def test_route_timeout_preserves_identity_and_changes_reset_per_hop_totals(self):
        run = diagnostics.Diagnostics("192.0.2.10", diagnostics.Settings.from_request({"mode": "combined"}))
        self.assertFalse(run.record_hop(1, ProbeReply("hop", "192.0.2.1", 8)))
        self.assertTrue(run.record_hop(2, ProbeReply("reply", "192.0.2.10", 20)))
        run.record_hop(1, ProbeReply("timeout"))
        self.assertEqual(run.hops[1].address, "192.0.2.1")
        self.assertEqual(run.hops[1].stats.snapshot()["loss"], 50)
        self.assertEqual(run.route_changes, 0)
        run.record_hop(1, ProbeReply("hop", "192.0.2.2", 3))
        self.assertEqual(run.hops[1].stats.snapshot()["sent"], 1)
        self.assertEqual(run.route_changes, 1)
        run.record_hop(1, ProbeReply("reply", "192.0.2.10", 4))
        self.assertNotIn(2, run.hops)

    def test_explicit_and_automatic_sources_respect_subnets_and_overlap(self):
        primary = dict(value="3|10.10.100.156", ip="10.10.100.156", prefix=24, secondary=False,
                       name="Ethernet", interface="3", gateway="10.10.100.1")
        secondary = dict(primary, value="3|192.168.0.100", ip="192.168.0.100", secondary=True)
        self.assertIs(diagnostics.select_source("192.168.0.60", "auto", [primary, secondary]), secondary)
        self.assertIsNone(diagnostics.select_source("192.168.0.60", "auto", [secondary, dict(secondary)]))
        self.assertIs(diagnostics.select_source("192.168.0.60", primary["value"], [primary, secondary]), primary)
        with self.assertRaisesRegex(ValueError, "no longer available"):
            diagnostics.select_source("192.168.0.60", secondary["value"], [primary])
        note = diagnostics.route_context("192.168.0.60", Route("3"), secondary, "", [], "auto")
        self.assertIn("local subnet", note["note"])
        self.assertIn("secondary", note["note"])
        self.assertEqual(note["default_gateway"], primary["gateway"])

    def test_settings_bounds_and_ipv6_mode_error(self):
        for data in [{"timeout": True}, {"interval": 0}, {"max_hops": 65}, {"mode": []}]:
            with self.assertRaises(ValueError):
                diagnostics.Settings.from_request(data)
        with self.assertRaisesRegex(ValueError, "IPv4"):
            diagnostics._resolve_target("::1", diagnostics.Settings.from_request({"mode": "trace"}))
        with patch("socket.getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaisesRegex(ValueError, "Could not resolve"):
                diagnostics._resolve_target("missing.test", diagnostics.Settings.from_request({}))


class DiagnosticSessionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch.object(diagnostics, "PING_HISTORY_FILE", Path(folder.name) / "ping.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        diagnostics.clear_ping()
        self.addCleanup(diagnostics.clear_ping)
        self.client = authorized_client(create_app())

    def wait_for_stop(self):
        deadline = time.monotonic() + 3
        while diagnostics.get_ping_activity() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(diagnostics.get_ping_activity())

    def test_trace_updates_results_and_exports_without_restarting_a_saved_session(self):
        with patch("ping_utils.WindowsIcmp") as win, patch("ping_utils.MacIcmp") as mac:
            for backend in (win, mac):
                backend.return_value.route.return_value = Route("lo0")
                backend.return_value.__enter__.return_value.send_probe.side_effect = [
                    ProbeReply("timeout"), ProbeReply("hop", "192.0.2.1", 1.5),
                    ProbeReply("reply", "127.0.0.1", 2.0),
                ]
            response = self.client.post("/ping/start", json={"ip": "127.0.0.1", "mode": "trace"})
            self.assertTrue(response.json["success"])
            self.wait_for_stop()
        data = self.client.get("/ping/status").json
        self.assertEqual(data["reachability"], "Reached")
        self.assertEqual(data["hops"][0]["kind"], "timeout")
        self.assertEqual(data["hops"][1]["last"], 1.5)
        txt = self.client.get("/ping/export.txt").text
        self.assertIn("Mode: Traceroute", txt)
        self.assertIn("Hop 1: *", txt)
        report = list(csv.DictReader(io.StringIO(self.client.get("/ping/export.csv").text)))
        self.assertEqual(report[1]["Hop"], "1")
        self.assertEqual(report[2]["Responding IP"], "192.0.2.1")
        diagnostics.restore_ping_status({**data, "running": True}, replace=True)
        self.assertFalse(diagnostics.get_ping_status()["running"])
        self.assertEqual(diagnostics.get_ping_status()["hops"], data["hops"])
        self.assertTrue(self.client.post("/ping/clear").json["success"])
        self.assertFalse(diagnostics.get_ping_status()["output"])

    def test_stop_cancels_combined_without_a_new_probe_and_rejects_duplicate_start(self):
        probing = threading.Event()
        finished = threading.Event()

        def send(ttl, timeout_ms, cancelled):
            probing.set()
            if cancelled.wait(5):
                finished.set()
                return None
            return ProbeReply("timeout")

        with patch("ping_utils.WindowsIcmp") as win, patch("ping_utils.MacIcmp") as mac:
            for backend in (win, mac):
                backend.return_value.route.return_value = Route("lo0")
                backend.return_value.__enter__.return_value.send_probe.side_effect = send
            try:
                self.assertTrue(diagnostics.start_ping("127.0.0.1", {"mode": "combined"})[0])
                self.assertTrue(probing.wait(2))
                self.assertFalse(diagnostics.start_ping("127.0.0.1")[0])
                self.assertTrue(self.client.post("/ping/stop").json["success"])
                self.assertTrue(finished.wait(1))
            finally:
                diagnostics.stop_ping()
                self.wait_for_stop()
            self.assertEqual(sum(backend.return_value.__enter__.return_value.send_probe.call_count
                                 for backend in (win, mac)), 1)
