import json
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import connection_history
import history
import history_store
import ping_utils
import scan_utils
import script_history


class HistoryStorageTests(unittest.TestCase):
    def test_interrupted_replace_preserves_primary_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            valid = lambda item: isinstance(item, str)
            history_store.update_list(path, valid, lambda old: (["first"], None))
            with patch.object(history_store.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    history_store.update_list(path, valid, lambda old: (old + ["second"], None))
            self.assertEqual(json.loads(path.read_text()), ["first"])
            self.assertEqual(history_store.load_list(path, valid), ["first"])
            self.assertEqual(list(path.glob("*.tmp")), [])

    def test_corruption_recovers_backup_and_reports_bad_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            history_store.update_list(path, lambda item: isinstance(item, str), lambda old: (["good"], None))
            path.write_text("{broken", encoding="utf-8")
            with warnings.catch_warnings(record=True) as notices:
                warnings.simplefilter("always")
                self.assertEqual(history_store.load_list(path, lambda item: isinstance(item, str)), ["good"])
            self.assertTrue(any("trying backup" in str(item.message) for item in notices))
            path.write_text('["good", 4]', encoding="utf-8")
            with warnings.catch_warnings(record=True) as notices:
                warnings.simplefilter("always")
                self.assertEqual(history_store.load_list(path, lambda item: isinstance(item, str)), ["good"])
            self.assertTrue(any("malformed records" in str(item.message) for item in notices))

    def test_overlapping_saves_keep_all_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ping.json"
            with patch.object(ping_utils, "PING_HISTORY_FILE", path):
                threads = [threading.Thread(target=ping_utils.save_ping_history_entry, args=(str(i),)) for i in range(20)]
                for thread in threads: thread.start()
                for thread in threads: thread.join()
                self.assertEqual(set(ping_utils.load_ping_history()), {str(i) for i in range(20)})

    def test_monitor_log_is_bounded(self):
        with scan_utils._scan_lock:
            old = scan_utils._monitor_log
            try:
                scan_utils._monitor_log = []
                for i in range(scan_utils._MAX_MONITOR_LOG + 5):
                    scan_utils._append_monitor_log_locked(str(i))
                self.assertEqual(len(scan_utils._monitor_log), scan_utils._MAX_MONITOR_LOG)
                self.assertTrue(scan_utils._monitor_log[-1].endswith(" 1004"))
            finally:
                scan_utils._monitor_log = old


if __name__ == "__main__":
    unittest.main()
