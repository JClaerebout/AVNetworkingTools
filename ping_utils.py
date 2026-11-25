"""Network diagnostics session and compatibility entry points for the Ping page."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from typing import List

from config import PING_HISTORY_FILE
from network_probes import MacIcmp, SystemPing6, WindowsIcmp
from workspace_state import load_workspace_list as load_list, update_workspace_list as update_list

_ping_lock = threading.Lock()
_session = None
_previous = {}


def load_ping_history() -> List[str]:
    return load_list(PING_HISTORY_FILE, lambda item: isinstance(item, str) and bool(item.strip()))


def save_ping_history_entry(ip: str) -> None:
    if not isinstance(ip, str) or not ip.strip():
        raise ValueError("Ping target is required.")
    def update(history):
        history = [item for item in history if item != ip]
        history.insert(0, ip)
        return history[:30], None
    update_list(PING_HISTORY_FILE, lambda item: isinstance(item, str) and bool(item.strip()), update)


_HOSTNAME_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_target(value: str) -> tuple[bool, str]:
    value = value.strip()
    if not value:
        return False, "IP address or hostname is required."

    try:
        ipaddress.ip_address(value)
        return True, value
    except ValueError:
        pass

    # A final dot is valid DNS notation, but omit it from the ping command and
    # history so equivalent hostnames are stored consistently.
    hostname = value[:-1] if value.endswith(".") else value
    labels = hostname.split(".")
    if len(hostname) <= 253 and all(_HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        return True, hostname

    return False, "Invalid IP address or hostname. Example: 192.168.1.1 or switch.local"


def validate_ip(value: str) -> tuple[bool, str]:
    """Backward-compatible alias for callers that used the old validator name."""
    return validate_target(value)


MODES = {"ping": "Ping", "trace": "Traceroute", "combined": "Combined"}


@dataclass(frozen=True)
class Settings:
    mode: str
    source: str
    interval: int
    timeout: int
    max_hops: int
    hop_interval: int

    @classmethod
    def from_request(cls, data):
        mode = data.get("mode", "ping")
        source = data.get("source", "auto")
        if not isinstance(mode, str) or mode not in MODES:
            raise ValueError("Choose Ping, Traceroute, or Combined.")
        if not isinstance(source, str) or len(source) > 128 or not source:
            raise ValueError("Choose a valid adapter and source address.")

        def number(key, default, minimum, maximum, label):
            value = data.get(key, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{label} must be between {minimum} and {maximum}.")
            return value

        return cls(mode, source, number("interval", 1000, 250, 60000, "Ping interval (ms)"),
                   number("timeout", 1000, 100, 5000, "Timeout (ms)"),
                   number("max_hops", 30, 1, 64, "Maximum hops"),
                   number("hop_interval", 2000, 500, 60000, "Hop interval (ms)"))


def diagnostics_sources():
    from nic_utils import get_nics
    choices = {}
    for nic in get_nics():
        if nic.get("link_status") != "Up":
            continue
        interface = str(nic["if_index"]) if sys.platform == "win32" else nic["device"]
        identity = nic.get("guid") or interface
        candidates = [(nic.get("ip"), nic.get("subnet"), False)]
        candidates.extend((entry["ip"], entry["prefix"], True)
                          for entry in nic.get("secondary_ips", []) if entry.get("active"))
        for address, mask, secondary in candidates:
            try:
                network = ipaddress.IPv4Interface(f"{address}/{mask}")
            except ValueError:
                continue
            if network.ip.is_unspecified or network.ip.is_multicast:
                continue
            value = f"{identity}|{network.ip}"
            choices[value] = dict(value=value, name=nic["name"], interface=interface, ip=str(network.ip),
                                  prefix=network.network.prefixlen, gateway=nic.get("gateway", ""),
                                  secondary=secondary)
    return list(choices.values())


def select_source(target, selection, sources):
    if selection != "auto":
        matched = next((item for item in sources if item["value"] == selection), None)
        if not matched:
            raise ValueError("The selected adapter or IPv4 address is no longer available. Refresh adapters.")
        return matched

    destination = ipaddress.IPv4Address(target)
    on_link = [item for item in sources if destination in
               ipaddress.IPv4Network(f"{item['ip']}/{item['prefix']}", strict=False)]
    if on_link:
        longest = max(item["prefix"] for item in on_link)
        best = [item for item in on_link if item["prefix"] == longest]
        if len(best) == 1 and best[0]["secondary"]:
            return best[0]
    return None


def route_context(target, route, source, automatic_source, sources, selection):
    context = {"interface": route.interface, "source": automatic_source, "gateway": route.gateway}
    nic = source or next((item for item in sources if item["interface"] == route.interface
                          and item["ip"] == automatic_source), None)
    if nic:
        context.update(interface=nic["name"], source=nic["ip"], prefix=nic["prefix"],
                       default_gateway=nic["gateway"], secondary=nic["secondary"])
    if route.gateway:
        note = f"Destination uses gateway {route.gateway}."
    elif nic and ipaddress.IPv4Address(target) in ipaddress.IPv4Network(
            f"{nic['ip']}/{nic['prefix']}", strict=False):
        note = "Destination is on the local subnet."
    else:
        note = "The routing table reports a direct route."
    if source and source["secondary"]:
        note += (" Automatically selected an on-subnet secondary IPv4 address."
                 if selection == "auto" else " Using the selected secondary IPv4 address.")
    elif not source:
        alternate = next((item for item in sources if item["secondary"] and
                          ipaddress.IPv4Address(target) in ipaddress.IPv4Network(
                              f"{item['ip']}/{item['prefix']}", strict=False)), None)
        if alternate:
            note += f" Secondary address {alternate['ip']} is on the destination subnet."
    context["note"] = note
    return context


def _resolve_target(target, settings):
    family = socket.AF_UNSPEC if settings.mode == "ping" and settings.source == "auto" else socket.AF_INET
    try:
        literal = ipaddress.ip_address(target)
    except ValueError:
        try:
            results = socket.getaddrinfo(target, None, family, socket.SOCK_DGRAM)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve {target} to a supported address.") from exc
        address = min(results, key=lambda row: row[0] != socket.AF_INET)[4]
        target = address[0]
        if len(address) == 4 and address[3] and "%" not in target:
            target += f"%{address[3]}"
        return target
    if literal.version == 6 and family == socket.AF_INET:
        raise ValueError("Traceroute, Combined, and adapter selection require an IPv4 destination.")
    return str(literal)


def _auto_source(target):
    # UDP connect performs source selection without sending a datagram.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target, 9))
            return sock.getsockname()[0]
    except OSError:
        return ""


@dataclass
class Statistics:
    sent: int = 0
    received: int = 0
    last: float | None = None
    best: float | None = None
    worst: float | None = None
    total: float = 0

    def record(self, ms):
        self.sent += 1
        self.last = ms
        if ms is not None:
            self.received += 1
            self.total += ms
            self.best = ms if self.best is None else min(self.best, ms)
            self.worst = ms if self.worst is None else max(self.worst, ms)

    def snapshot(self):
        return dict(sent=self.sent, received=self.received, last=self.last, best=self.best, worst=self.worst,
                    avg=self.total / self.received if self.received else None,
                    loss=100 * (self.sent - self.received) / self.sent if self.sent else None)


@dataclass
class Hop:
    address: str
    kind: str
    stats: Statistics


class Diagnostics:
    def __init__(self, target, settings):
        self.target = target
        self.settings = settings
        self.cancelled = threading.Event()
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.message = "Resolving destination…"
        self.destination = ""
        self.route = {}
        self.progress = 0
        self.sweeps = 0
        self.route_changes = 0
        self.reachability = "Unknown"
        self.stats = Statistics()
        self.samples = deque(maxlen=120)
        self.hops = {}
        self.output = deque(maxlen=500)
        self.log(f"Started {MODES[settings.mode]} to {target}.")

    def log(self, text):
        self.output.append(f"[{datetime.now():%H:%M:%S}] {text}")

    def snapshot(self):
        return dict(target=self.target, mode=self.settings.mode, started_at=self.started_at,
                    destination=self.destination, route=dict(self.route), message=self.message,
                    source_selection=self.settings.source, reachability=self.reachability,
                    progress=self.progress, max_hops=self.settings.max_hops, sweeps=self.sweeps,
                    route_changes=self.route_changes, stats=self.stats.snapshot(), samples=list(self.samples),
                    hops=[dict(hop=ttl, address=hop.address, kind=hop.kind, **hop.stats.snapshot())
                          for ttl, hop in sorted(self.hops.items())], output=list(self.output))

    def record_destination(self, reply):
        ms = reply.ms if reply.kind == "reply" else None
        self.stats.record(ms)
        self.samples.append(ms)
        if reply.kind == "reply":
            self.reachability = "Reached"
            self.log(f"Reply from {reply.address}: {ms:.2f} ms")
        elif reply.kind == "unreachable":
            self.reachability = "Unreachable"
            self.log(f"Destination unreachable{f' (reported by {reply.address})' if reply.address else ''}.")
        else:
            self.reachability = "No reply"
            self.log("Request timed out.")

    def record_hop(self, ttl, reply):
        self.progress = ttl
        previous = self.hops.get(ttl)
        if previous is None:
            previous = self.hops[ttl] = Hop(reply.address, reply.kind, Statistics())
            if self.sweeps and reply.address:
                self.route_changes += 1
                self.log(f"Route changed: hop {ttl} appeared at {reply.address}.")
        elif reply.address and reply.address != previous.address:
            if previous.address:
                self.route_changes += 1
                self.log(f"Route changed at hop {ttl}: {previous.address} → {reply.address}.")
                previous.stats = Statistics()
            elif self.sweeps:
                self.route_changes += 1
                self.log(f"Route changed: hop {ttl} now responds from {reply.address}.")
            previous.address = reply.address

        previous.kind = reply.kind
        previous.stats.record(reply.ms)
        terminal = reply.kind in {"reply", "unreachable"} or ttl == self.settings.max_hops
        if reply.kind == "reply":
            removed = [hop for hop in self.hops if hop > ttl]
            if self.sweeps and any(self.hops[hop].address for hop in removed):
                self.route_changes += 1
                self.log(f"Route shortened: destination now responds at hop {ttl}.")
            for hop in removed:
                del self.hops[hop]
        if self.settings.mode == "trace" or self.sweeps == 0:
            latency = f" · {reply.ms:.2f} ms" if reply.ms is not None else ""
            failure = " · unreachable" if reply.kind == "unreachable" else ""
            self.log(f"Hop {ttl}: {reply.address or '*'}{latency}{failure}")
        if terminal:
            self.sweeps += 1
        return terminal

    def clear(self):
        self.output.clear()
        self.samples.clear()
        self.stats = Statistics()
        self.reachability = "Unknown"
        self.route_changes = 0
        for hop in self.hops.values():
            hop.stats = Statistics()

    def probe_destination(self, probe):
        reply = probe.send_probe(255, self.settings.timeout, self.cancelled)
        with _ping_lock:
            if reply is not None and not self.cancelled.is_set():
                self.record_destination(reply)

    def probe_hop(self, probe, ttl):
        reply = probe.send_probe(ttl, self.settings.timeout, self.cancelled)
        with _ping_lock:
            if reply is None or self.cancelled.is_set():
                return None, True
            return reply, self.record_hop(ttl, reply)

    def measure(self, probe):
        settings = self.settings
        if settings.mode != "ping":
            with _ping_lock:
                self.message = "Discovering route…"
            for ttl in range(1, settings.max_hops + 1):
                reply, terminal = self.probe_hop(probe, ttl)
                if terminal or self.cancelled.wait(0.15):
                    break
            if self.cancelled.is_set() or reply is None:
                return
            with _ping_lock:
                if reply.kind == "reply":
                    self.record_destination(reply)
                else:
                    self.reachability = "Unreachable" if reply.kind == "unreachable" else "No reply"
                if settings.mode == "trace":
                    self.message = ("Route complete." if reply.kind == "reply" else
                                    "Trace stopped at an unreachable hop." if reply.kind == "unreachable"
                                    else "Hop limit reached without a destination reply.")
                    return

        with _ping_lock:
            self.message = "Monitoring route and destination…" if settings.mode == "combined" else "Pinging destination…"
        next_ping = time.monotonic()
        next_hop = next_ping + settings.hop_interval / 1000
        ttl = 1
        while not self.cancelled.is_set():
            now = time.monotonic()
            if now >= next_ping:
                self.probe_destination(probe)
                next_ping = now + settings.interval / 1000
            now = time.monotonic()
            if settings.mode == "combined" and now >= next_hop and not self.cancelled.is_set():
                _, terminal = self.probe_hop(probe, ttl)
                ttl = 1 if terminal else ttl + 1
                next_hop = now + settings.hop_interval / 1000
            deadline = min(next_ping, next_hop) if settings.mode == "combined" else next_ping
            self.cancelled.wait(max(0, deadline - time.monotonic()))

    def run(self):
        global _session, _previous
        try:
            target = _resolve_target(self.target, self.settings)
            if self.cancelled.is_set():
                return
            with _ping_lock:
                self.destination = target
                self.message = "Checking route…"
            if ipaddress.ip_address(target).version == 6:
                probe = SystemPing6(target)
                context = {"note": "IPv6 Ping uses automatic system routing."}
            else:
                try:
                    sources = [] if ipaddress.IPv4Address(target).is_loopback and self.settings.source == "auto" else diagnostics_sources()
                except Exception:
                    if self.settings.source != "auto":
                        raise ValueError("Could not read adapter addresses. Refresh adapters and try again.")
                    logging.exception("Could not read adapter addresses for diagnostics")
                    sources = []
                if self.cancelled.is_set():
                    return
                source = select_source(target, self.settings.source, sources)
                if source:
                    with _ping_lock:
                        self.route = {"interface": source["name"], "source": source["ip"],
                                      "prefix": source["prefix"], "default_gateway": source["gateway"]}
                backend = WindowsIcmp if sys.platform == "win32" else MacIcmp
                probe = backend(target, source["ip"] if source else "", source["interface"] if source else "")
                route = probe.route()
                context = route_context(target, route, source, _auto_source(target) if not source else "",
                                        sources, self.settings.source)
            if self.cancelled.is_set():
                return
            with _ping_lock:
                self.route = context
                self.log(context["note"])
            with probe as active_probe:
                self.measure(active_probe)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            with _ping_lock:
                if not self.cancelled.is_set():
                    self.message = str(exc)
                    self.log(self.message)
        except Exception:
            logging.exception("Unexpected diagnostics failure")
            with _ping_lock:
                self.message = "Diagnostics failed. See the application log for details."
                self.log(self.message)
        finally:
            with _ping_lock:
                if self.cancelled.is_set():
                    self.message = "Stopped."
                    self.log(self.message)
                _previous = self.snapshot()
                _session = None


def start_ping(ip: str, options=None) -> tuple[bool, str]:
    global _session
    valid, target = validate_target(ip)
    if not valid:
        return False, target
    try:
        settings = Settings.from_request(options or {})
    except ValueError as exc:
        return False, str(exc)
    with _ping_lock:
        if _session is not None:
            return False, "Diagnostics are already active. Stop the current run first."
        session = Diagnostics(target, settings)
        _session = session
        worker = threading.Thread(target=session.run, name="network-diagnostics", daemon=True)
        try:
            worker.start()
        except RuntimeError as exc:
            _session = None
            return False, f"Could not start diagnostics: {exc}"
    try:
        save_ping_history_entry(target)
    except OSError:
        logging.exception("Could not save diagnostics target")
    return True, f"Started {MODES[settings.mode]} to {target}."


def stop_ping() -> tuple[bool, str]:
    with _ping_lock:
        if _session is None:
            return False, "No diagnostics are running."
        _session.cancelled.set()
        return True, "Stop requested."


def clear_ping():
    global _previous
    with _ping_lock:
        if _session is None:
            _previous = {}
        else:
            _session.clear()


def get_ping_status() -> dict:
    history = load_ping_history()
    with _ping_lock:
        running = _session is not None
        snapshot = _session.snapshot() if running else deepcopy(_previous)
        return {"output": [], **snapshot, "running": running,
                "stopping": running and _session.cancelled.is_set(),
                "history": history}


def restore_ping_status(data, *, replace=False):
    global _previous
    with _ping_lock:
        if not isinstance(data, dict) or _session is not None:
            return
        if replace or data:
            _previous = deepcopy({key: value for key, value in data.items()
                                  if key not in {"running", "stopping", "history"}})
            if _previous and data.get("running"):
                _previous["message"] = "Restored previous diagnostics (stopped)."


def get_ping_activity() -> bool:
    with _ping_lock:
        return _session is not None
