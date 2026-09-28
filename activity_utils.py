"""Small, read-only snapshot of work that can outlive a page."""
from connection_utils import get_connection_activity
from multicast_utils import get_multicast_activity
from ping_utils import get_ping_activity
from scan_utils import get_scan_activity
from script_utils import get_script_activity
from wifi_utils import get_wifi_activity


def get_active_tasks() -> list[dict]:
    tasks = []
    scan = get_scan_activity()
    if scan["scan"] or scan["lookup"] or scan["monitor"]:
        phase = ("Scanning" if scan["scan"] else "Looking up details" if scan["lookup"]
                 else "Monitor paused" if scan["monitor_paused"] else "Monitoring")
        tasks.append({"key": "scan", "label": f"IP Scan · {phase}", "endpoint": "main.ip_scan_page"})
    if get_ping_activity():
        tasks.append({"key": "ping", "label": "Ping", "endpoint": "main.ping_page"})
    connection = get_connection_activity()
    if connection in {"Connecting", "Connected", "Stopping"}:
        label = "Connecting" if connection == "Connecting" else "Connection closing" if connection == "Stopping" else "Connection open"
        tasks.append({"key": "connection", "label": label, "endpoint": "main.connection_test_page"})
    script_running, script_paused = get_script_activity()
    if script_running:
        tasks.append({"key": "script", "label": "Script paused" if script_paused else "Script running", "endpoint": "main.scripts_page"})
    if get_wifi_activity():
        tasks.append({"key": "wifi", "label": "Wi-Fi scan", "endpoint": "main.wifi_scan_page"})
    if get_multicast_activity():
        tasks.append({"key": "health", "label": "Health Check", "endpoint": "main.multicast_page"})
    return tasks
