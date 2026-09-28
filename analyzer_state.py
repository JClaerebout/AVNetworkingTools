"""Bounded stream/timing state. Caller owns synchronization and supplies capture time."""
from collections import Counter, deque
from copy import deepcopy

from analyzer_packets import dscp_class, stream_key

MAX_STREAMS = 2048
MAX_CLOCKS = 256
MAX_RTP_SOURCES = 16
HISTORY_LIMIT = 256
ACTIVE_SECONDS = 15
RATE_SECONDS = 5


def rates(item, now, started_at):
    buckets = [b for b in item["buckets"] if int(now) - RATE_SECONDS < b[0] <= int(now)]
    duration = min(RATE_SECONDS, max(1, now - started_at))
    return (sum(b[1] for b in buckets) / duration,
            sum(b[2] for b in buckets) * 8 / 1_000_000 / duration)


def record_rate(item, size, now):
    item["packets"] += 1
    item["bytes"] += size
    second = int(now)
    buckets = item["buckets"]
    if buckets and buckets[-1][0] == second:
        buckets[-1][1] += 1
        buckets[-1][2] += size
    else:
        buckets.append([second, 1, size])
    while buckets and second - buckets[0][0] >= RATE_SECONDS:
        buckets.popleft()


class RTPStats:
    """Sequence estimates with a 2048-packet repair/duplicate window, per SSRC/PT.

    Large jumps (>3000) are treated as discontinuities, not thousands of losses.
    Jitter is arrival-spacing variation, not RFC3550 jitter (clock rate unknown).
    """
    def __init__(self):
        self.data = {"packets": 0, "estimated_missing_packets": 0, "duplicate_packets": 0,
                     "out_of_order_packets": 0, "largest_packet_gap": 0,
                     "largest_arrival_gap_ms": 0, "estimated_network_jitter_ms": 0,
                     "discontinuities": 0}
        self.highest = None
        self.seen = set()
        self.missing = set()
        self.previous_interval = None
        self.confirmed = False

    def record(self, header, now):
        d = self.data
        if d["packets"]:
            interval = max(0, now - d["last_seen"]) * 1000
            d["largest_arrival_gap_ms"] = max(d["largest_arrival_gap_ms"], interval)
            if self.previous_interval is not None:
                d["estimated_network_jitter_ms"] += (abs(interval - self.previous_interval) - d["estimated_network_jitter_ms"]) / 16
            self.previous_interval = interval
        else:
            d["first_seen"] = now
        seq = header["sequence_number"]
        if self.highest is None:
            extended = seq
            self.highest = extended
        else:
            delta = ((seq - (self.highest & 65535) + 32768) % 65536) - 32768
            if delta == 1:
                self.confirmed = True
            extended = self.highest + delta
            if extended in self.seen:
                d["duplicate_packets"] += 1
            elif delta > 3000:
                d["discontinuities"] += 1
                self.highest = extended
                self.missing.clear()
            elif delta > 0:
                gap = delta - 1
                d["largest_packet_gap"] = max(d["largest_packet_gap"], gap)
                d["estimated_missing_packets"] += gap
                self.missing.update(range(max(self.highest + 1, extended - 2048), extended))
                self.highest = extended
            else:
                d["out_of_order_packets"] += 1
                if extended in self.missing:
                    self.missing.remove(extended)
                    d["estimated_missing_packets"] -= 1
        self.seen.add(extended)
        # Prune in batches, avoiding an O(window) scan for every packet.
        if len(self.seen) > 4096:
            self.seen = {n for n in self.seen if n >= self.highest - 2048}
        self.missing = {n for n in self.missing if n >= self.highest - 2048}
        d.update(header)
        d["packets"] += 1
        d["last_seen"] = now


