"""Atomic, process-local serialized storage for small JSON history lists."""
import json
import os
import tempfile
import threading
import warnings
from contextlib import contextmanager
from pathlib import Path


_locks = {}
_locks_guard = threading.Lock()


@contextmanager
def history_lock(path):
    key = str(Path(path).absolute())
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        yield


def _backup(path):
    return path.with_name(path.name + ".bak")


def _valid_list(path, valid):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("history root must be a list")
    clean = [item for item in data if valid(item)]
    if len(clean) != len(data):
        warnings.warn(f"Ignored {len(data) - len(clean)} malformed records in {path}", RuntimeWarning)
    return clean


def load_list(path, valid):
    """Load valid records, reporting corruption and recovering a last-good copy."""
    path = Path(path)
    with history_lock(path):
        if not path.exists():
            return []
        try:
            return _valid_list(path, valid)
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.warn(f"Could not read {path}: {exc}; trying backup", RuntimeWarning)
            backup = _backup(path)
            if backup.exists():
                try:
                    return _valid_list(backup, valid)
                except (OSError, ValueError, UnicodeError) as backup_exc:
                    warnings.warn(f"Could not read {backup}: {backup_exc}", RuntimeWarning)
            return []


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_list(path, valid, update, *, backup_new=False):
    """Run a read-modify-write transaction; update returns (new_list, result)."""
    path = Path(path)
    with history_lock(path):
        previous = load_list(path, valid)
        new, result = update(previous)
        if new is not None:
            # A corrupt primary must not replace a good backup with empty data.
            if backup_new:
                _atomic_write(_backup(path), new)
            elif path.exists():
                try:
                    old = _valid_list(path, valid)
                except (OSError, ValueError, UnicodeError):
                    old = None
                if old is not None:
                    _atomic_write(_backup(path), old)
            _atomic_write(path, new)
            if not _backup(path).exists():
                _atomic_write(_backup(path), new)
        return result
