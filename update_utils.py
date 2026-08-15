import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from version import APP_VERSION, GITHUB_REPOSITORY


LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
USER_AGENT = f"Windows-NIC-Manager/{APP_VERSION}"
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_ASSET_NAMES = {"networkmanager.exe", "network.manager.exe", "network manager.exe"}
_state_lock = threading.Lock()
_update_state = {
    "status": "idle",
    "message": "",
    "downloaded_bytes": 0,
    "total_bytes": 0,
}


def _version_parts(value: str):
    match = re.fullmatch(r"[vV]?(\d+(?:\.\d+)*)", (value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    if candidate_parts is None or current_parts is None:
        return False

    width = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (width - len(candidate_parts)) > current_parts + (0,) * (width - len(current_parts))


def _read_json(url: str, timeout: int = 8):
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _select_executable_asset(assets):
    executable_assets = [asset for asset in assets if str(asset.get("name", "")).lower() in _ASSET_NAMES]
    return executable_assets[0] if len(executable_assets) == 1 else None


def check_for_update():
    release = _read_json(LATEST_RELEASE_URL)
    tag = str(release.get("tag_name", "")).strip()
    asset = _select_executable_asset(release.get("assets", []))
    available = is_newer_version(tag)

    result = {
        "available": available,
        "current_version": APP_VERSION,
        "latest_version": tag.lstrip("vV") or APP_VERSION,
        "release_name": release.get("name") or tag,
        "release_url": release.get("html_url", ""),
        "published_at": release.get("published_at", ""),
        "can_auto_update": bool(getattr(sys, "frozen", False)),
    }

    if available and asset is None:
        result["error"] = "The latest release does not contain exactly one supported Network Manager EXE asset."
    return result


def _set_state(**values):
    with _state_lock:
        _update_state.update(values)


def get_update_state():
    with _state_lock:
        return dict(_update_state)


def _download_latest_release():
    try:
        release = _read_json(LATEST_RELEASE_URL)
        tag = str(release.get("tag_name", "")).strip()
        asset = _select_executable_asset(release.get("assets", []))
        if not is_newer_version(tag):
            raise RuntimeError("No newer release is available.")
        if asset is None:
            raise RuntimeError("The release does not contain exactly one supported EXE asset.")

        download_url = str(asset.get("browser_download_url", ""))
        digest = str(asset.get("digest", ""))
        if urlparse(download_url).scheme != "https" or urlparse(download_url).hostname != "github.com":
            raise RuntimeError("GitHub returned an invalid asset URL.")
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise RuntimeError("The release asset has no valid SHA-256 digest; update cancelled.")

        update_dir = Path(tempfile.gettempdir()) / "WindowsNICManagerUpdate" / tag
        update_dir.mkdir(parents=True, exist_ok=True)
        destination = update_dir / "NetworkManager.exe"
        partial = update_dir / "NetworkManager.exe.part"
        request = Request(download_url, headers={"User-Agent": USER_AGENT})
        hasher = hashlib.sha256()
        downloaded = 0

        with urlopen(request, timeout=30) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length", asset.get("size", 0)) or 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("The update is larger than the allowed download size.")
            _set_state(total_bytes=total)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("The update is larger than the allowed download size.")
                output.write(chunk)
                hasher.update(chunk)
                _set_state(downloaded_bytes=downloaded)

        expected_hash = digest.split(":", 1)[1].lower()
        if hasher.hexdigest().lower() != expected_hash:
            partial.unlink(missing_ok=True)
            raise RuntimeError("The downloaded update failed SHA-256 verification.")

        partial.replace(destination)
        _set_state(
            status="ready",
            message="Update downloaded and verified. Restarting to install...",
            downloaded_path=str(destination),
            version=tag.lstrip("vV"),
        )
    except Exception as exc:
        _set_state(status="error", message=str(exc))


def start_update_download():
    if not getattr(sys, "frozen", False):
        return False, "Automatic replacement is only available in the packaged EXE."

    with _state_lock:
        if _update_state["status"] in {"downloading", "ready", "installing"}:
            return True, _update_state["message"]
        _update_state.update({
            "status": "downloading",
            "message": "Downloading update...",
            "downloaded_bytes": 0,
            "total_bytes": 0,
        })

    threading.Thread(target=_download_latest_release, daemon=True).start()
    return True, "Downloading update..."


def _updater_script():
    return r'''param(
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target
)
$ErrorActionPreference = "Stop"
$staged = "$Target.update"
$backup = "$Target.old"
try {
    Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $Source -Destination $staged -Force
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    Move-Item -LiteralPath $Target -Destination $backup -Force
    try {
        Move-Item -LiteralPath $staged -Destination $Target -Force
    } catch {
        Move-Item -LiteralPath $backup -Destination $Target -Force
        throw
    }
    Start-Process -FilePath $Target
} finally {
    if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged -Force }
}
'''


def install_downloaded_update():
    if not getattr(sys, "frozen", False):
        return False, "Automatic replacement is only available in the packaged EXE."

    state = get_update_state()
    if state.get("status") != "ready":
        return False, "The update has not finished downloading."

    source = Path(state["downloaded_path"]).resolve()
    target = Path(sys.executable).resolve()
    if not source.is_file() or source.suffix.lower() != ".exe":
        return False, "The verified update file is missing."

    script_path = source.parent / "install-update.ps1"
    script_path.write_text(_updater_script(), encoding="utf-8")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script_path),
            "-ProcessId", str(os.getpid()),
            "-Source", str(source),
            "-Target", str(target),
        ],
        close_fds=True,
        creationflags=creation_flags,
    )
    _set_state(status="installing", message="Installing update and restarting...")
    threading.Timer(1.0, os._exit, args=(0,)).start()
    return True, "Installing update and restarting..."
