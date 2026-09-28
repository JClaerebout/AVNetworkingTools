"""Locally learned connection settings for manufacturers seen in IP Scan."""

from datetime import datetime, timezone

from config import CONNECTION_PRESETS_FILE
from history_store import load_list, update_list


MAX_LEARNED_PRESETS = 100
LEARNABLE_PROTOCOLS = {"tcp", "telnet", "ssh"}
UNKNOWN_NAMES = {"-", "unknown", "looking up...", "this pc",
                 "private", "ieee registration authority"}


def _manufacturer(value):
    if not isinstance(value, str):
        return ""
    name = " ".join(value.strip().split())
    return name if 0 < len(name) <= 120 and name.casefold() not in UNKNOWN_NAMES else ""


def _valid(item):
    return (isinstance(item, dict)
            and bool(_manufacturer(item.get("manufacturer")))
            and isinstance(item.get("protocol"), str)
            and item["protocol"] in LEARNABLE_PROTOCOLS
            and type(item.get("port")) is int
            and 1 <= item["port"] <= 65535)


def get_learned_preset(manufacturer):
    name = _manufacturer(manufacturer)
    if not name:
        return None
    return next((item for item in load_list(CONNECTION_PRESETS_FILE, _valid)
                 if item["manufacturer"].casefold() == name.casefold()), None)


def learn_preset(manufacturer, protocol, port):
    name = _manufacturer(manufacturer)
    if not name or not isinstance(protocol, str) or protocol not in LEARNABLE_PROTOCOLS:
        return False
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if not 1 <= port <= 65535:
        return False

    def update(entries):
        record = {"manufacturer": name, "protocol": protocol, "port": port,
                  "learned_at": datetime.now(timezone.utc).isoformat()}
        remaining = [item for item in entries
                     if item["manufacturer"].casefold() != name.casefold()]
        return [record, *remaining][:MAX_LEARNED_PRESETS], True

    return update_list(CONNECTION_PRESETS_FILE, _valid, update)
