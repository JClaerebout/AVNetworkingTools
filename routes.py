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
from multicast_utils import get_multicast_status, start_multicast_test, stop_multicast_test
from script_utils import get_script_status, set_script_paused, start_script, stop_script
from script_history import delete_script, get_script, list_scripts, save_script

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


@main_bp.route("/scripts")
def scripts_page():
    return render_template("scripts.html")


@main_bp.route("/scripts/start", methods=["POST"])
def scripts_start():
    data = request.get_json(silent=True) or {}
    success, message = start_script(data.get("blocks"))
    return jsonify({"success": success, "message": message, **get_script_status()}), 200 if success else 409


@main_bp.route("/scripts/pause", methods=["POST"])
def scripts_pause():
    data = request.get_json(silent=True) or {}
    success, message = set_script_paused(bool(data.get("paused")))
    return jsonify({"success": success, "message": message, **get_script_status()}), 200 if success else 409


@main_bp.route("/scripts/stop", methods=["POST"])
def scripts_stop():
    success, message = stop_script()
    return jsonify({"success": success, "message": message, **get_script_status()}), 200 if success else 409


@main_bp.route("/scripts/status")
def scripts_status():
    return jsonify(get_script_status())


@main_bp.route("/scripts/saved")
def scripts_saved():
    return jsonify({"scripts": list_scripts()})


@main_bp.route("/scripts/saved/<path:name>")
def scripts_saved_entry(name):
    entry = get_script(name)
    if not entry:
        return jsonify({"success": False, "message": "Script not found."}), 404
    return jsonify({"success": True, "script": entry})


@main_bp.route("/scripts/save", methods=["POST"])
def scripts_save():
    data = request.get_json(silent=True) or {}
    success, message = save_script(
        data.get("name", ""), data.get("blocks"), bool(data.get("overwrite"))
    )
    status = 200 if success else (409 if message == "NAME_EXISTS" else 400)
    return jsonify({"success": success, "message": message}), status


@main_bp.route("/scripts/saved/<path:name>", methods=["DELETE"])
def scripts_delete(name):
    success, message = delete_script(name)
    return jsonify({"success": success, "message": message}), 200 if success else 404


def _connection_output_text(output):
    lines = []
    for item in output:
        if not isinstance(item, dict):
            lines.append(str(item))
            continue

        direction = item.get("direction", "")
        if direction == "TX":
            value = item.get("hex", "") if item.get("sent_as_hex") else item.get("ascii", "")
        else:
            value = item.get("ascii", "")
        lines.append(f"[{item.get('time', '')}] {direction}\n{value}")

    return "\n\n".join(lines).replace("\r\n", "\n").replace("\r", "\n")


@main_bp.route("/connection-test/export.txt", methods=["GET", "POST"])
def connection_test_export():
    output = get_connection_status().get("output", [])
    if not output:
        message = "No connection session is available."
        if request.method == "POST":
            return jsonify({"success": False, "message": message}), 409
        return Response(message + "\r\n", status=409, content_type="text/plain; charset=utf-8")

    content = _connection_output_text(output).replace("\n", "\r\n") + "\r\n"
    filename = f"connection-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"

    if request.method == "POST":
        try:
            destination = _save_download(filename, content)
        except OSError as exc:
            return jsonify({"success": False, "message": f"Could not save TXT: {exc}"}), 500
        return jsonify({
            "success": True,
            "filename": filename,
            "path": str(destination),
            "count": len(output),
        })

    return Response(
        content,
        content_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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


@main_bp.route("/multicast")
def multicast_page():
    try:
        nics = [nic for nic in get_nics() if nic.get("link_status") == "Up" and nic.get("ip")]
    except Exception as exc:
        nics = []
        flash(f"Could not load network interfaces: {exc}", "error")
    return render_template("multicast.html", nics=nics)


@main_bp.route("/multicast/start", methods=["POST"])
def multicast_start():
    data = request.get_json(silent=True) or {}
    success, message = start_multicast_test(data.get("interface", ""))
    return jsonify({"success": success, "message": message, **get_multicast_status()})


@main_bp.route("/multicast/stop", methods=["POST"])
def multicast_stop():
    success, message = stop_multicast_test()
    return jsonify({"success": success, "message": message, **get_multicast_status()})


@main_bp.route("/multicast/status")
def multicast_status():
    return jsonify(get_multicast_status())


def _multicast_report_content(status):
    counts = status.get("igmp_counts", {})
    lines = [
        "AVNetworkingTools - IGMP / Multicast Health Report",
        "=" * 52,
        f"Interface: {status.get('interface') or '-'}",
        f"IP: {status.get('ip') or '-'}",
        f"Capture duration: {status.get('elapsed_seconds', 0)} seconds",
        f"Captured multicast packets: {status.get('packets', 0)}",
        f"Rate at stop: {status.get('total_mbps', 0):.3f} Mbps",
        "",
        "IGMP",
        "----",
        f"Querier detected: {'YES' if status.get('querier_detected') else 'NO'}",
        f"Versions observed: {', '.join(status.get('igmp_versions', [])) or 'None'}",
        f"Queries / Reports / Leaves: {counts.get('query', 0)} / {counts.get('report', 0)} / {counts.get('leave', 0)}",
    ]
    queriers = status.get("queriers", [])
    if queriers:
        lines.extend(["", "Querier sources", "---------------"])
        for item in queriers:
            interval = item.get("query_interval_seconds")
            interval_text = f"{interval} sec" if interval is not None else "not yet measured"
            lines.append(f"{item.get('ip', '-')} | last query {item.get('last_query_seconds', 0)} sec before stop | interval {interval_text}")

    joined = status.get("joined_groups", [])
    lines.extend(["", "Joined groups", "-------------"])
    lines.extend(joined or ["Unavailable or none"])

    lines.extend(["", "Multicast traffic", "-----------------"])
    groups = status.get("groups", [])
    if groups:
        lines.append("Group | Service | Packets/s at stop | Mbps at stop | Total packets | Joined | Assessment")
        for item in groups:
            membership = "Unknown" if not item.get("membership_known") else ("Yes" if item.get("joined") else "No")
            assessment = "Flooding suspected" if item.get("suspected_flood") else "Normal"
            lines.append(
                f"{item.get('address', '-')} | {item.get('service', 'Unknown')} | "
                f"{item.get('packets_per_second', 0):.1f} | {item.get('mbps', 0):.3f} | "
                f"{item.get('packets', 0)} | {membership} | {assessment}"
            )
    else:
        lines.append("No multicast traffic observed.")

    lines.extend(["", "Warnings", "--------"])
    warnings = status.get("warnings", [])
    lines.extend((f"[{item.get('severity', 'warning').upper()}] {item.get('message', '')}" for item in warnings))
    if not warnings:
        lines.append("No health warnings detected.")
    return "\r\n".join(lines) + "\r\n"


@main_bp.route("/multicast/export.txt", methods=["POST"])
def multicast_export():
    status = get_multicast_status()
    if status.get("running"):
        return jsonify({"success": False, "message": "Stop the multicast test before downloading a report."}), 409
    if not status.get("interface"):
        return jsonify({"success": False, "message": "No multicast test report is available."}), 409

    filename = f"multicast-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    try:
        destination = _save_download(filename, _multicast_report_content(status))
    except OSError as exc:
        return jsonify({"success": False, "message": f"Could not save multicast report: {exc}"}), 500
    return jsonify({
        "success": True,
        "filename": filename,
        "path": str(destination),
        "groups": len(status.get("groups", [])),
    })

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
