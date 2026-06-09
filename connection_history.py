import json
from datetime import datetime
from typing import Dict, List, Tuple

from config import CONNECTION_HISTORY_FILE


MAX_CONNECTION_HISTORY = 50


def load_connection_history() -> List[Dict]:
    if not CONNECTION_HISTORY_FILE.exists():
        return []

    try:
        data = json.loads(CONNECTION_HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_connection_history_entry(entry: Dict, overwrite: bool = False) -> Tuple[bool, str]:
    history = load_connection_history()

    name = (entry.get("name") or "").strip()
    if not name:
        return False, "Name is required."

    existing = next((item for item in history if item.get("name") == name), None)

    if existing and not overwrite:
        return False, "NAME_EXISTS"

    history = [item for item in history if item.get("name") != name]

    entry["name"] = name
    entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    history.insert(0, entry)
    history = history[:MAX_CONNECTION_HISTORY]

    CONNECTION_HISTORY_FILE.write_text(
        json.dumps(history, indent=2),
        encoding="utf-8"
    )

    return True, "Saved."


def get_connection_history_entry(name: str) -> Dict:
    name = (name or "").strip()

    for item in load_connection_history():
        if item.get("name") == name:
            return item

    return {}