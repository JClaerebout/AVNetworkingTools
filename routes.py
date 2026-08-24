import csv
import io
import ipaddress
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for

from history import load_history
from ping_utils import get_ping_status, load_ping_history, start_ping, stop_ping
from nic_utils import clean_dns, get_nics, release_dhcp, renew_dhcp, set_dhcp, set_static
from scan_utils import get_monitor_log, get_scannable_nics, get_scan_status, start_lookup, start_scan, stop_scan, start_monitor, stop_monitor, set_monitor_paused
from connection_utils import get_connection_status, get_serial_ports, send_data, start_connection, stop_connection
from system_utils import is_admin
from wifi_utils import get_wifi_status, start_wifi_scan, stop_wifi_scan
from connection_history import (
    get_connection_history_entry,
    load_connection_history,
    save_connection_history_entry,
)
from command_utils import run_command
from config import DOWNLOADS_DIR
from update_utils import check_for_update, get_update_state, install_downloaded_update, start_update_download

main_bp = Blueprint("main", __name__)


@main_bp.route("/api/update/check")
def update_check():
    try:
        return jsonify({"success": True, **check_for_update()})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Could not check for updates: {exc}"}), 502


@main_bp.route("/api/update/download", methods=["POST"])
def update_download():
    success, message = start_update_download()
    return jsonify({"success": success, "message": message}), 200 if success else 409


@main_bp.route("/api/update/status")
def update_status():
    return jsonify({"success": True, **get_update_state()})


@main_bp.route("/api/update/install", methods=["POST"])
def update_install():
    success, message = install_downloaded_update()
    return jsonify({"success": success, "message": message}), 200 if success else 409


@main_bp.route("/")
def index():
    try:
        nics = get_nics()
    except Exception as exc:
        nics = []
        flash(f"Could not scan NICs: {exc}", "error")

    return render_template("index.html", nics=nics, history=load_history(), admin=is_admin())


@main_bp.route("/apply", methods=["POST"])
def apply_settings():
    interface_name = request.form.get("interface", "").strip()
    mode = request.form.get("mode", "").strip().lower()

    if not interface_name:
        flash("No interface selected.", "error")
        return redirect(url_for("main.index"))

    if mode == "dhcp":
        success, message = set_dhcp(interface_name)
    elif mode == "static":
        ip = request.form.get("ip", "").strip()
        subnet = request.form.get("subnet", "").strip()
        gateway = request.form.get("gateway", "").strip()
        dns_servers = clean_dns(request.form.get("dns", ""))
        success, message = set_static(interface_name, ip, subnet, gateway, dns_servers)
    else:
        success, message = False, "Invalid mode."

    if success and "WARNING" in message:
        flash(message, "error")
    else:
        flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@main_bp.route("/release", methods=["POST"])
def dhcp_release():
    interface_name = request.form.get("interface", "").strip()
    if not interface_name:
        flash("No interface selected.", "error")
        return redirect(url_for("main.index"))

    success, message = release_dhcp(interface_name)
    flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@main_bp.route("/renew", methods=["POST"])
def dhcp_renew():
    interface_name = request.form.get("interface", "").strip()
    if not interface_name:
        flash("No interface selected.", "error")
        return redirect(url_for("main.index"))

    success, message = renew_dhcp(interface_name)
    flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@main_bp.route("/ping")
def ping_page():
    return render_template("ping.html", ping_history=load_ping_history())


@main_bp.route("/ping/start", methods=["POST"])
def ping_start():
    data = request.get_json(silent=True) or {}
    ip = data.get("ip", "")
    success, message = start_ping(ip)
    return jsonify({"success": success, "message": message, **get_ping_status()})


@main_bp.route("/ping/stop", methods=["POST"])
def ping_stop():
    success, message = stop_ping()
    return jsonify({"success": success, "message": message, **get_ping_status()})


@main_bp.route("/ping/status")
def ping_status():
    return jsonify(get_ping_status())


def _save_download(filename, content):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOADS_DIR / filename
    counter = 2
    while destination.exists():
        destination = DOWNLOADS_DIR / f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
        counter += 1
    destination.write_bytes(content.encode("utf-8"))
    return destination.resolve()


