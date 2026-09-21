from pathlib import Path
from threading import Lock

import webview


class SaveCancelled(Exception):
    pass


_dialog_lock = Lock()


def choose_save_path(filename):
    if not webview.windows:
        raise OSError("Save As requires the desktop application window.")
    suffix = Path(filename).suffix
    with _dialog_lock:
        selection = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=(f"Export file (*{suffix})", "All files (*.*)"),
        )
    if not selection:
        raise SaveCancelled()
    return Path(selection if isinstance(selection, str) else selection[0])


def save_export(filename, content):
    destination = choose_save_path(filename)
    destination.write_bytes(content.encode("utf-8"))
    return destination.resolve()
