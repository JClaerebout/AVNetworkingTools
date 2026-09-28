from datetime import datetime

from config import SCRIPT_HISTORY_FILE
from script_utils import _normalize_blocks
from history_store import load_list, update_list


MAX_SCRIPTS = 50


def load_scripts():
    return load_list(SCRIPT_HISTORY_FILE, _valid)


def _valid(item):
    return (isinstance(item, dict) and isinstance(item.get("name"), str)
            and bool(item["name"].strip()) and isinstance(item.get("blocks"), list)
            and all(isinstance(block, dict) and block.get("type") in {"target", "delay", "command"}
                    for block in item["blocks"]))


def list_scripts():
    return [
        {
            "name": item.get("name", ""),
            "timestamp": item.get("timestamp", ""),
            "block_count": len(item.get("blocks", [])),
        }
        for item in load_scripts()
        if item.get("name")
    ]


def get_script(name):
    clean_name = (name or "").strip()
    return next((item for item in load_scripts() if item.get("name") == clean_name), {})


def save_script(name, raw_blocks, overwrite=False):
    clean_name = (name or "").strip()
    if not clean_name:
        return False, "Name is required."
    if len(clean_name) > 80:
        return False, "Name can contain at most 80 characters."

    try:
        blocks = _normalize_blocks(raw_blocks)
    except ValueError as exc:
        return False, str(exc)

    # Passwords are deliberately run-only and never written to disk.
    for block in blocks:
        if block["type"] == "target":
            block["targets"] = "\n".join(block["targets"])
            block["password"] = ""

    def update(scripts):
        if any(item.get("name") == clean_name for item in scripts) and not overwrite:
            return None, (False, "NAME_EXISTS")
        scripts = [item for item in scripts if item.get("name") != clean_name]
        scripts.insert(0, {"name": clean_name, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "blocks": blocks})
        return scripts[:MAX_SCRIPTS], (True, "Script saved.")
    return update_list(SCRIPT_HISTORY_FILE, _valid, update)


def delete_script(name):
    clean_name = (name or "").strip()
    def update(scripts):
        remaining = [item for item in scripts if item.get("name") != clean_name]
        if len(remaining) == len(scripts):
            return None, (False, "Script not found.")
        return remaining, (True, "Script deleted.")
    return update_list(SCRIPT_HISTORY_FILE, _valid, update)