@main_bp.route("/ping/export.txt", methods=["GET", "POST"])
def ping_export():
    lines = get_ping_status().get("output", [])
    safe_lines = [str(line).replace("\r\n", "\n").replace("\r", "\n") for line in lines]
    content = "\r\n".join(safe_lines) if safe_lines else "No ping output available."
    filename = f"ping-result-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"

    if request.method == "POST":
        try:
            destination = _save_download(filename, content + "\r\n")
        except OSError as exc:
            return jsonify({"success": False, "message": f"Could not save TXT: {exc}"}), 500
        return jsonify({"success": True, "filename": filename, "path": str(destination)})

    return Response(
        content + "\r\n",
        content_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@main_bp.route("/command-line")
def command_line_page():
    return render_template("command_line.html", working_directory=str(Path.cwd()))


@main_bp.route("/command-line/run", methods=["POST"])
def command_line_run():
    data = request.get_json(silent=True) or {}
    result = run_command(
        data.get("command", ""),
        data.get("working_directory", ""),
    )
    return jsonify(result)



@main_bp.route("/connection-test")
def connection_test_page():
    return render_template("connection_test.html")


@main_bp.route("/connection-test/start", methods=["POST"])
def connection_test_start():
    data = request.get_json(silent=True) or {}
    success, message = start_connection(
        data.get("protocol", ""),
        data.get("host", ""),
        data.get("port", ""),
        data.get("username", ""),
        data.get("password", ""),
        data.get("baudrate", "9600"),
        data.get("databits", "8"),
        data.get("parity", "N"),
        data.get("stopbits", "1"),
    )
    return jsonify({"success": success, "message": message, **get_connection_status()})

@main_bp.route("/connection-test/serial-ports")
def connection_test_serial_ports():
    return jsonify({"ports": get_serial_ports()})

@main_bp.route("/connection-test/send", methods=["POST"])
def connection_test_send():
    data = request.get_json(silent=True) or {}
    success, message = send_data(
        data.get("data", ""),
        bool(data.get("is_hex", False)),
        bool(data.get("add_cr", False)),
        bool(data.get("add_lf", False)),
    )
    return jsonify({"success": success, "message": message, **get_connection_status()})


@main_bp.route("/connection-test/stop", methods=["POST"])
def connection_test_stop():
    success, message = stop_connection()
    return jsonify({"success": success, "message": message, **get_connection_status()})


@main_bp.route("/connection-test/status")
def connection_test_status():
    return jsonify(get_connection_status())


@main_bp.route("/ip-scan")
def ip_scan_page():
    try:
        nics = get_scannable_nics()
    except Exception as exc:
        nics = []
        flash(f"Could not load scan NICs: {exc}", "error")

    return render_template("ip_scan.html", nics=nics)


@main_bp.route("/ip-scan/start", methods=["POST"])
def ip_scan_start():
    data = request.get_json(silent=True) or {}
    interface_name = data.get("interface", "").strip()
    custom_subnet = data.get("custom_subnet", "").strip()
    quick_scan = bool(data.get("quick_scan", False))

    success, message = start_scan(interface_name, custom_subnet, quick_scan)
    return jsonify({"success": success, "message": message, **get_scan_status()})


@main_bp.route("/ip-scan/stop", methods=["POST"])
def ip_scan_stop():
    success, message = stop_scan()
    return jsonify({"success": success, "message": message, **get_scan_status()})


@main_bp.route("/ip-scan/lookup", methods=["POST"])
def ip_scan_lookup():
    success, message = start_lookup()
    return jsonify({"success": success, "message": message, **get_scan_status()})


@main_bp.route("/ip-scan/status")
def ip_scan_status():
    return jsonify(get_scan_status())


@main_bp.route("/ip-scan/open-web", methods=["POST"])
def ip_scan_open_web():
    data = request.get_json(silent=True) or {}
    ip = str(data.get("ip", "")).strip()
    scheme = str(data.get("scheme", "")).strip().lower()

    try:
        parsed_ip = ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid device IP address."}), 400

    if parsed_ip.version != 4 or scheme not in {"http", "https"}:
        return jsonify({"success": False, "message": "Invalid webpage address."}), 400

    result = next(
        (item for item in get_scan_status().get("results", []) if item.get("ip") == ip),
        None,
    )
    if not result or scheme not in result.get("web_services", []):
        return jsonify({"success": False, "message": "That webpage is no longer available."}), 409

    url = f"{scheme}://{ip}/"
    if not webbrowser.open(url, new=2):
        return jsonify({"success": False, "message": "Could not open the default browser."}), 500

    return jsonify({"success": True, "url": url})


def _safe_csv_value(value):
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _scan_result_status(item):
    if item.get("is_local"):
        return "This PC"
    if item.get("missing"):
        return "Missing"
    if item.get("duplicate_ip"):
        return "Duplicate IP"
    return "OK"


def _scan_csv_content(results):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["IP", "MAC", "Manufacturer", "Hostname", "Status"])

    for item in results:
        writer.writerow([
            _safe_csv_value(item.get("ip")),
            _safe_csv_value(item.get("mac")),
            _safe_csv_value(item.get("manufacturer") or "Unknown"),
            _safe_csv_value(item.get("hostname")),
            _scan_result_status(item),
        ])

    return "\ufeff" + output.getvalue()


