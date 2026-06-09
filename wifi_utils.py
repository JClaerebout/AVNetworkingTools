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


def _signal_to_dbm(percent):
    # Good approximation for Windows netsh signal %
    return round((percent / 2) - 100)


def _parse_netsh(output):
    results = []
    current_ssid = ""
    current_auth = ""
    current_encryption = ""
    current_bssid = None

    for line in output.splitlines():
        line = line.strip()

        ssid_match = re.match(r"SSID\s+\d+\s+:\s+(.*)", line)
        if ssid_match:
            current_ssid = ssid_match.group(1).strip()
            current_auth = ""
            current_encryption = ""
            continue

        if line.startswith("Authentication"):
            current_auth = line.split(":", 1)[1].strip()
            continue

        if line.startswith("Encryption"):
            current_encryption = line.split(":", 1)[1].strip()
            continue

        bssid_match = re.match(r"BSSID\s+\d+\s+:\s+(.*)", line)
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
            }
            results.append(current_bssid)
            continue

        if current_bssid and line.startswith("Signal"):
            percent = _parse_signal_percent(line.split(":", 1)[1])
            current_bssid["signal_percent"] = percent
            current_bssid["signal_dbm"] = _signal_to_dbm(percent)
            continue

        if current_bssid and line.startswith("Radio type"):
            current_bssid["radio_type"] = line.split(":", 1)[1].strip()
            continue

        if current_bssid and line.startswith("Channel"):
            channel = line.split(":", 1)[1].strip()
            current_bssid["channel"] = channel
            current_bssid["band"] = _channel_to_band(channel)
            continue

    return results


def _group_results(results):
    grouped = defaultdict(list)

    for item in results:
        grouped[item["ssid"]].append(item)

    output = []

    for ssid, bssids in grouped.items():
        best = max(bssids, key=lambda x: x["signal_percent"])

        output.append({
            "ssid": ssid,
            "band_summary": sorted(set(x["band"] for x in bssids)),
            "best_signal_percent": best["signal_percent"],
            "best_signal_dbm": best["signal_dbm"],
            "count": len(bssids),
            "bssids": sorted(
                bssids,
                key=lambda x: (x["band"], int(x["channel"] or 0), -x["signal_percent"])
            )
        })

    return sorted(output, key=lambda x: x["ssid"].lower())


def _channel_distance(a, b):
    try:
        return abs(int(a) - int(b))
    except Exception:
        return 999


def _severity_label(severity):
    return {
        "danger": "Conflict",
        "warn": "Warning",
        "info": "Info",
        "ok": "OK",
    }.get(severity, severity)


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

        for item in band24:
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
            f"{best['overlapping_count']} overlapping AP(s))."
        ),
    }


def _analyze_conflicts(results):
    conflicts = []

    for item in results:
        channel = item.get("channel", "")
        band = item.get("band", "unknown")
        signal = item.get("signal_percent", 0)
        reasons = []
        severity = "ok"

        if not channel or band == "unknown":
            continue

        same_channel = [
            other for other in results
            if other is not item
            and other.get("band") == band
            and str(other.get("channel")) == str(channel)
        ]

        if same_channel:
            strongest = max((x.get("signal_percent", 0) for x in same_channel), default=0)
            if strongest >= 40 or signal >= 40:
                reasons.append(f"{len(same_channel)} other AP(s) on channel {channel}")
                severity = "danger" if strongest >= 70 or signal >= 70 else "warn"

        if band == "2.4GHz":
            adjacent = [
                other for other in results
                if other is not item
                and other.get("band") == "2.4GHz"
                and str(other.get("channel")) != str(channel)
                and _channel_distance(other.get("channel"), channel) < 5
            ]

            if adjacent:
                reasons.append(f"{len(adjacent)} adjacent overlapping AP(s)")
                if severity == "ok":
                    severity = "warn"

            if str(channel) not in {"1", "6", "11"}:
                reasons.append("2.4GHz channel is not 1, 6 or 11")
                if severity == "ok":
                    severity = "warn"

        if signal <= 25:
            reasons.append("weak signal")
            if severity == "ok":
                severity = "info"

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
            int(x.get("channel") or 0),
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
            "channel_recommendation": _recommend_24ghz_channel(flattened),
        }
