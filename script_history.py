import json
from datetime import datetime

from config import SCRIPT_HISTORY_FILE
from script_utils import _normalize_blocks


MAX_SCRIPTS = 50


def load_scripts():
    if not SCRIPT_HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(SCRIPT_HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


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

    scripts = load_scripts()
    exists = any(item.get("name") == clean_name for item in scripts)
    if exists and not overwrite:
        return False, "NAME_EXISTS"

    scripts = [item for item in scripts if item.get("name") != clean_name]
    scripts.insert(0, {
        "name": clean_name,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "blocks": blocks,
    })
    SCRIPT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_HISTORY_FILE.write_text(json.dumps(scripts[:MAX_SCRIPTS], indent=2), encoding="utf-8")
    return True, "Script saved."


def delete_script(name):
    clean_name = (name or "").strip()
    scripts = load_scripts()
    remaining = [item for item in scripts if item.get("name") != clean_name]
    if len(remaining) == len(scripts):
        return False, "Script not found."
    SCRIPT_HISTORY_FILE.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
    return True, "Script deleted."
