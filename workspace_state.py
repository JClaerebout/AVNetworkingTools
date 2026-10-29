"""Persistent desktop drafts and snapshots, independent of the webview origin."""
import logging
import threading
from pathlib import Path

from config import BASE_DIR
from history_store import load_list, update_list

STATE_FILE = BASE_DIR / "workspace_state.json"
_workspace_lock = threading.RLock()


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
    with _workspace_lock:
        _snapshot_runtime()


def _snapshot_runtime():
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


def restore_runtime(data=None, *, replace=False):
    import scan_utils as scan
    import connection_utils as connection
    import ping_utils as ping
    import script_utils as script
    import wifi_utils as wifi
    if data is None:
        data = read_section("runtime")
    if not isinstance(data, dict):
        return
    with scan._scan_lock:
        if replace:
            scan._scan_results = []
            scan._scan_total = scan._scan_done = scan._lookup_total = scan._lookup_done = 0
            scan._large_scan_quick_only = False
            scan._monitor_log = []
            scan._last_scan_context = dict(interface="", source_ip="", network="", local_network="",
                                           hosts=[], quick_scan=False, completed=False)
            scan._scan_message = "Idle"
        for name, value in data.get("scan", {}).items():
            if name in {"_scan_results", "_scan_total", "_scan_done", "_lookup_total", "_lookup_done",
                        "_large_scan_quick_only", "_last_scan_context", "_monitor_log"}:
                setattr(scan, name, value)
        if scan._scan_results:
            scan._scan_message = "Restored previous scan (stopped)"
    with connection._conn_lock:
        if replace:
            connection._conn_output.clear()
            connection._session = None
        connection._conn_output.extend(data.get("connection", {}).get("output", []))
    with ping._ping_lock:
        if replace:
            ping._ping_output.clear()
        ping._ping_output.extend(data.get("ping", {}).get("output", []))
    with script._lock:
        if replace:
            script._state["output"].clear()
            script._state.update(status_text="Idle", current_block=None, paused=False)
        previous = data.get("script", {})
        script._state["output"].extend(previous.get("output", []))
        script._state["outcomes"] = previous.get("outcomes", {})
        if previous.get("output"):
            script._state["status_text"] = "Restored previous run (stopped)"
    with wifi._wifi_lock:
        wifi._wifi_results = data.get("wifi", {}).get("results", [])
        wifi._wifi_message = "Idle"
        if wifi._wifi_results:
            wifi._wifi_message = "Restored previous scan (stopped)"


def start_checkpointing():
    """Save during use as well as on normal exit; never restart network actions."""
    try:
        workspace_libraries()
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


LIBRARY_FILES = ("nic_history.json", "ping_history.json", "connection_history.json",
                 "connection_presets.json", "script_history.json")


def workspace_libraries():
    """Import previously shared libraries once, preserving existing saved workspaces."""
    import copy
    with _workspace_lock:
        records = load_list(STATE_FILE, lambda item: isinstance(item, dict))
        if any(item.get("name") == "libraries" for item in records):
            _migrate_default_workspace()
            return read_section("libraries")
        libraries = {name: load_list(BASE_DIR / name, lambda item: True) for name in LIBRARY_FILES}
        def migrate(records):
            for item in records:
                if item.get("name") == "saved" and isinstance(item.get("value"), dict):
                    item["value"].setdefault("libraries", copy.deepcopy(libraries))
                if item.get("name") == "workspaces":
                    for saved in item.get("value", {}).values():
                        saved.setdefault("libraries", copy.deepcopy(libraries))
            return records + [{"name": "libraries", "value": libraries}], None
        update_list(STATE_FILE, lambda item: isinstance(item, dict), migrate)
        _migrate_default_workspace()
        return libraries


def _migrate_default_workspace():
    """Snapshot pre-workspace data once; never recreate a deleted default."""
    import copy
    def migrate(records):
        sections = {item.get("name"): item.get("value") for item in records}
        if sections.get("default_workspace_migrated"):
            return None, None
        libraries = sections.get("libraries", {})
        drafts = sections.get("drafts", {})
        runtime = sections.get("runtime", {})
        workspaces = sections.get("workspaces", {})
        has_data = bool(drafts or runtime or any(libraries.values()))
        # Existing named workspace users keep their active workspace and saves.
        if has_data and not sections.get("current_name") and not workspaces:
            workspaces = dict(workspaces)
            workspaces["Default workspace"] = copy.deepcopy({
                "drafts": drafts, "runtime": runtime, "libraries": libraries})
            legacy = sections.get("saved")
            if legacy:
                workspaces["Saved workspace"] = copy.deepcopy(legacy)
            records = [item for item in records if item.get("name") not in {"workspaces", "current_name"}]
            records += [{"name": "workspaces", "value": workspaces},
                        {"name": "current_name", "value": "Default workspace"}]
        return records + [{"name": "default_workspace_migrated", "value": True}], None
    update_list(STATE_FILE, lambda item: isinstance(item, dict), migrate)


