from http_test_support import authorized_client
"""Offline byte fixtures and mocked Windows capture; never opens a live socket."""
import json
import random
import socket
import struct
import threading
import unittest
from collections import deque
from unittest.mock import Mock, patch

import analyzer_packets as packets
import analyzer_state
import multicast_utils as capture
from analyzer_diagnostics import evaluate
from analyzer_state import Analyzer, RTPStats, rates, record_rate


def ipv4(payload=b"", protocol=17, source="10.0.0.1", group="239.1.1.1", dscp=46, ecn=3, ttl=32, options=b"", fragment=0):
    return struct.pack("!BBHHHBBH4s4s", 0x40 | (5 + len(options) // 4),
                       dscp << 2 | ecn, 20 + len(options) + len(payload), 0, fragment, ttl,
                       protocol, 0, socket.inet_aton(source), socket.inet_aton(group)) + options + payload


def udp(payload=b"media", source=5004, destination=5004):
    return struct.pack("!HHHH", source, destination, 8 + len(payload), 0) + payload


def ptp(kind=11, domain=0, clock=1, sequence=7, interval=1):
    data = bytearray(packets.PTP_MIN_LENGTH[kind])
    data[0:2] = bytes([kind, 2])
    data[2:4] = len(data).to_bytes(2, "big")
    data[4] = domain
    data[20:28] = clock.to_bytes(8, "big")
    data[28:30] = (2).to_bytes(2, "big")
    data[30:32] = sequence.to_bytes(2, "big")
    data[33] = interval & 255
    return bytes(data)


def rtp(seq=1, timestamp=1000, ssrc=123, payload_type=96):
    return struct.pack("!BBHII", 0x80, payload_type, seq, timestamp, ssrc) + b"media"


def igmp(kind=0x11, group="0.0.0.0", response=100):
    return bytes([kind, response, 0, 0]) + socket.inet_aton(group)


class PacketTests(unittest.TestCase):
    def test_ipv4_options_ttl_dscp_ecn_and_length(self):
        raw = ipv4(udp(), options=b"\x01" * 4)
        ip = packets.parse_ipv4(raw + b"ignored padding")
        self.assertEqual((ip["source"], ip["group"], ip["ttl"], ip["dscp"], ip["ecn"]),
                         ("10.0.0.1", "239.1.1.1", 32, 46, 3))
        self.assertEqual(ip["bytes"], len(raw))
        self.assertEqual(packets.parse_udp(ip["payload"])["destination_port"], 5004)

    def test_all_dscp_ecn_values(self):
        for dscp in range(64):
            for ecn in range(4):
                ip = packets.parse_ipv4(ipv4(dscp=dscp, ecn=ecn))
                self.assertEqual((ip["dscp"], ip["ecn"]), (dscp, ecn))

    def test_dscp_classes(self):
        expected = {0:"CS0 / Best Effort", 8:"CS1", 10:"AF11", 12:"AF12", 14:"AF13",
                    16:"CS2", 18:"AF21", 20:"AF22", 22:"AF23", 24:"CS3", 26:"AF31",
                    28:"AF32", 30:"AF33", 32:"CS4", 34:"AF41", 36:"AF42", 38:"AF43",
                    40:"CS5", 46:"EF", 48:"CS6", 56:"CS7", 63:"DSCP 63"}
        for value, label in expected.items():
            self.assertEqual(packets.dscp_class(value), label)

    def test_ipv4_rejects_truncation_and_bad_lengths(self):
        raw = ipv4(udp())
        for length in range(len(raw)):
            self.assertIsNone(packets.parse_ipv4(raw[:length]))
        for offset, value in ((0, 0x65), (0, 0x44), (3, 10), (0, 0x4f)):
            bad = bytearray(raw); bad[offset] = value
            self.assertIsNone(packets.parse_ipv4(bad))

    def test_udp_ports_and_truncation(self):
        raw = udp(source=1234, destination=5678)
        parsed = packets.parse_udp(raw)
        self.assertEqual((parsed["source_port"], parsed["destination_port"]), (1234, 5678))
        for length in range(len(raw)):
            self.assertIsNone(packets.parse_udp(raw[:length]))
        self.assertIsNone(packets.parse_udp(b"\0" * 8))

    def test_stream_key_separates_sources_ports_and_protocols(self):
        base = packets.parse_ipv4(ipv4())
        keys = {packets.stream_key(base), packets.stream_key({**base, "protocol": 2}),
                packets.stream_key({**base, "source": "10.0.0.2"})}
        for source, destination in ((1, 2), (2, 2), (1, 3)):
            keys.add(packets.stream_key(base, packets.parse_udp(udp(source=source, destination=destination))))
        self.assertEqual(len(keys), 6)

    def test_igmp_versions_and_events(self):
        self.assertEqual(packets.parse_igmp(igmp(response=0)), ("v1", [], "query"))
        self.assertEqual(packets.parse_igmp(igmp()), ("v2", [], "query"))
        self.assertEqual(packets.parse_igmp(igmp() + b"\x02\x7d\0\0"), ("v3", [], "query"))
        for kind, version, event in ((0x12,"v1","report"),(0x16,"v2","report"),(0x17,"v2","leave")):
            self.assertEqual(packets.parse_igmp(igmp(kind, "239.1.1.1")), (version,["239.1.1.1"],event))

    def test_igmp_v3_records_with_sources_aux_and_truncation(self):
        record = b"\x01\x01\0\x01" + socket.inet_aton("239.1.1.2") + b"\x0a\0\0\x01" + b"aux!"
        raw = b"\x22\0\0\0\0\0\0\x02" + record * 2
        self.assertEqual(packets.parse_igmp(raw), ("v3",["239.1.1.2"] * 2,"report"))
        for length in range(len(raw)):
            self.assertEqual(packets.parse_igmp(raw[:length])[2], "invalid")

    def test_igmp_truncated_query_source_list(self):
        raw = igmp() + b"\x02\x7d\0\x01" + b"\x0a\0\0\x01"
        self.assertEqual(packets.parse_igmp(raw)[0], "v3")
        for length in range(9, len(raw)):
            self.assertEqual(packets.parse_igmp(raw[:length])[2], "invalid")

    def test_igmp_invalid_group_addresses(self):
        for kind in (0x11,0x12,0x16,0x17):
            self.assertEqual(packets.parse_igmp(igmp(kind,"10.0.0.1"))[2],"invalid")

    def test_ptp_fields_and_all_message_types(self):
        for kind, name in packets.PTP_TYPES.items():
            parsed = packets.parse_ptp(ptp(kind, domain=42, sequence=65535, interval=-3))
            self.assertEqual(parsed, {"version":2,"message_type":name,"domain":42,"sequence_id":65535,
                                     "clock_identity":"00:00:00:00:00:00:00:01","source_port_number":2,"log_message_interval":-3})
        self.assertIsNone(packets.parse_ptp(ptp(interval=127))["log_message_interval"])

    def test_ptp_rejects_truncation_length_and_version(self):
        for kind in packets.PTP_TYPES:
            raw = ptp(kind)
            for length in range(len(raw)):
                self.assertIsNone(packets.parse_ptp(raw[:length]))
        for offset, value in ((1,1),(0,7),(3,255),(3,20)):
            raw = bytearray(ptp()); raw[offset] = value
            self.assertIsNone(packets.parse_ptp(raw))

    def test_rtp_fields_and_rejection(self):
        self.assertEqual(packets.parse_rtp(rtp()), {"payload_type":96,"sequence_number":1,"rtp_timestamp":1000,"ssrc":123})
        for raw in (b"x" * 30, rtp()[:12], b"\x80\xc8" + rtp()[2:], b"\x40" + rtp()[1:], rtp(payload_type=72)):
            self.assertIsNone(packets.parse_rtp(raw))

    def test_rtp_csrc_extension_and_padding(self):
        header = bytearray(rtp()[:12]); header[0] = 0xb1
        raw = bytes(header) + b"csrc" + b"\x10\0\0\x01" + b"ext!" + b"media" + b"\0\0\0\x04"
        self.assertIsNotNone(packets.parse_rtp(raw))
        for raw in (raw[:15], raw[:19], raw[:-1] + b"\0", raw[:-1] + b"\xff"):
            self.assertIsNone(packets.parse_rtp(raw))

    def test_deterministic_random_bytes_never_raise(self):
        rng = random.Random(1729)
        for _ in range(500):
            raw = rng.randbytes(rng.randrange(200))
            for parser in (packets.parse_ipv4, packets.parse_udp, packets.parse_igmp, packets.parse_ptp, packets.parse_rtp):
                parser(raw)


class StateTests(unittest.TestCase):
    def test_rates_and_expiry(self):
        item = {"packets":0,"bytes":0,"buckets":deque(maxlen=5)}
        for t in range(100, 105):
            record_rate(item, 1000, t)
        self.assertEqual(rates(item, 104, 99), (1, .008))
        self.assertEqual(rates(item, 109, 99), (0, 0))
        self.assertEqual((item["packets"],item["bytes"]), (5,5000))

    def test_rtp_wrap_loss_duplicate_repair(self):
        stats = RTPStats()
        for i, seq in enumerate((65534, 65535, 1, 1, 0, 2)):
            stats.record(packets.parse_rtp(rtp(seq)), 100 + i * .001)
        self.assertTrue(stats.confirmed)
        self.assertEqual(stats.data["estimated_missing_packets"], 0)
        self.assertEqual(stats.data["duplicate_packets"], 1)
        self.assertEqual(stats.data["out_of_order_packets"], 1)
        self.assertEqual(stats.data["largest_packet_gap"], 1)
        self.assertEqual(stats.highest, 65538)

    def test_rtp_loss_and_jitter_are_estimates(self):
        stats = RTPStats()
        for seq, now in ((1,100),(2,100.01),(5,100.03)):
            stats.record(packets.parse_rtp(rtp(seq)),now)
        self.assertEqual(stats.data["estimated_missing_packets"], 2)
        self.assertAlmostEqual(stats.data["estimated_network_jitter_ms"], .625)
        self.assertAlmostEqual(stats.data["largest_arrival_gap_ms"], 20)

    def test_rtp_large_jump_is_discontinuity(self):
        stats = RTPStats()
        for seq in (1,2,10000):
            stats.record(packets.parse_rtp(rtp(seq)),100)
        self.assertEqual(stats.data["discontinuities"], 1)
        self.assertEqual(stats.data["estimated_missing_packets"], 0)

    def test_rtp_history_bounded(self):
        stats = RTPStats()
        for seq in range(10000):
            stats.record(packets.parse_rtp(rtp(seq)),100)
        self.assertLessEqual(len(stats.seen),4096)
        self.assertLessEqual(len(stats.missing),2048)

    def test_unrelated_valid_headers_not_confirmed(self):
        stats = RTPStats()
        for seq in (1,10000,20000):
            stats.record(packets.parse_rtp(rtp(seq)),100)
        self.assertFalse(stats.confirmed)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original = capture._state.copy()
        with capture._lock:
            capture._state.clear()
            capture._state.update(capture._fresh_state("Ethernet","10.0.0.5",7))
            capture._state.update(started_at=100, membership_checked_at=10**12, membership_available=True)
        capture._stop_event.clear()

    def tearDown(self):
        capture._state.clear(); capture._state.update(self.original)
        capture._stop_event.clear()

    def status(self, now=105):
        with patch.object(capture.time,"time",return_value=now):
            return capture.get_multicast_status()

    def send(self, payload=None, now=101, **kwargs):
        capture._process_ipv4_packet(ipv4(udp() if payload is None else payload, **kwargs), now)

    def test_stream_metadata_mixed_markings_and_json_copy(self):
        self.send(dscp=0, ttl=1)
        self.send(dscp=46, ttl=32, now=102)
        status = self.status()
        row = status["streams"][0]
        self.assertEqual(row["packets"],2)
        self.assertEqual(row["bytes"],66)
        self.assertEqual(row["average_packet_size"],33)
        self.assertTrue(row["mixed_dscp"] and row["mixed_ttl"])
        self.assertEqual(row["dscp_changes"],1)
        json.dumps(status, allow_nan=False)
        row["dscp_history"][0]["value"] = 99
        self.assertEqual(self.status()["streams"][0]["dscp_history"][0]["value"],0)
        warnings = {w["code"]:w for w in status["warnings"]}
        self.assertEqual(warnings["best_effort"]["severity"],"information")
        self.assertIn("mixed_dscp",warnings)

    def test_inactive_streams_and_rates_expire(self):
        self.send()
        status = self.status(120)
        self.assertEqual(status["active_streams"],0)
        self.assertEqual(status["total_mbps"],0)
        self.assertEqual(status["streams"][0]["status"],"Inactive")

    def test_protocols_conservative_and_rtp_per_ssrc(self):
        self.send(udp(rtp(1)),now=101)
        self.assertEqual(self.status()["streams"][0]["possible_protocol"],"Unknown multicast")
        self.send(udp(rtp(2)),now=102)
        self.send(udp(rtp(100,ssrc=999)),now=103)
        self.send(udp(rtp(101,ssrc=999)),now=104)
        row = self.status()["streams"][0]
        self.assertEqual(row["possible_protocol"],"Possible RTP")
        self.assertEqual(len(row["rtp"]),2)
        self.assertEqual([r["estimated_missing_packets"] for r in row["rtp"]],[0,0])

    def test_service_requires_group_and_port(self):
        self.send(group="224.0.0.251")
        self.send(udp(destination=5353),group="224.0.0.251")
        self.assertEqual([r["possible_protocol"] for r in self.status()["streams"]],["Unknown multicast","mDNS"])

    def test_ptp_domains_sources_dscp_and_disappearance(self):
        for clock, domain, dscp in ((1,0,46),(2,0,48),(3,1,46)):
            self.send(udp(ptp(clock=clock,domain=domain),destination=320),dscp=dscp)
        status = self.status()
        self.assertEqual(status["ptp_domains"],[0,1])
        self.assertEqual(len(status["ptp_sources"]),3)
        self.assertTrue({"ptp_domains","ptp_announce_sources"}.issubset({w["code"] for w in status["warnings"]}))
        self.assertIn("ptp_announce_missing",{w["code"] for w in self.status(120)["warnings"]})

    def test_unicast_ptp_is_visible_but_not_multicast_bandwidth(self):
        self.send(udp(ptp(),destination=320),group="10.0.0.5")
        status = self.status()
        self.assertEqual(len(status["ptp_sources"]),1)
        self.assertEqual(status["packets"],0)
        self.assertEqual(status["streams"],[])

    def test_malformed_counters_and_fragments(self):
        capture._process_ipv4_packet(b"bad",101)
        self.send(b"bad")
        self.send(b"bad",protocol=2)
        self.send(udp(b"bad",destination=319))
        self.send(udp(ptp(),destination=319),fragment=0x2000)
        status = self.status()
        self.assertEqual(status["malformed_packets"],dict(ipv4=1,udp=1,igmp=1))
        self.assertEqual(status["ptp_sources"],[])
        self.assertEqual(status["packets"],4)

    def test_events_dscp_histories_and_churn_bounded(self):
        for i in range(300):
            self.send(igmp(0x16,"239.1.1.1"),protocol=2,dscp=i%64,now=101+i*.001)
        status = self.status()
        self.assertEqual(len(status["igmp_events"]),256)
        self.assertEqual(len(status["streams"][0]["dscp_history"]),64)
        self.assertNotIn("igmp_churn",{w["code"] for w in status["warnings"]})

    def test_ptp_dscp_is_scoped_to_source_clock_and_message_type(self):
        for source, kind, marking in (("10.0.0.1", 0, 56), ("10.0.0.1", 8, 46), ("10.0.0.2", 0, 46)):
            self.send(udp(ptp(kind=kind), destination=319), source=source, dscp=marking)
        status = self.status()
        self.assertEqual(len(status["ptp_sources"]), 2)
        self.assertFalse({"ptp_dscp", "mixed_dscp"} & {w["code"] for w in status["warnings"]})
        self.send(udp(ptp(kind=0), destination=319), dscp=48, now=102)
        warnings = [w for w in self.status()["warnings"] if w["code"] == "ptp_dscp"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Sync", warnings[0]["message"])
        self.assertIn("10.0.0.1", warnings[0]["message"])

    def test_unrecognized_ptp_is_information_not_malformed(self):
        for payload in (b"unknown", ptp()[:34], bytes([0, 1]) + b"x" * 50):
            self.send(udp(payload, destination=319))
        status = self.status()
        self.assertEqual(status["unrecognized_ptp_candidates"], 3)
        self.assertEqual(status["ptp_sources"], [])
        self.assertNotIn("ptp", status["malformed_packets"])
        self.assertTrue(status["streams"][0]["possible_protocol"].startswith("Possible PTP"))
        self.assertEqual(next(w for w in status["warnings"] if w["code"] == "possible_ptp")["severity"], "information")

    def test_many_reporters_and_cs0_sources_are_not_warning_storms(self):
        for n in range(1, 41):
            source = f"192.168.1.{n}"
            for repeat in range(10):
                self.send(igmp(0x16, "239.1.1.1"), source=source, protocol=2)
            self.send(udp(destination=8708), source=source, group="224.0.0.233", dscp=0)
        status = self.status()
        self.assertNotIn("igmp_churn", {w["code"] for w in status["warnings"]})
        observations = [w for w in status["warnings"] if w["code"] == "best_effort"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(len(observations[0]["sources"]), 40)
        self.assertIn("40 sources", observations[0]["message"])
        self.assertEqual(observations[0]["severity"], "information")

    def test_churn_requires_transitions_for_same_source_and_group(self):
        for n in range(8):
            self.send(igmp(0x16 if n % 2 == 0 else 0x17, "239.1.1.1"), protocol=2, now=101+n*.1)
        warnings = [w for w in self.status()["warnings"] if w["code"] == "igmp_churn"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("10.0.0.1 / 239.1.1.1", warnings[0]["message"])
        self.assertNotIn("igmp_churn", {w["code"] for w in self.status(120)["warnings"]})

    def test_reports_and_leaves_from_different_sources_do_not_flap(self):
        for n in range(20):
            self.send(igmp(0x16, "239.1.1.1"), source="10.0.0.1", protocol=2)
            self.send(igmp(0x17, "239.1.1.1"), source="10.0.0.2", protocol=2)
            self.send(igmp(0x17, "239.1.1.2"), source="10.0.0.1", protocol=2)
        self.assertNotIn("igmp_churn", {w["code"] for w in self.status()["warnings"]})

    def test_cs0_aggregation_handles_missing_udp_header(self):
        self.send(b"bad", dscp=0)
        self.send(dscp=0)
        self.assertEqual(len([w for w in self.status()["warnings"] if w["code"] == "best_effort"]), 2)

    def test_low_rate_unjoined_ptp_group_is_observation(self):
        for n in range(430):
            self.send(udp(ptp(kind=1), destination=319), group="224.0.1.129", now=100+n/86)
        status = self.status(105)
        group = next(g for g in status["groups"] if g["address"] == "224.0.1.129")
        self.assertFalse(group["suspected_flood"])
        self.assertIn("unjoined_observed", {w["code"] for w in status["warnings"]})

    def test_ptp_over_100_pps_has_no_legacy_flood_warning(self):
        from routes import _multicast_report_content
        for now in (101, 131, 161, 191):
            self.send(igmp(), protocol=2, group="224.0.0.1", now=now)
        for _ in range(518):
            self.send(udp(ptp(kind=0) + b"x" * 14, destination=319), group="224.0.1.129", now=198)
        status = self.status(199)
        group = next(g for g in status["groups"] if g["address"] == "224.0.1.129")
        self.assertEqual(group["packets_per_second"], 103.6)
        self.assertEqual(group["mbps"], .071)
        self.assertEqual(group["assessment"], "Observed")
        self.assertFalse(group["suspected_flood"])
        self.assertEqual(status["health_status"], "HEALTHY")
        self.assertEqual(status["warning_count"], 0)
        self.assertNotIn("Review", {s["status"] for s in status["streams"]})
        self.assertEqual(group["traffic_roles"], ["Timing / control / discovery"])
        self.assertFalse({"unjoined_traffic", "snooping"} & {w["code"] for w in status["warnings"]})
        report = _multicast_report_content(status)
        self.assertNotIn("[WARNING]", report)
        self.assertNotIn("Flooding suspected", report)
        self.assertIn("103.6 | 0.071", report)

    def test_capture_retains_sustained_bandwidth_evidence_and_expires_it(self):
        for second in range(210, 240):
            capture._record_multicast("239.1.1.1", 37_500_000, second)
        status = self.status(240)
        self.assertEqual(status["groups"][0]["sustained_min_mbps"], 300)
        self.assertEqual(status["groups"][0]["assessment"], "Possible flooding")
        self.assertEqual(self.status(241)["groups"][0]["assessment"], "Observed")

    def test_group_queries_timeline_interval_and_querier_expiry(self):
        self.send(igmp(),protocol=2,now=101)
        self.send(igmp(group="239.1.1.1"),protocol=2,now=111)
        status = self.status(112)
        self.assertEqual(status["queriers"][0]["query_interval_seconds"],10)
        self.assertEqual(status["igmp_events"][-1]["event_type"],"group_query")
        self.assertEqual(status["igmp_counts"]["group_query"],1)
        self.assertFalse(self.status(250)["querier_detected"])
        self.assertIn("no_querier",{w["code"] for w in self.status(250)["warnings"]})

    def test_flooding_excludes_joined_and_link_local(self):
        capture._state["joined_groups"] = {"239.1.1.1"}
        for _ in range(600):
            self.send(now=104)
            self.send(group="224.0.0.251",now=104)
        self.assertFalse(any(g["suspected_flood"] for g in self.status()["groups"]))

    def test_capacity_limits_retain_total_counters_and_rates(self):
        with patch.object(analyzer_state,"MAX_STREAMS",2), patch.object(capture,"MAX_STREAMS",2), patch.object(analyzer_state,"MAX_CLOCKS",2):
            for n in range(1,5):
                self.send(udp(ptp(clock=n),destination=320),source=f"10.0.0.{n}",group=f"239.1.1.{n}")
        status = self.status()
        self.assertEqual(len(status["streams"]),2)
        self.assertEqual(len(status["groups"]),2)
        self.assertEqual(len(status["ptp_sources"]),2)
        self.assertEqual(status["packets"],4)
        self.assertAlmostEqual(status["total_mbps"],round(4*92*8/1e6/5,3))
        self.assertEqual(status["evicted_records"],dict(streams=2,groups=2,clocks=2))

    def test_optional_profile_overrides_information_only_expectation(self):
        self.send(dscp=0)
        result = evaluate(self.status(),105,{"expected_dscp":{"Unknown multicast":46}})
        self.assertEqual(result[0]["code"],"profile_dscp")
        self.assertEqual(result[0]["severity"],"warning")

    def test_membership_empty_success_and_failure(self):
        with patch.object(capture,"run_cmd",return_value=(0,"Interface 7: Ethernet", "")):
            self.assertEqual(capture._read_joined_groups(7),set())
        with patch.object(capture,"run_cmd",return_value=(1,"", "failed")):
            self.assertIsNone(capture._read_joined_groups(7))

    def test_membership_system_call_does_not_hold_state_lock(self):
        capture._state["membership_checked_at"] = 0
        def read(_index):
            acquired = []
            def attempt():
                ok = capture._lock.acquire(timeout=.5)
                acquired.append(ok)
                if ok: capture._lock.release()
            thread = threading.Thread(target=attempt); thread.start(); thread.join()
            self.assertEqual(acquired,[True])
            return set()
        with patch.object(capture,"_read_joined_groups",side_effect=read):
            self.status()

    def test_lease_expiry_closes_socket(self):
        sock = Mock()
        capture._state["heartbeat"] = 0
        with patch.object(capture,"_open_raw_capture",return_value=sock), patch.object(capture.time,"monotonic",return_value=31):
            capture._capture("10.0.0.5")
        sock.close.assert_called_once()
        sock.recvfrom.assert_not_called()
        self.assertFalse(capture._state["running"])

    def test_permission_failure_and_cleanup(self):
        with patch.object(capture,"_open_raw_capture",side_effect=PermissionError):
            capture._capture("10.0.0.5")
        self.assertFalse(capture._state["running"])
        self.assertIn("administrator",capture._state["error"])

    def test_socket_setup_failure_closes_partial_socket(self):
        sock = Mock()
        sock.bind.side_effect = OSError("bad interface")
        with patch.object(capture.socket,"socket",return_value=sock):
            with self.assertRaises(OSError):
                capture._open_raw_capture("10.0.0.5")
        sock.close.assert_called_once()

    def test_thread_start_failure_resets_running_state(self):
        capture._state["running"] = False
        thread = Mock(); thread.start.side_effect = RuntimeError("no thread")
        with patch.object(capture,"_find_interface",return_value={"ip":"10.0.0.5","if_index":7}), patch.object(capture.threading,"Thread",return_value=thread):
            self.assertEqual(capture.start_multicast_test("Ethernet"),(False,"no thread"))
        self.assertFalse(capture._state["running"])

    def test_membership_result_does_not_mutate_new_capture(self):
        capture._state["membership_checked_at"] = 0
        def read(_index):
            capture._state["started_at"] = 200
            return {"239.1.2.3"}
        with patch.object(capture,"_read_joined_groups",side_effect=read):
            self.status()
        self.assertEqual(capture._state["joined_groups"],set())

    def test_stopped_snapshot_keeps_stream_and_ptp_data(self):
        self.send(udp(ptp(),destination=320))
        capture._state.update(running=False,stopped_at=105)
        early, late = self.status(105), self.status(1000)
        for field in ("streams","ptp_sources","warnings","total_mbps","health_status","health_checks","actionable_findings","health_confidence"):
            self.assertEqual(early[field],late[field])

    def test_rtp_source_capacity(self):
        with patch.object(analyzer_state,"MAX_RTP_SOURCES",2):
            for ssrc in range(4):
                self.send(udp(rtp(1,ssrc=ssrc)))
                self.send(udp(rtp(2,ssrc=ssrc)))
        status = self.status()
        self.assertEqual(len(status["streams"][0]["rtp"]),2)
        self.assertEqual(status["evicted_records"]["rtp_sources"],2)

    def test_start_stop_and_duplicate_start_with_mock_capture(self):
        capture._state["running"] = False
        entered = threading.Event()
        def fake(_ip):
            entered.set(); capture._stop_event.wait(2)
            with capture._lock: capture._state["running"] = False
        nic = {"ip":"10.0.0.5","if_index":7}
        with patch.object(capture,"_find_interface",return_value=nic), patch.object(capture,"_capture",side_effect=fake):
            self.assertTrue(capture.start_multicast_test("Ethernet")[0])
            self.assertTrue(entered.wait(1))
            self.assertFalse(capture.start_multicast_test("Ethernet")[0])
            self.assertTrue(capture.stop_multicast_test()[0])
            self.assertFalse(capture._thread.is_alive())
        capture._thread = None

    def test_page_and_json_route_and_failure_message(self):
        from app import app
        with patch("routes.get_nics",return_value=[]):
            response = authorized_client(app).get("/multicast")
        self.assertEqual(response.status_code,200)
        self.assertIn(b"AV Network Health Check",response.data)
        response = authorized_client(app).get("/multicast/status")
        self.assertTrue({"streams", "health_status", "health_checks", "actionable_findings", "diagnostic_information"}.issubset(response.get_json()))
        with patch("routes.start_multicast_test",return_value=(False,"Specific failure")):
            response = authorized_client(app).post("/multicast/start",json={})
        self.assertEqual(response.get_json()["message"],"Specific failure")


if __name__ == "__main__":
    unittest.main()