class Analyzer:
    def __init__(self):
        self.streams = {}
        self.clocks = {}
        self.events = deque(maxlen=HISTORY_LIMIT)
        self.malformed = Counter()
        self.ptp_candidates = 0
        self.evicted = Counter()
        self.dscp_counts = Counter()

    def record(self, ip, udp, ptp, rtp, now):
        if ptp:
            key = (ip["source"], ptp["domain"], ptp["clock_identity"], ptp["source_port_number"])
            if key not in self.clocks and len(self.clocks) >= MAX_CLOCKS:
                del self.clocks[min(self.clocks, key=lambda k: self.clocks[k]["last_seen"])]
                self.evicted["clocks"] += 1
            clock = self.clocks.setdefault(key, {"first_seen": now, "packets": 0,
                "message_counts": Counter(), "dscp_by_message_type": {}, "dscp_values": set(), "last_announce": None})
            clock.update(ptp)
            clock.update(last_seen=now, source=ip["source"])
            clock["packets"] += 1
            clock["message_counts"][ptp["message_type"]] += 1
            clock["dscp_values"].add(ip["dscp"])
            clock["dscp_by_message_type"].setdefault(ptp["message_type"], set()).add(ip["dscp"])
            if ptp["message_type"] == "Announce":
                clock["last_announce"] = now
                interval = ptp["log_message_interval"]
                clock["announce_timeout"] = max(15, 3 * 2 ** max(-10, min(10, interval))) if interval is not None else 15
        if not 224 <= int(ip["group"].split(".")[0]) <= 239:
            return
        key = stream_key(ip, udp)
        if key not in self.streams and len(self.streams) >= MAX_STREAMS:
            del self.streams[min(self.streams, key=lambda k: self.streams[k]["last_seen"])]
            self.evicted["streams"] += 1
        if key not in self.streams:
            self.streams[key] = {"source": ip["source"], "group": ip["group"],
            "ip_protocol": ip["protocol"], "udp_source_port": key[4], "udp_destination_port": key[3],
            "id": "|".join(map(str, key)), "packets": 0, "bytes": 0,
            "buckets": deque(maxlen=RATE_SECONDS), "first_seen": now, "last_seen": now,
            "dscp_values": set(), "ttl_values": set(), "ecn_values": set(),
            "dscp_history": deque(maxlen=64), "dscp_changes": 0,
            "possible_protocol": "Unknown multicast", "rtp": {}}
        item = self.streams[key]
        if item["packets"] and item["dscp"] != ip["dscp"]:
            item["dscp_changes"] += 1
        if not item["packets"] or item["dscp"] != ip["dscp"]:
            item["dscp_history"].append({"timestamp": now, "value": ip["dscp"], "class": dscp_class(ip["dscp"])})
        for name in ("dscp", "ttl", "ecn"):
            item[name] = ip[name]
            item[name + "_values"].add(ip[name])
        self.dscp_counts[ip["dscp"]] += 1
        record_rate(item, ip["bytes"], now)
        item["last_seen"] = now
        if ptp:
            item["possible_protocol"] = "PTP"
        elif udp:
            service = {("224.0.0.251", 5353): "mDNS", ("224.0.0.252", 5355): "LLMNR",
                       ("239.255.255.250", 1900): "SSDP"}.get((ip["group"], udp["destination_port"]))
            if udp["source_port"] in (319, 320) or udp["destination_port"] in (319, 320):
                if item["possible_protocol"] != "PTP":
                    item["possible_protocol"] = "Possible PTP / UDP 319 or 320"
            elif service:
                item["possible_protocol"] = service
            elif rtp:
                rkey = (rtp["ssrc"], rtp["payload_type"])
                if rkey not in item["rtp"] and len(item["rtp"]) >= MAX_RTP_SOURCES:
                    del item["rtp"][next(iter(item["rtp"]))]
                    self.evicted["rtp_sources"] += 1
                if rkey not in item["rtp"]:
                    item["rtp"][rkey] = RTPStats()
                stats = item["rtp"][rkey]
                stats.record(rtp, now)
                if stats.confirmed:
                    item["possible_protocol"] = "Possible RTP"

    def snapshot(self, now, started_at):
        streams = []
        for item in self.streams.values():
            row = {k: deepcopy(v) for k, v in item.items() if k not in ("buckets", "rtp")}
            for name in ("dscp", "ttl", "ecn"):
                row[name + "_values"] = sorted(item[name + "_values"])
            row["dscp_history"] = list(row["dscp_history"])
            row["mixed_dscp"] = len(row["dscp_values"]) > 1
            row["mixed_ttl"] = len(row["ttl_values"]) > 1
            row["dscp_class"] = dscp_class(row["dscp"])
            row["packets_per_second"], row["mbps"] = rates(item, now, started_at)
            row["average_packet_size"] = row["bytes"] / row["packets"]
            row["active"] = now - row["last_seen"] < ACTIVE_SECONDS
            row["rtp"] = [deepcopy(s.data) for s in item["rtp"].values() if s.confirmed]
            streams.append(row)
        clocks = []
        for item in self.clocks.values():
            row = deepcopy(item)
            row["dscp_values"] = sorted(row["dscp_values"])
            row["message_counts"] = dict(row["message_counts"])
            row["dscp_by_message_type"] = {k: sorted(v) for k, v in row["dscp_by_message_type"].items()}
            row["active"] = now - row["last_seen"] < max(ACTIVE_SECONDS, row.get("announce_timeout", 0))
            row["announce_active"] = row["last_announce"] is not None and now - row["last_announce"] < row["announce_timeout"]
            clocks.append(row)
        return {"streams": streams, "active_streams": sum(s["active"] for s in streams),
                "ptp_sources": clocks, "ptp_domains": sorted({c["domain"] for c in clocks if c["active"]}),
                "igmp_events": deepcopy(list(self.events)), "malformed_packets": dict(self.malformed),
                "unrecognized_ptp_candidates": self.ptp_candidates,
                "evicted_records": dict(self.evicted),
                "dscp_distribution": [{"value": d, "class": dscp_class(d), "packets": n}
                                      for d, n in sorted(self.dscp_counts.items())]}