@main_bp.route("/ip-scan/export.csv", methods=["GET", "POST"])
def ip_scan_export():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        visible_results = data.get("results")
        if not isinstance(visible_results, list) or not all(isinstance(item, dict) for item in visible_results):
            return jsonify({"success": False, "message": "Invalid visible scan results."}), 400
        if len(visible_results) > 1024:
            return jsonify({"success": False, "message": "Too many scan results to export."}), 400
        results = visible_results
    else:
        results = get_scan_status().get("results", [])

    content = _scan_csv_content(results)
    filename = f"ip-scan-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"

    if request.method == "POST":
        try:
            destination = _save_download(filename, content)
        except OSError as exc:
            return jsonify({"success": False, "message": f"Could not save CSV: {exc}"}), 500
        return jsonify({
            "success": True,
            "filename": filename,
            "path": str(destination),
            "count": len(results),
        })

    return Response(
        content,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@main_bp.route("/ip-scan/monitor/start", methods=["POST"])
def ip_scan_monitor_start():
    success, message = start_monitor()
    return jsonify({"success": success, "message": message, **get_scan_status()})


@main_bp.route("/ip-scan/monitor/stop", methods=["POST"])
def ip_scan_monitor_stop():
    success, message = stop_monitor()
    return jsonify({"success": success, "message": message, **get_scan_status()})


@main_bp.route("/ip-scan/monitor/export.txt", methods=["GET", "POST"])
def ip_scan_monitor_export():
    lines = get_monitor_log()
    if not lines:
        message = "No monitoring log is available."
        if request.method == "POST":
            return jsonify({"success": False, "message": message}), 409
        return Response(message + "\r\n", status=409, content_type="text/plain; charset=utf-8")

    content = "\r\n".join(str(line).replace("\r", "").replace("\n", " ") for line in lines) + "\r\n"
    filename = f"ip-monitor-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"

    if request.method == "POST":
        try:
            destination = _save_download(filename, content)
        except OSError as exc:
            return jsonify({"success": False, "message": f"Could not save monitor log: {exc}"}), 500
        return jsonify({
            "success": True,
            "filename": filename,
            "path": str(destination),
            "count": len(lines),
        })

    return Response(
        content,
        content_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@main_bp.route("/ip-scan/monitor/pause", methods=["POST"])
def ip_scan_monitor_pause():
    data = request.get_json(silent=True) or {}
    paused = bool(data.get("paused", False))

    success, message = set_monitor_paused(paused)
    return jsonify({"success": success, "message": message, **get_scan_status()})

@main_bp.route("/wifi-scan")
def wifi_scan_page():
    return render_template("wifi_scan.html")


@main_bp.route("/wifi-scan/start", methods=["POST"])
def wifi_scan_start():
    success, message = start_wifi_scan()
    return jsonify({"success": success, "message": message, **get_wifi_status()})


@main_bp.route("/wifi-scan/stop", methods=["POST"])
def wifi_scan_stop():
    success, message = stop_wifi_scan()
    return jsonify({"success": success, "message": message, **get_wifi_status()})


@main_bp.route("/wifi-scan/status")
def wifi_scan_status():
    return jsonify(get_wifi_status())

@main_bp.route("/connection-test/history")
def connection_test_history():
    return jsonify({
        "history": load_connection_history()
    })


@main_bp.route("/connection-test/history/<name>")
def connection_test_history_entry(name):
    entry = get_connection_history_entry(name)
    if not entry:
        return jsonify({"success": False, "message": "History entry not found."})

    return jsonify({
        "success": True,
        "entry": entry
    })


@main_bp.route("/connection-test/history/save", methods=["POST"])
def connection_test_history_save():
    data = request.get_json(silent=True) or {}

    success, message = save_connection_history_entry(
        data,
        overwrite=bool(data.get("overwrite"))
    )

    return jsonify({
        "success": success,
        "message": message
    })
