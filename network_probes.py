"""Native IPv4 ICMP and route lookups for the desktop diagnostics worker."""
import ctypes
from dataclasses import dataclass
import errno
import ipaddress
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import time


@dataclass(frozen=True)
class ProbeReply:
    kind: str
    address: str = ""
    ms: float | None = None


@dataclass(frozen=True)
class Route:
    interface: str
    gateway: str = ""


_DWORD = ctypes.c_uint32
_POINTER = ctypes.c_void_p
_DARWIN_IP_BOUND_IF = 25
_IP_OPTION_FIELDS = [(name, ctypes.c_ubyte) for name in ("ttl", "tos", "flags", "size")]


class _IpOptions(ctypes.Structure):
    # The Windows x64 ICMP request takes IP_OPTION_INFORMATION32.
    _fields_ = _IP_OPTION_FIELDS + [("data", _DWORD)]


class _EchoReplyHeader(ctypes.Structure):
    _fields_ = [("address", _DWORD), ("status", _DWORD), ("rtt", _DWORD)]


class _ForwardRow(ctypes.Structure):
    _fields_ = [(field, _DWORD) for field in (
        "destination", "mask", "policy", "next_hop", "index", "type", "protocol", "age",
        "next_hop_as", "metric1", "metric2", "metric3", "metric4", "metric5")]


def _windows_address(value):
    return int.from_bytes(socket.inet_aton(value), "little") if value else 0


def _windows_route(api, target, source=""):
    function = api.GetBestRoute
    function.argtypes = [_DWORD, _DWORD, ctypes.POINTER(_ForwardRow)]
    function.restype = _DWORD
    row = _ForwardRow()
    result = function(_windows_address(target), _windows_address(source), ctypes.byref(row))
    if result:
        raise OSError(f"No suitable IPv4 route to {target} (Windows error {result}).")
    gateway = socket.inet_ntoa(row.next_hop.to_bytes(4, "little")) if row.type == 4 else ""
    return Route(str(row.index), gateway)


def _windows_reply(status, address="", rtt=None):
    if status == 0:
        return ProbeReply("reply", address, rtt)
    if status == 11013:
        return ProbeReply("hop", address, rtt)
    if status == 11010:
        return ProbeReply("timeout")
    if status in {11002, 11003, 11004, 11005, 11012, 11018, 1231, 1232}:
        return ProbeReply("unreachable", address, rtt)
    raise OSError(f"Windows ICMP probe failed (error {status}).")


