from typing import Dict, List

from config import HISTORY_FILE
from history_store import load_list, update_list


def load_history() -> List[Dict]:
    return load_list(HISTORY_FILE, lambda item: isinstance(item, dict) and isinstance(item.get("interface"), str) and isinstance(item.get("ip"), str))


def save_history_entry(entry: Dict) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("interface"), str) or not isinstance(entry.get("ip"), str):
        raise ValueError("History entry requires an interface and IP address.")

    def update(history):
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
        return history[:10], None

    update_list(HISTORY_FILE, lambda item: isinstance(item, dict) and isinstance(item.get("interface"), str) and isinstance(item.get("ip"), str), update)
