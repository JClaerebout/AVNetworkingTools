"""Persistent desktop drafts and snapshots, independent of the webview origin."""
import logging
import threading

from config import BASE_DIR
from history_store import load_list, update_list

STATE_FILE = BASE_DIR / "workspace_state.json"


def read_section(name):
    records = load_list(STATE_FILE, lambda item: isinstance(item, dict))
    return next((item.get("value", {}) for item in records if item.get("name") == name), {})


def write_section(name, value):
    def update(records):
        records = [item for item in records if item.get("name") != name]
        records.append({"name": name, "value": value})
        return records, None
    update_list(STATE_FILE, lambda item: isinstance(item, dict), update)


def clean_draft(key, value):
    """Only accept known UI drafts; credentials never belong in field drafts."""
    import json
    if not isinstance(key, str) or not isinstance(value, str):
        raise ValueError("Invalid draft")
    if key.startswith("avNetworkingTools:page-fields:"):
        fields = json.loads(value)
        if not isinstance(fields, dict):
            raise ValueError("Invalid fields")
        value = json.dumps({name: state for name, state in fields.items()
                            if not any(word in name.lower() for word in ("password", "secret", "token"))})
    elif key == "avNetworkingTools:scriptDraft:v1":
        blocks = json.loads(value)
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise ValueError("Invalid script")
        value = json.dumps([{**block, "password": ""} for block in blocks])
    elif key not in {"avNetworkingTools:connection-send-rows", "avNetworkingTools:theme", "autoRefreshEnabled"}:
        raise ValueError("Unknown draft")
    return value


def save_draft(key, value):
    value = clean_draft(key, value)
    def update(records):
        drafts = next((item["value"] for item in records if item.get("name") == "drafts"), {})
        drafts[key] = value
        return [item for item in records if item.get("name") != "drafts"] + [{"name": "drafts", "value": drafts}], None
    update_list(STATE_FILE, lambda item: isinstance(item, dict), update)


def snapshot_runtime():
    import scan_utils as scan
    import connection_utils as connection
    import ping_utils as ping
    import script_utils as script
    import wifi_utils as wifi
    with scan._scan_lock:
        scan_state = {name: getattr(scan, name) for name in (
            "_scan_results", "_scan_total", "_scan_done", "_lookup_total", "_lookup_done",
            "_large_scan_quick_only", "_last_scan_context", "_monitor_log")}
        # Copy while holding the lock: workers may modify these containers.
        import copy
        scan_state = copy.deepcopy(scan_state)
    write_section("runtime", {"scan": scan_state, "connection": connection.get_connection_status(),
                             "ping": ping.get_ping_status(), "script": script.get_script_status(),
                             "wifi": wifi.get_wifi_status()})


def restore_runtime():
    import scan_utils as scan
    import connection_utils as connection
    import ping_utils as ping
    import script_utils as script
    import wifi_utils as wifi
    data = read_section("runtime")
    if not isinstance(data, dict):
        return
    with scan._scan_lock:
        for name, value in data.get("scan", {}).items():
            if name in {"_scan_results", "_scan_total", "_scan_done", "_lookup_total", "_lookup_done",
                        "_large_scan_quick_only", "_last_scan_context", "_monitor_log"}:
                setattr(scan, name, value)
        if scan._scan_results:
            scan._scan_message = "Restored previous scan (stopped)"
    with connection._conn_lock:
        connection._conn_output.extend(data.get("connection", {}).get("output", []))
    with ping._ping_lock:
        ping._ping_output.extend(data.get("ping", {}).get("output", []))
    with script._lock:
        previous = data.get("script", {})
        script._state["output"].extend(previous.get("output", []))
        script._state["outcomes"] = previous.get("outcomes", {})
        if previous.get("output"):
            script._state["status_text"] = "Restored previous run (stopped)"
    with wifi._wifi_lock:
        wifi._wifi_results = data.get("wifi", {}).get("results", [])
        if wifi._wifi_results:
            wifi._wifi_message = "Restored previous scan (stopped)"


def start_checkpointing():
    """Save during use as well as on normal exit; never restart network actions."""
    try:
        restore_runtime()
    except Exception:
        logging.exception("Could not restore workspace")
    stopped = threading.Event()
    def checkpoint():
        while not stopped.wait(5):
            try:
                snapshot_runtime()
            except Exception:
                logging.exception("Could not save workspace")
    thread = threading.Thread(target=checkpoint, daemon=True, name="workspace-checkpoint")
    thread.start()
    def finish():
        stopped.set()
        thread.join(timeout=2)
        try:
            snapshot_runtime()
        except Exception:
            logging.exception("Could not save workspace on exit")
    return finish
