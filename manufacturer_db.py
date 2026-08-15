import csv
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from config import MANUFACTURER_DB_FILE


DATABASE_VERSION = 1
UPDATE_INTERVAL_SECONDS = 7 * 24 * 60 * 60
IEEE_CSV_URLS = {
    24: "https://standards-oui.ieee.org/oui/oui.csv",
    28: "https://standards-oui.ieee.org/oui28/mam.csv",
    36: "https://standards-oui.ieee.org/oui36/oui36.csv",
}

_db_lock = threading.Lock()
_prefixes: Optional[Dict[int, Dict[str, str]]] = None
_update_started = False


def _bundled_database_path() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        root = Path(__file__).resolve().parent
    return root / "manufacturer_data" / "ieee_manufacturers.json"


def _normalize_prefixes(raw) -> Dict[int, Dict[str, str]]:
    if not isinstance(raw, dict) or raw.get("version") != DATABASE_VERSION:
        raise ValueError("Unsupported manufacturer database format.")

    source = raw.get("prefixes")
    if not isinstance(source, dict):
        raise ValueError("Manufacturer database has no prefix data.")

    normalized = {}
    for length in (24, 28, 36):
        values = source.get(str(length), {})
        if not isinstance(values, dict):
            raise ValueError(f"Invalid {length}-bit manufacturer data.")
        normalized[length] = {
            str(prefix).upper(): str(name).strip()
            for prefix, name in values.items()
            if name
        }

    if not normalized[24]:
        raise ValueError("Manufacturer database contains no MA-L assignments.")
    return normalized


def _load_database(path: Path) -> Dict[int, Dict[str, str]]:
    with path.open("r", encoding="utf-8") as handle:
        return _normalize_prefixes(json.load(handle))


def _ensure_loaded() -> Dict[int, Dict[str, str]]:
    global _prefixes

    with _db_lock:
        if _prefixes is not None:
            return _prefixes

        for path in (MANUFACTURER_DB_FILE, _bundled_database_path()):
            try:
                if path.is_file():
                    _prefixes = _load_database(path)
                    return _prefixes
            except (OSError, ValueError, json.JSONDecodeError):
                continue

        _prefixes = {24: {}, 28: {}, 36: {}}
        return _prefixes


def lookup_local_manufacturer(mac: str) -> str:
    clean = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
    if len(clean) != 12:
        return ""

    first_octet = int(clean[:2], 16)
    if first_octet & 0x03:
        # Multicast and locally administered/randomized MAC addresses do not
        # identify an IEEE-assigned hardware manufacturer.
        return ""

    prefixes = _ensure_loaded()
    for length in (36, 28, 24):
        name = prefixes[length].get(clean[: length // 4])
        if name:
            return name
    return ""


def _download_csv(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "AVNetKit/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig", errors="strict")


def _parse_ieee_csv(text: str, prefix_length: int) -> Dict[str, str]:
    results = {}
    expected_chars = prefix_length // 4

    for row in csv.DictReader(io.StringIO(text)):
        assignment = re.sub(r"[^0-9A-Fa-f]", "", row.get("Assignment", "")).upper()
        organization = (row.get("Organization Name") or "").strip()
        if len(assignment) == expected_chars and organization:
            results[assignment] = organization

    if not results:
        raise ValueError(f"IEEE {prefix_length}-bit CSV contained no assignments.")
    return results


def _database_payload(prefixes: Dict[int, Dict[str, str]]) -> Dict:
    return {
        "version": DATABASE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "prefixes": {str(length): prefixes[length] for length in (24, 28, 36)},
    }


def _write_database(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def update_manufacturer_database(output_path: Path = MANUFACTURER_DB_FILE, force: bool = False) -> bool:
    global _prefixes

    if not force and output_path.is_file():
        try:
            if time.time() - output_path.stat().st_mtime < UPDATE_INTERVAL_SECONDS:
                return False
        except OSError:
            pass

    downloaded = {
        length: _parse_ieee_csv(_download_csv(url), length)
        for length, url in IEEE_CSV_URLS.items()
    }
    payload = _database_payload(downloaded)
    _write_database(output_path, payload)

    if output_path == MANUFACTURER_DB_FILE:
        with _db_lock:
            _prefixes = downloaded
    return True


def _background_update() -> None:
    try:
        update_manufacturer_database()
    except Exception:
        # Lookup continues with the bundled or last successfully downloaded DB.
        pass


def start_manufacturer_database_update() -> None:
    global _update_started
    with _db_lock:
        if _update_started:
            return
        _update_started = True

    threading.Thread(target=_background_update, daemon=True).start()
