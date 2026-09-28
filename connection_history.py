from datetime import datetime
from typing import Dict, List, Tuple

from config import CONNECTION_HISTORY_FILE
from history_store import load_list, update_list

MAX_CONNECTION_HISTORY = 50


def _valid(item):
    return isinstance(item, dict) and isinstance(item.get("name"), str) and bool(item["name"].strip())


def load_connection_history() -> List[Dict]:
    return load_list(CONNECTION_HISTORY_FILE, _valid)


def save_connection_history_entry(entry: Dict, overwrite: bool = False) -> Tuple[bool, str]:
    name = (entry.get("name") or "").strip() if isinstance(entry, dict) else ""
    if not name:
        return False, "Name is required."

    def update(history):
        if any(item.get("name") == name for item in history) and not overwrite:
            return None, (False, "NAME_EXISTS")
        record = dict(entry, name=name, timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        history = [item for item in history if item.get("name") != name]
        history.insert(0, record)
        return history[:MAX_CONNECTION_HISTORY], (True, "Saved.")

    return update_list(CONNECTION_HISTORY_FILE, _valid, update)


def get_connection_history_entry(name: str) -> Dict:
    name = (name or "").strip()
    return next((item for item in load_connection_history() if item.get("name") == name), {})
