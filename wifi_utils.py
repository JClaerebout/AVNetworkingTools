import re
import subprocess
import threading
import time
from collections import defaultdict

_wifi_lock = threading.Lock()
_wifi_thread = None
_wifi_stop = threading.Event()
_wifi_running = False
_wifi_message = "Idle"
_wifi_results = []


def _run_netsh():
    result = subprocess.run(
        ["netsh", "wlan", "show", "networks", "mode=bssid"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.stdout


def _channel_to_band(channel):
    try:
        ch = int(channel)
    except Exception:
        return "unknown"

    if 1 <= ch <= 14:
        return "2.4GHz"
    return "5GHz"


def _parse_signal_percent(value):
    try:
        return int(value.replace("%", "").strip())
    except Exception:
        return 0


def _parse_utilization_percent(value):
    match = re.search(r"\((\d+)\s*%\)", value)
    if match:
        return int(match.group(1))

    match = re.search(r"(\d+)\s*%", value)
    if match:
        return int(match.group(1))

    return None


def _parse_channel(value):
    channel = value.strip()
    return channel if re.fullmatch(r"\d+", channel) else ""


def _channel_sort_value(value):
    try:
        return int(value)
    except Exception:
        return 0


def _radio_key(item):
    return str(item.get("bssid") or "").lower()


def _ap_device_key(item):
    bssid = _radio_key(item)
    parts = bssid.split(":")
    if len(parts) != 6 or not all(re.fullmatch(r"[0-9a-f]{2}", part) for part in parts):
        return bssid

    first = int(parts[0], 16) & 0xFC
    last = int(parts[5], 16) & 0xF0
    return ":".join([f"{first:02x}", *parts[1:5], f"{last:02x}"])


def _signal_to_dbm(percent):
    # Good approximation for Windows netsh signal %
    return round((percent / 2) - 100)


def _estimate_distance_m(signal_dbm):
    try:
        rssi = abs(int(signal_dbm))
    except Exception:
        return None

    if rssi <= 55:
        distance = 3
    elif rssi <= 65:
        distance = 8
    elif rssi <= 72:
        distance = 20
    elif rssi <= 80:
        distance = 40
    elif rssi <= 88:
        distance = 70
    else:
        distance = 100

    return distance


def _severity_from_utilization(utilization):
    if utilization is None:
        return "ok"
    if utilization >= 70:
        return "danger"
    if utilization >= 50:
        return "warn"
    return "ok"


def _channel_load_label(item):
    source = item.get("channel_load_source")
    load = item.get("channel_load_percent")
    assessment = item.get("channel_load_assessment")

    if source == "reported" and load is not None:
        return f"{load}% reported"
    if source == "channel_reported" and load is not None:
        return f"{load}% channel"
    return assessment


def _assessment_for_ap(item):
    signal = int(item.get("signal_percent") or 0)
    channel = str(item.get("channel") or "")
    band = item.get("band", "unknown")
    load = item.get("channel_load_percent")
    load_source = item.get("channel_load_source")
    load_assessment = item.get("channel_load_assessment")
    reasons = []
    severity = _severity_from_utilization(load) if load_source in {"reported", "channel_reported"} else "ok"

    load_label = _channel_load_label(item)
    if load_source in {"reported", "channel_reported"} and load is not None:
        if load >= 70:
            reasons.append(f"high channel load ({load_label})")
        elif load >= 50:
            reasons.append(f"elevated channel load ({load_label})")
    elif load_assessment == "Overlap":
        reasons.append("adjacent channel overlap")
        severity = _raise_severity(severity, "warn")

    if band == "2.4GHz" and channel and channel not in {"1", "6", "11"}:
        reasons.append("2.4GHz channel is not 1, 6 or 11")
        severity = _raise_severity(severity, "danger" if signal >= 70 else "warn")

    if signal <= 25:
        reasons.append("weak signal")
        severity = _raise_severity(severity, "info")

    return {
        "severity": severity,
        "severity_label": _severity_label(severity),
        "reason": "; ".join(reasons) if reasons else "OK",
        "reasons": reasons,
    }


def _enrich_ap(item):
    enriched = dict(item)
    enriched["distance_m"] = _estimate_distance_m(enriched.get("signal_dbm"))
    return enriched


def _assess_channel_load(item, radios, reported_by_channel):
    if item.get("channel_utilization_percent") is not None:
        return item["channel_utilization_percent"], "reported", None

    channel = item.get("channel")
    band = item.get("band")
    if not channel or band == "unknown":
        return None, "unknown", "Unknown"

    channel_key = (band, str(channel))
    if channel_key in reported_by_channel:
        return reported_by_channel[channel_key], "channel_reported", None

    same_channel = 0
    adjacent_overlap = 0
    for other in radios:
        if other.get("band") != band or not other.get("channel"):
            continue

        distance = _channel_distance(other.get("channel"), channel)
        if distance == 0:
            same_channel += 1
        elif band == "2.4GHz" and distance < 5:
            adjacent_overlap += 1

    if adjacent_overlap:
        return None, "estimated", "Overlap"
    if same_channel >= 4:
        return None, "estimated", "Busy"
    if same_channel >= 2:
        return None, "estimated", "Shared"

    return None, "estimated", "Clear"


def _finalize_scan_items(items):
    enriched = [_enrich_ap(item) for item in items]
    channel_reports = defaultdict(list)

    for item in enriched:
        utilization = item.get("channel_utilization_percent")
        channel = item.get("channel")
        band = item.get("band")
        if utilization is not None and channel and band != "unknown":
            channel_reports[(band, str(channel))].append(utilization)

    reported_by_channel = {
        key: round(sum(values) / len(values))
        for key, values in channel_reports.items()
    }

    for item in enriched:
        load, source, assessment = _assess_channel_load(item, enriched, reported_by_channel)
        item["channel_load_percent"] = load
        item["channel_load_source"] = source
        item["channel_load_assessment"] = assessment
        item.update(_assessment_for_ap(item))

    return enriched


def _parse_netsh(output):
    results = []
    current_ssid = ""
    current_auth = ""
    current_encryption = ""
    current_bssid = None

    for line in output.splitlines():
        line = line.strip()

        ssid_match = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line)
        if ssid_match:
            current_ssid = ssid_match.group(1).strip()
            current_auth = ""
            current_encryption = ""
            current_bssid = None
            continue

        auth_match = re.match(r"^Authentication\s*:\s*(.*)$", line)
        if auth_match:
            current_auth = auth_match.group(1).strip()
            continue

        encryption_match = re.match(r"^Encryption\s*:\s*(.*)$", line)
        if encryption_match:
            current_encryption = encryption_match.group(1).strip()
            continue

        bssid_match = re.match(r"^BSSID\s+\d+\s*:\s*(.+)$", line)
        if bssid_match:
            current_bssid = {
                "ssid": current_ssid or "Hidden network",
                "bssid": bssid_match.group(1).strip(),
                "authentication": current_auth,
                "encryption": current_encryption,
                "signal_percent": 0,
                "signal_dbm": -100,
                "channel": "",
                "band": "unknown",
                "radio_type": "",
                "channel_width": "",
                "channel_utilization_percent": None,
                "connected_stations": None,
                "medium_available_capacity": "",
            }
            results.append(current_bssid)
            continue

        signal_match = re.match(r"^Signal\s*:\s*(.*)$", line)
        if current_bssid and signal_match:
            percent = _parse_signal_percent(signal_match.group(1))
            current_bssid["signal_percent"] = percent
            current_bssid["signal_dbm"] = _signal_to_dbm(percent)
            continue

        radio_match = re.match(r"^Radio type\s*:\s*(.*)$", line)
        if current_bssid and radio_match:
            current_bssid["radio_type"] = radio_match.group(1).strip()
            continue

        band_match = re.match(r"^Band\s*:\s*(.*)$", line)
        if current_bssid and band_match:
            band = band_match.group(1).strip().replace(" ", "")
            current_bssid["band"] = "2.4GHz" if band.startswith("2.4") else band
            continue

        width_match = re.match(r"^Channel width\s*:\s*(.*)$", line)
        if current_bssid and width_match:
            current_bssid["channel_width"] = width_match.group(1).strip()
            continue

        channel_match = re.match(r"^Channel\s*:\s*(.*)$", line)
        if current_bssid and channel_match:
            channel = _parse_channel(channel_match.group(1))
            current_bssid["channel"] = channel
            current_bssid["band"] = _channel_to_band(channel)
            continue

        stations_match = re.match(r"^Connected Stations\s*:\s*(\d+).*$", line, re.IGNORECASE)
        if current_bssid and stations_match:
            current_bssid["connected_stations"] = int(stations_match.group(1))
            continue

        utilization_match = re.match(r"^Channel Utilization\s*:\s*(.*)$", line, re.IGNORECASE)
        if current_bssid and utilization_match:
            current_bssid["channel_utilization_percent"] = _parse_utilization_percent(
                utilization_match.group(1)
            )
            continue

        capacity_match = re.match(r"^Medium Available Capacity\s*:\s*(.*)$", line, re.IGNORECASE)
        if current_bssid and capacity_match:
            current_bssid["medium_available_capacity"] = capacity_match.group(1).strip()
            continue

    return _finalize_scan_items(results)


def _group_results(results):
    grouped = defaultdict(list)

    for item in results:
        grouped[item["ssid"]].append(item)

    output = []

    for ssid, bssids in grouped.items():
        best = max(bssids, key=lambda x: x["signal_percent"])
        channels = sorted(
            {x["channel"] for x in bssids if x.get("channel")},
            key=_channel_sort_value,
        )
        loads = [
            x["channel_load_percent"]
            for x in bssids
            if x.get("channel_load_percent") is not None
        ]
        load_assessments = [
            x.get("channel_load_assessment")
            for x in bssids
            if x.get("channel_load_assessment")
        ]
        severity_order = {"danger": 3, "warn": 2, "info": 1, "ok": 0}
        severities = [x.get("severity", "ok") for x in bssids]
        non_weak_severities = [severity for severity in severities if severity != "info"]
        group_severity = max(
            non_weak_severities or severities,
            key=lambda x: severity_order.get(x, 0),
        )
        reasons = sorted(
            {
                reason
                for x in bssids
                for reason in x.get("reasons", [])
                if reason != "weak signal" or group_severity == "info"
            }
        )
        security = sorted(
            {
                " / ".join(filter(None, [x.get("authentication"), x.get("encryption")]))
                for x in bssids
                if x.get("authentication") or x.get("encryption")
            }
        )
        load_sources = {
            x.get("channel_load_source")
            for x in bssids
            if x.get("channel_load_percent") is not None
            or x.get("channel_load_assessment")
        }
        load_source = "reported" if load_sources == {"reported"} else "estimated"
        load_order = {"Overlap": 3, "Busy": 2, "Shared": 1, "Clear": 0, "Unknown": -1}
        load_assessment = max(
            load_assessments,
            key=lambda x: load_order.get(x, -1),
            default="Unknown",
        )

        output.append({
            "ssid": ssid,
            "band_summary": sorted(set(x["band"] for x in bssids)),
            "channels": channels,
            "channel_summary": ", ".join(channels) if channels else "-",
            "security_summary": ", ".join(security) if security else "-",
            "best_signal_percent": best["signal_percent"],
            "best_signal_dbm": best["signal_dbm"],
            "best_distance_m": best.get("distance_m"),
            "max_channel_load_percent": max(loads) if loads else None,
            "channel_load_source": load_source if loads or load_assessments else "unknown",
            "channel_load_assessment": load_assessment,
            "status": group_severity,
            "status_label": _severity_label(group_severity),
            "reason": "; ".join(reasons) if reasons else "OK",
            "reasons": reasons,
            "radio_count": len(bssids),
            "bssids": sorted(
                bssids,
                key=lambda x: (
                    x["band"],
                    _channel_sort_value(x.get("channel")),
                    -x["signal_percent"],
                )
            )
        })

    return sorted(output, key=lambda x: x["ssid"].lower())


def _channel_distance(a, b):
    try:
        return abs(int(a) - int(b))
    except Exception:
        return 999


def _strongest_by_radio(items):
    radios = {}

    for item in items:
        key = _radio_key(item)
        if not key:
            key = str(id(item))

        existing = radios.get(key)
        if existing is None or int(item.get("signal_percent") or 0) > int(existing.get("signal_percent") or 0):
            radios[key] = dict(item)

    return list(radios.values())


def _severity_label(severity):
    return {
        "danger": "Conflict",
        "warn": "Warning",
        "info": "Weak",
        "ok": "OK",
    }.get(severity, severity)


def _raise_severity(current, candidate):
    order = {"ok": 0, "info": 1, "warn": 2, "danger": 3}
    return candidate if order[candidate] > order[current] else current


def _add_reason(reasons, reason):
    if reason not in reasons:
        reasons.append(reason)


def _recommend_24ghz_channel(results):
    candidates = [1, 6, 11]
    band24 = [
        item for item in results
        if item.get("band") == "2.4GHz" and item.get("channel")
    ]

    if not band24:
        return {
            "channel": "",
            "message": "No 2.4GHz networks found.",
        }

    scores = {}
    for candidate in candidates:
        score = 0
        same_count = 0
        overlapping_count = 0

        for item in _strongest_by_radio(band24):
            distance = _channel_distance(item.get("channel"), candidate)
            signal = int(item.get("signal_percent") or 0)

            if distance == 0:
                same_count += 1
                score += signal * 2
            elif distance < 5:
                overlapping_count += 1
                score += signal

        scores[candidate] = {
            "channel": candidate,
            "score": score,
            "same_count": same_count,
            "overlapping_count": overlapping_count,
        }

    best = min(scores.values(), key=lambda x: (x["score"], x["same_count"], x["channel"]))
    return {
        **best,
        "message": (
            f"Recommended 2.4GHz channel: {best['channel']} "
            f"({best['same_count']} same-channel, "
            f"{best['overlapping_count']} overlapping AP radio(s))."
        ),
    }


def _recommend_5ghz_channel(results):
    unii_bands = [
        {
            "name": "U-NII-1",
            "range": "5150-5250 MHz",
            "channels": [36, 40, 44, 48],
            "note": "low band",
        },
        {
            "name": "U-NII-2A",
            "range": "5250-5350 MHz",
            "channels": [52, 56, 60, 64],
            "note": "DFS",
        },
        {
            "name": "U-NII-2C",
            "range": "5470-5725 MHz",
            "channels": [100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144],
            "note": "DFS",
        },
        {
            "name": "U-NII-3",
            "range": "5725-5850 MHz",
            "channels": [149, 153, 157, 161, 165],
            "note": "upper band",
        },
    ]
    band5 = [
        item for item in results
        if item.get("band") == "5GHz" and item.get("channel")
    ]

    if not band5:
        return {
            "channel": "",
            "message": "No 5GHz networks found.",
        }

    def score_channel(candidate):
        score = 0
        same_count = 0
        nearby_count = 0

        for item in _strongest_by_radio(band5):
            distance = _channel_distance(item.get("channel"), candidate)
            signal = int(item.get("signal_percent") or 0)

            if distance == 0:
                same_count += 1
                score += signal * 2
            elif distance <= 8:
                nearby_count += 1
                score += round(signal * 0.4)

        return {
            "channel": candidate,
            "score": score,
            "same_count": same_count,
            "nearby_count": nearby_count,
        }

    band_scores = []
    for band in unii_bands:
        channel_scores = [score_channel(channel) for channel in band["channels"]]
        best_channel = min(channel_scores, key=lambda x: (x["score"], x["same_count"], x["channel"]))
        occupied_channels = {
            int(item["channel"])
            for item in band5
            if _channel_sort_value(item.get("channel")) in band["channels"]
        }

        band_scores.append({
            "band": band["name"],
            "frequency_range": band["range"],
            "note": band["note"],
            "channel": best_channel["channel"],
            "score": best_channel["score"],
            "same_count": best_channel["same_count"],
            "nearby_count": best_channel["nearby_count"],
            "occupied_channel_count": len(occupied_channels),
            "total_channel_count": len(band["channels"]),
        })

    best = min(
        band_scores,
        key=lambda x: (
            x["score"],
            x["occupied_channel_count"],
            x["same_count"],
            x["channel"],
        )
    )
    note = f", {best['note']}" if best.get("note") else ""
    return {
        **best,
        "message": (
            f"Recommended 5GHz band: {best['band']} ({best['frequency_range']}{note}), "
            f"channel {best['channel']} "
            f"({best['same_count']} same-channel, "
            f"{best['nearby_count']} nearby AP radio(s))."
        ),
    }


def _recommend_channels(results):
    recommendations = [
        _recommend_24ghz_channel(results),
        _recommend_5ghz_channel(results),
    ]
    messages = [item["message"] for item in recommendations if item.get("message")]
    return {
        "items": recommendations,
        "message": " ".join(messages) if messages else "No channel recommendation yet.",
    }


def _analyze_conflicts(results):
    conflicts = []
    radios = _strongest_by_radio(results)

    for item in radios:
        channel = item.get("channel", "")
        band = item.get("band", "unknown")
        signal = item.get("signal_percent", 0)
        reasons = list(item.get("reasons", []))
        severity = item.get("severity", "ok")

        if not channel or band == "unknown":
            continue

        same_channel = _strongest_by_radio([
            other for other in radios
            if other is not item
            and other.get("band") == band
            and str(other.get("channel")) == str(channel)
            and _radio_key(other) != _radio_key(item)
        ])

        if same_channel and signal >= 40:
            strongest = max((x.get("signal_percent", 0) for x in same_channel), default=0)
            if strongest >= 40:
                _add_reason(reasons, f"{len(same_channel)} other AP radio(s) sharing channel {channel}")
                co_channel_severity = (
                    "warn"
                    if len(same_channel) >= 3 or (signal >= 75 and strongest >= 75)
                    else "info"
                )
                severity = _raise_severity(severity, co_channel_severity)

        if band == "2.4GHz":
            adjacent = _strongest_by_radio([
                other for other in radios
                if other is not item
                and other.get("band") == "2.4GHz"
                and str(other.get("channel")) != str(channel)
                and _radio_key(other) != _radio_key(item)
                and _channel_distance(other.get("channel"), channel) < 5
            ])

            if adjacent and signal >= 40:
                strongest_adjacent = max((x.get("signal_percent", 0) for x in adjacent), default=0)
                _add_reason(reasons, f"{len(adjacent)} adjacent overlapping AP radio(s)")
                adjacent_severity = "danger" if signal >= 70 and strongest_adjacent >= 70 else "warn"
                severity = _raise_severity(severity, adjacent_severity)

            if str(channel) not in {"1", "6", "11"}:
                _add_reason(reasons, "2.4GHz channel is not 1, 6 or 11")
                channel_severity = "danger" if signal >= 70 else "warn"
                severity = _raise_severity(severity, channel_severity)

        if signal <= 25:
            _add_reason(reasons, "weak signal")
            severity = _raise_severity(severity, "info")

        if reasons:
            conflicts.append({
                **item,
                "severity": severity,
                "severity_label": _severity_label(severity),
                "reasons": reasons,
                "reason": "; ".join(reasons),
            })

    return sorted(
        conflicts,
        key=lambda x: (
            {"danger": 0, "warn": 1, "info": 2, "ok": 3}.get(x["severity"], 3),
            x.get("band", ""),
            _channel_sort_value(x.get("channel")),
            -int(x.get("signal_percent") or 0),
        )
    )


def _scan_loop():
    global _wifi_running, _wifi_message, _wifi_results

    with _wifi_lock:
        _wifi_message = "Scanning..."

    while not _wifi_stop.is_set():
        try:
            raw = _run_netsh()
            parsed = _parse_netsh(raw)
            grouped = _group_results(parsed)

            with _wifi_lock:
                _wifi_results = grouped
                _wifi_message = f"Last scan found {len(grouped)} SSID(s)."
        except Exception as exc:
            with _wifi_lock:
                _wifi_message = f"WiFi scan error: {exc}"

        for _ in range(5):
            if _wifi_stop.is_set():
                break
            time.sleep(1)

    with _wifi_lock:
        _wifi_running = False
        _wifi_message = "Scan stopped."


def start_wifi_scan():
    global _wifi_thread, _wifi_running

    with _wifi_lock:
        if _wifi_running:
            return False, "WiFi scan already running."

        _wifi_stop.clear()
        _wifi_running = True

    _wifi_thread = threading.Thread(target=_scan_loop, daemon=True)
    _wifi_thread.start()

    return True, "WiFi scan started."


def stop_wifi_scan():
    with _wifi_lock:
        if not _wifi_running:
            return False, "WiFi scan is not running."

    _wifi_stop.set()
    return True, "Stopping WiFi scan..."


def get_wifi_status():
    with _wifi_lock:
        results = list(_wifi_results)
        flattened = [
            bssid
            for group in results
            for bssid in group.get("bssids", [])
        ]

        return {
            "running": _wifi_running,
            "message": _wifi_message,
            "results": results,
            "conflicts": _analyze_conflicts(flattened),
            "channel_recommendation": _recommend_channels(flattened),
        }
