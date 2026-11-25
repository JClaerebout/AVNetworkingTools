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
        for route in ("/", "/ip-scan", "/ping", "/connection-test", "/multicast"):
            with urlopen(window_url + route, timeout=30) as response:
                body = response.read()
                if response.status != 200 or b"<html" not in body.lower():
                    raise AssertionError(f"Navigation failed: {route}")
            checks.append(route)

        for route, marker in (("/nics/cards", b"nic-card"), ("/ip-scan/adapters", b"<option"),
                              ("/multicast/adapters", b"<option")):
            # Cold Windows WMI/PowerShell adapter enumeration can exceed five seconds.
            with urlopen(window_url + route, timeout=30) as response:
                body = response.read()
                if response.status != 200 or (marker not in body and b"No network adapters found" not in body):
                    raise AssertionError(f"Adapter controls failed to load: {route}")
            checks.append(route)

        import sys
        from threading import Event
        from network_probes import MacIcmp, WindowsIcmp
        backend = WindowsIcmp if sys.platform == "win32" else MacIcmp
        address = "127.0.0.1"
        route = backend(address, "", "").route()
        with backend(address, address, route.interface) as probe:
            reply = probe.send_probe(1, 1000, Event())
        if reply.kind != "reply" or reply.address != address or reply.ms is None:
            raise AssertionError(f"Source-bound diagnostic probe failed: {reply}")
        checks.append("source-bound IPv4 loopback probe")

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
