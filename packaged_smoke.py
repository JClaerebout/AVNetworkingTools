"""Headless runtime check for the PyInstaller executable.

Run the built EXE with --packaged-smoke RESULT_FILE. The app still starts its
real local server, but replaces the desktop window with a small route check.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import urlopen


def run(desktop_app, result_file):
    result_file = Path(result_file)
    checks = []
    window_url = None

    def create_window(_title, url, **_kwargs):
        nonlocal window_url
        window_url = url

    def inspect_window():
        if not window_url:
            raise AssertionError("Desktop window was not created")
        for route in ("/", "/ip-scan", "/connection-test", "/multicast"):
            with urlopen(window_url + route, timeout=5) as response:
                body = response.read()
                if response.status != 200 or b"<html" not in body.lower():
                    raise AssertionError(f"Navigation failed: {route}")
            checks.append(route)

        from export_utils import SaveCancelled, save_export
        fake_window = Mock()
        fake_window.create_file_dialog.return_value = None
        with patch.object(desktop_app.webview, "windows", [fake_window]), patch.object(Path, "write_bytes") as write:
            try:
                save_export("packaged-smoke.txt", "test")
            except SaveCancelled:
                pass
            else:
                raise AssertionError("Cancelled Save As was not reported")
            write.assert_not_called()
        checks.append("cancelled Save As")

    try:
        with patch.object(desktop_app, "start_manufacturer_database_update"), \
             patch.object(desktop_app.webview, "create_window", side_effect=create_window), \
             patch.object(desktop_app.webview, "start", side_effect=inspect_window):
            desktop_app.run_desktop()
        checks.append("shutdown")
        result = {"success": True, "checks": checks}
    except Exception as exc:
        result = {"success": False, "checks": checks, "error": str(exc)}

    result_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["success"] else 1
