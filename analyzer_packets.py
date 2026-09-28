"""Pure, bounds-checked packet parsers. No capture or operating-system dependencies."""
import socket
import struct

DSCP_CLASSES = {0: "CS0 / Best Effort", 46: "EF"}
DSCP_CLASSES.update({n * 8: f"CS{n}" for n in range(1, 8)})
DSCP_CLASSES.update({8 * c + 2 * p: f"AF{c}{p}" for c in range(1, 5) for p in range(1, 4)})
PTP_TYPES = dict(enumerate(["Sync", "Delay_Req", "Pdelay_Req", "Pdelay_Resp"]))
PTP_TYPES.update({8: "Follow_Up", 9: "Delay_Resp", 10: "Pdelay_Resp_Follow_Up",
                  11: "Announce", 12: "Signaling", 13: "Management"})
PTP_MIN_LENGTH = {0: 44, 1: 44, 2: 54, 3: 54, 8: 44, 9: 54, 10: 54, 11: 64, 12: 44, 13: 48}


def dscp_class(value):
    return DSCP_CLASSES.get(value, f"DSCP {value}")


def parse_ipv4(packet):
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    length = (packet[0] & 15) * 4
    total = int.from_bytes(packet[2:4], "big")
    if length < 20 or total < length or len(packet) < total:
        return None
    fragment = int.from_bytes(packet[6:8], "big")
    return {"source": socket.inet_ntoa(packet[12:16]),
            "group": socket.inet_ntoa(packet[16:20]), "protocol": packet[9],
            "ttl": packet[8], "dscp": packet[1] >> 2, "ecn": packet[1] & 3,
            "bytes": total, "fragmented": bool(fragment & 0x3fff),
            "payload": packet[length:total]}


def parse_udp(payload):
    if len(payload) < 8:
        return None
    source, destination, length = struct.unpack("!HHH", payload[:6])
    if length < 8 or length > len(payload):
        return None
    return {"source_port": source, "destination_port": destination, "payload": payload[8:length]}


def stream_key(ip, udp=None):
    return (ip["source"], ip["group"], ip["protocol"],
            udp["destination_port"] if udp else None, udp["source_port"] if udp else None)


def parse_igmp(payload):
    invalid = (None, [], "invalid")
    if len(payload) < 8:
        return invalid
    kind = payload[0]
    group = socket.inet_ntoa(payload[4:8])
    multicast_group = 224 <= payload[4] <= 239
    if kind == 0x11:
        if group != "0.0.0.0" and not multicast_group:
            return invalid
        if len(payload) != 8:
            if len(payload) < 12 or 12 + int.from_bytes(payload[10:12], "big") * 4 > len(payload):
                return invalid
        return ("v3" if len(payload) >= 12 else "v1" if payload[1] == 0 else "v2",
                [] if group == "0.0.0.0" else [group], "query")
    if kind in (0x12, 0x16, 0x17):
        if not multicast_group:
            return invalid
        return ("v1" if kind == 0x12 else "v2", [group], "leave" if kind == 0x17 else "report")
    if kind == 0x22:
        groups, offset = [], 8
        for _ in range(int.from_bytes(payload[6:8], "big")):
            if offset + 8 > len(payload):
                return invalid
            end = offset + 8 + 4 * (payload[offset + 1] + int.from_bytes(payload[offset + 2:offset + 4], "big"))
            if end > len(payload) or not 1 <= payload[offset] <= 6 or not 224 <= payload[offset + 4] <= 239:
                return invalid
            groups.append(socket.inet_ntoa(payload[offset + 4:offset + 8]))
            offset = end
        return "v3", groups, "report"
    return None, [], f"type_{kind:#04x}"


def parse_ptp(payload):
    """Decode IEEE 1588 v2 common header; v1 has a different layout and is not decoded."""
    if len(payload) < 34 or payload[1] & 15 != 2:
        return None
    kind = payload[0] & 15
    length = int.from_bytes(payload[2:4], "big")
    if kind not in PTP_TYPES or not PTP_MIN_LENGTH[kind] <= length <= len(payload):
        return None
    interval = struct.unpack("!b", payload[33:34])[0]
    return {"version": 2, "message_type": PTP_TYPES[kind], "domain": payload[4],
            "sequence_id": int.from_bytes(payload[30:32], "big"),
            "clock_identity": payload[20:28].hex(":"),
            "source_port_number": int.from_bytes(payload[28:30], "big"),
            "log_message_interval": None if interval == 127 else interval}


def parse_rtp(payload):
    """Conservative candidate only: valid v2 layout, media bytes, no RTCP/reserved PTs."""
    if len(payload) < 13 or payload[0] >> 6 != 2 or 192 <= payload[1] <= 223:
        return None
    pt = payload[1] & 127
    if pt in (1, 2, 19) or 35 <= pt <= 95:
        return None
    offset = 12 + (payload[0] & 15) * 4
    if offset > len(payload):
        return None
    if payload[0] & 16:
        if offset + 4 > len(payload):
            return None
        offset += 4 + int.from_bytes(payload[offset + 2:offset + 4], "big") * 4
    end = len(payload)
    if payload[0] & 32:
        padding = payload[-1]
        if padding == 0 or padding > end - offset:
            return None
        end -= padding
    if offset >= end:
        return None
    return {"payload_type": pt, "sequence_number": int.from_bytes(payload[2:4], "big"),
            "rtp_timestamp": int.from_bytes(payload[4:8], "big"),
            "ssrc": int.from_bytes(payload[8:12], "big")}