class WindowsIcmp:
    def __init__(self, target, source, interface):
        self.target = target
        self.source = source
        self.interface = interface
        self.api = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)
        self.api.IcmpCreateFile.argtypes = []
        self.api.IcmpCreateFile.restype = _POINTER
        self.api.IcmpCloseHandle.argtypes = [_POINTER]
        self.api.IcmpCloseHandle.restype = ctypes.c_int
        self.send = self.api.IcmpSendEcho2Ex if source else self.api.IcmpSendEcho
        args = [_POINTER, _DWORD, _POINTER, ctypes.c_ushort, _POINTER, _POINTER, _DWORD, _DWORD]
        if source:
            args[1:1] = [_POINTER, _POINTER, _POINTER, _DWORD]
        self.send.argtypes = args
        self.send.restype = _DWORD

    def route(self):
        route = _windows_route(self.api, self.target, self.source)
        if self.interface and route.interface != self.interface:
            raise OSError(f"Windows routes {self.target} through interface {route.interface} "
                          f"instead of selected interface {self.interface}. No probe was sent.")
        return route

    def __enter__(self):
        self.handle = self.api.IcmpCreateFile()
        if self.handle in (None, _POINTER(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(self, *_):
        self.api.IcmpCloseHandle(self.handle)

    def send_probe(self, ttl, timeout_ms, cancelled):
        if self.interface:
            self.route()
        if cancelled.is_set():
            return None
        options = _IpOptions(ttl=ttl)
        payload = ctypes.create_string_buffer(b"AVNetworkingTools")
        buffer = ctypes.create_string_buffer(256)
        args = [_windows_address(self.target), payload, len(payload) - 1, ctypes.byref(options),
                buffer, len(buffer), timeout_ms]
        if self.source:
            args[:0] = [None, None, None, _windows_address(self.source)]
        count = self.send(self.handle, *args)
        if cancelled.is_set():
            return None
        if not count:
            return _windows_reply(ctypes.get_last_error())
        reply = _EchoReplyHeader.from_buffer(buffer)
        address = socket.inet_ntoa(reply.address.to_bytes(4, "little")) if reply.address else ""
        return _windows_reply(reply.status, address, float(reply.rtt))


def _ipv4_payload(packet):
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return b""
    offset = (packet[0] & 15) * 4
    return packet[offset:] if offset >= 20 and packet[9] == socket.IPPROTO_ICMP else b""


def parse_ipv4_response(packet, address, target, token, marker):
    icmp = _ipv4_payload(packet)
    if len(icmp) < 8:
        return None
    kind, code = icmp[:2]
    if kind == 0 and code == 0 and icmp[4:8] == token and icmp[8:] == marker and address == target:
        return "reply"
    if kind not in (3, 11) or (kind == 11 and code != 0):
        return None
    quoted = icmp[8:]
    embedded = _ipv4_payload(quoted)
    if (len(embedded) >= 8 and embedded[0] == 8 and embedded[4:8] == token
            and quoted[16:20] == socket.inet_aton(target)):
        return "hop" if kind == 11 else "unreachable"
    return None


def _echo_request(token, marker):
    packet = b"\x08\x00\x00\x00" + token + marker
    checksum = sum(struct.unpack(f"!{len(packet) // 2}H", packet))
    while checksum >> 16:
        checksum = (checksum & 0xffff) + (checksum >> 16)
    return packet[:2] + struct.pack("!H", ~checksum & 0xffff) + packet[4:]


def parse_macos_route(output):
    fields = dict((key.strip(), value.strip()) for key, value in
                  (line.split(":", 1) for line in output.splitlines() if ":" in line))
    if not fields.get("interface") or any(flag in fields.get("flags", "") for flag in ("REJECT", "BLACKHOLE")):
        raise OSError("No suitable IPv4 route found.")
    gateway = fields.get("gateway", "") if "GATEWAY" in fields.get("flags", "") else ""
    return Route(fields["interface"], gateway)


class MacIcmp:
    def __init__(self, target, source, interface):
        self.target = target
        self.source = source
        self.interface = interface
        self.identifier = secrets.randbits(16)
        self.sequence = 0

    def route(self):
        args = ["/sbin/route", "-n", "get", "-inet"]
        if self.interface:
            args.extend(["-ifscope", self.interface])
        result = subprocess.run([*args, self.target], capture_output=True, text=True, timeout=2)
        if result.returncode:
            raise OSError(f"No suitable IPv4 route to {self.target}"
                          + (f" on {self.interface}." if self.interface else "."))
        route = parse_macos_route(result.stdout)
        if self.interface and route.interface != self.interface:
            raise OSError(f"Route uses {route.interface} instead of {self.interface}. No probe was sent.")
        return route

    def __enter__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        try:
            if self.interface:
                # Darwin IP_BOUND_IF pins the socket to the physical adapter.
                self.socket.setsockopt(socket.IPPROTO_IP, _DARWIN_IP_BOUND_IF, socket.if_nametoindex(self.interface))
            if self.source:
                self.socket.bind((self.source, 0))
        except OSError:
            self.socket.close()
            raise
        return self

    def __exit__(self, *_):
        self.socket.close()

    def send_probe(self, ttl, timeout_ms, cancelled):
        self.sequence = (self.sequence + 1) & 0xffff
        token = struct.pack("!HH", self.identifier, self.sequence)
        marker = secrets.token_bytes(8)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
        if cancelled.is_set():
            return None
        started = time.monotonic()
        try:
            self.socket.sendto(_echo_request(token, marker), (self.target, 0))
        except OSError as exc:
            if exc.errno in {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN}:
                return ProbeReply("unreachable")
            raise
        deadline = started + timeout_ms / 1000
        while not cancelled.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ProbeReply("timeout")
            self.socket.settimeout(min(0.05, remaining))
            try:
                packet, (address, _) = self.socket.recvfrom(2048)
            except TimeoutError:
                continue
            kind = parse_ipv4_response(packet, address, self.target, token, marker)
            if kind:
                return ProbeReply(kind, address, (time.monotonic() - started) * 1000)
        return None


def _parse_ping6(output):
    # Windows may localize the reply prefix and time label. Match the numeric
    # latency after an IPv6 address. Unreachable messages do not contain it.
    for line in output.splitlines():
        match = re.search(r"([0-9a-fA-F]*:[0-9a-fA-F:.]+(?:%[\w.-]+)?)[,:]?\s+.*?"
                          r"[=<]\s*(\d+(?:[.,]\d+)?)\s*ms\b", line)
        if match:
            address, latency = match.groups()
            for candidate in (address, address[:-1]):
                try:
                    return ProbeReply("reply", str(ipaddress.IPv6Address(candidate)), float(latency.replace(",", ".")))
                except ValueError:
                    continue
    return ProbeReply("timeout")


class SystemPing6:
    def __init__(self, target):
        self.target = target

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def send_probe(self, ttl, timeout_ms, cancelled):
        if cancelled.is_set():
            return None
        if sys.platform == "win32":
            command = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PING.EXE"),
                       "-6", "-n", "1", "-w", str(timeout_ms), self.target]
        else:
            command = ["/sbin/ping6", "-n", "-c", "1", self.target]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                              text=True, errors="replace") as process:
            deadline = time.monotonic() + timeout_ms / 1000 + (0.1 if sys.platform == "win32" else 0)
            try:
                while not cancelled.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return ProbeReply("timeout")
                    try:
                        output, _ = process.communicate(timeout=min(0.05, remaining))
                        return _parse_ping6(output)
                    except subprocess.TimeoutExpired:
                        continue
                return None
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
