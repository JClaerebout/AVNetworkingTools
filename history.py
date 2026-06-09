import json
from typing import Dict, List

from config import HISTORY_FILE


def load_history() -> List[Dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history_entry(entry: Dict) -> None:
    history = load_history()

    duplicate_key = (
        entry.get("interface"),
        entry.get("ip"),
        entry.get("subnet"),
        entry.get("gateway"),
        entry.get("dns1"),
        entry.get("dns2"),
    )

    history = [
        item for item in history
        if (
            item.get("interface"),
            item.get("ip"),
            item.get("subnet"),
            item.get("gateway"),
            item.get("dns1"),
            item.get("dns2"),
        ) != duplicate_key
    ]

    history.insert(0, entry)
    history = history[:10]
    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
