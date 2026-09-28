"""Bounded shutdown of tasks owned by the desktop process."""
import logging
import threading


def stop_background_tasks():
    import connection_utils
    import multicast_utils
    import ping_utils
    import scan_utils
    import script_utils
    import wifi_utils

    tasks = (
        ("scan", scan_utils.stop_scan, (scan_utils, "_scan_thread"), (scan_utils, "_lookup_thread")),
        ("monitor", scan_utils.stop_monitor, (scan_utils, "_monitor_thread")),
        ("ping", ping_utils.stop_ping, (ping_utils, "_ping_thread")),
        ("connection", connection_utils.stop_connection),
        ("scripts", script_utils.stop_script, (script_utils, "_thread")),
        ("Wi-Fi", wifi_utils.stop_wifi_scan, (wifi_utils, "_wifi_thread")),
        ("capture", multicast_utils.stop_multicast_test),
    )
    for name, stop, *threads in tasks:
        try:
            stop()
        except Exception:
            logging.exception("Could not stop %s during shutdown", name)
        for module, attribute in threads:
            thread = getattr(module, attribute, None)
            if thread is not None and thread is not threading.current_thread():
                try:
                    thread.join(timeout=1)
                    if thread.is_alive():
                        logging.warning("%s worker did not stop within shutdown deadline", name)
                except RuntimeError:
                    pass