def _library_key(path):
    path = Path(path)
    return path.name if path.name in LIBRARY_FILES and path == BASE_DIR / path.name else None


def load_workspace_list(path, valid):
    key = _library_key(path)
    if key is None:
        return load_list(path, valid)
    with _workspace_lock:
        return [item for item in workspace_libraries().get(key, []) if valid(item)]


def update_workspace_list(path, valid, update, *, backup_new=False):
    key = _library_key(path)
    if key is None:
        return update_list(path, valid, update, backup_new=backup_new)
    with _workspace_lock:
        workspace_libraries()
        def transaction(records):
            libraries = next(item["value"] for item in records if item.get("name") == "libraries")
            new, result = update([item for item in libraries.get(key, []) if valid(item)])
            if new is None:
                return None, result
            libraries[key] = new
            return records, result
        return update_list(STATE_FILE, lambda item: isinstance(item, dict), transaction,
                           backup_new=backup_new)


def saved_workspaces():
    """Migrate the original single save without losing it or reviving deletions."""
    with _workspace_lock:
        workspace_libraries()
        records = load_list(STATE_FILE, lambda item: isinstance(item, dict))
        if any(item.get("name") == "workspaces" for item in records):
            return read_section("workspaces")
        legacy = read_section("saved")
        workspaces = {"Saved workspace": legacy} if legacy else {}
        write_section("workspaces", workspaces)
        return workspaces


def workspace_action(action, name=None, *, overwrite=False, new_name=None):
    """Keep an explicit saved workspace separate from automatic checkpoints."""
    from activity_utils import get_active_tasks
    if action not in {"save", "reload", "new", "delete", "rename"}:
        raise ValueError("Unknown workspace action.")
    with _workspace_lock:
        workspaces = saved_workspaces()
        # The default name keeps older clients compatible.
        if name is None:
            name = "Saved workspace"
        if action != "new":
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
                raise ValueError("Enter a workspace name between 1 and 100 characters.")
            name = name.strip()
        if action == "rename":
            if not isinstance(new_name, str) or not new_name.strip() or len(new_name.strip()) > 100:
                raise ValueError("Enter a workspace name between 1 and 100 characters.")
            new_name = new_name.strip()
            if name not in workspaces:
                raise ValueError("Workspace no longer exists.")
            if new_name != name and new_name in workspaces:
                raise ValueError("A workspace with this name already exists.")
            workspaces[new_name] = workspaces.pop(name)
            def rename(records):
                records = [item for item in records if item.get("name") != "workspaces"]
                if read_section("current_name") == name:
                    records = [item for item in records if item.get("name") != "current_name"]
                    records.append({"name": "current_name", "value": new_name})
                return records + [{"name": "workspaces", "value": workspaces}], None
            update_list(STATE_FILE, lambda item: isinstance(item, dict), rename)
            return
        if action == "delete":
            if name not in workspaces:
                raise ValueError("Workspace no longer exists.")
            del workspaces[name]
            write_section("workspaces", workspaces)
            return
        if action in {"new", "reload"} and get_active_tasks():
            raise ValueError("Stop running tasks and disconnect before changing workspace.")
        if action == "save":
            if name in workspaces and not overwrite:
                raise ValueError("A workspace with this name already exists. Confirm replacement to save it.")
            snapshot_runtime()
            workspaces[name] = {"drafts": read_section("drafts"), "runtime": read_section("runtime"),
                                "libraries": workspace_libraries(),
                                "previous_network_configs": read_section("previous_network_configs"),
                                "api_setups": read_section("api_setups")}
            write_section("workspaces", workspaces)
            write_section("current_name", name)
            return
        saved = workspaces.get(name) if action == "reload" else {"drafts": {}, "runtime": {}}
        if action == "reload" and not saved:
            raise ValueError("Workspace not found. Save a workspace first.")
        drafts = dict(saved.get("drafts", {}))
        # Appearance is an application preference, independent of the workspace.
        theme = read_section("drafts").get("avNetworkingTools:theme")
        if theme is not None:
            drafts["avNetworkingTools:theme"] = theme
        restore_runtime(saved.get("runtime", {}), replace=True)
        def update(records):
            records = [item for item in records if item.get("name") not in {"drafts", "runtime", "current_name", "libraries", "previous_network_configs", "api_setups"}]
            return records + [{"name": "current_name", "value": name if action == "reload" else "new workspace"},
                              {"name": "libraries", "value": saved.get("libraries", {})},
                              {"name": "api_setups", "value": saved.get("api_setups", {})},
                              {"name": "previous_network_configs", "value": saved.get("previous_network_configs", {})},
                              {"name": "drafts", "value": drafts},
                              {"name": "runtime", "value": saved.get("runtime", {})}], None
        update_list(STATE_FILE, lambda item: isinstance(item, dict), update)
