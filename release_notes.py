"""Bundled highlights and installation-wide release-note acknowledgement."""
from config import BASE_DIR
from history_store import load_list, update_list
from version import APP_VERSION

STATE_FILE = BASE_DIR / "release_notes_seen.json"
# Keep these highlights short and update them with each release.
RELEASE_NOTES = (
    "Add and remove temporary secondary IPv4 addresses on network adapters.",
    "Drag NIC cards into your preferred order, saved with each workspace.",
    "Import saved scripts and Connect setups from other workspaces.",
    "Send selected IP Scan devices to a script and keep working in the scan.",
    "Restore HTTP and WebSocket connection options, results and logs with your workspace.",
)


def pending_release_notes():
    seen = load_list(STATE_FILE, lambda item: isinstance(item, str))
    return {"version": APP_VERSION, "highlights": RELEASE_NOTES} if APP_VERSION not in seen else None


def acknowledge_release_notes():
    def remember(seen):
        return (seen if APP_VERSION in seen else seen + [APP_VERSION]), None
    update_list(STATE_FILE, lambda item: isinstance(item, str), remember)
