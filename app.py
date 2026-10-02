import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

from flask import Flask, jsonify
from waitress import create_server

from config import SECRET_KEY
from routes import main_bp
from version import APP_VERSION
from request_security import install_request_protection

import webview
from manufacturer_db import start_manufacturer_database_update
from app_lifecycle import stop_background_tasks


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.jinja_env.globals["app_version"] = APP_VERSION
    app.jinja_env.globals["macos"] = sys.platform == "darwin"
    app.config["INSTANCE_ID"] = secrets.token_urlsafe(24)
    install_request_protection(app)
    app.register_blueprint(main_bp)

    @app.get("/_instance_health")
    def instance_health():
        return jsonify(instance_id=app.config["INSTANCE_ID"])

    return app


app = create_app()


DEFAULT_PORT = 49780


class PortInUseError(RuntimeError):
    """The desktop app cannot own its fixed local port."""


_server_stopping = threading.Event()

def start_flask(server):
    try:
        server.run()
    except OSError:
        if not _server_stopping.is_set():
            raise


def create_desktop_server(port=DEFAULT_PORT):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(socket.SOMAXCONN)
        return create_server(app, sockets=[listener], threads=8)
    except BaseException:
        listener.close()
        raise


def wait_for_flask(instance_id, server_thread, host="127.0.0.1", port=DEFAULT_PORT, timeout=10):
    deadline = time.monotonic() + timeout
    url = f"http://{host}:{port}/_instance_health"
    while time.monotonic() < deadline and server_thread.is_alive():
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                import json
                if json.load(response).get("instance_id") == instance_id:
                    return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    return False


def run_desktop():
    try:
        server = create_desktop_server()
    except OSError as exc:
        raise PortInUseError(f"AVNetworkingTools is already running, or port {DEFAULT_PORT} is in use. Close the other application before starting a new window.") from exc
    _server_stopping.clear()
    flask_thread = threading.Thread(target=start_flask, args=(server,), daemon=True)
    try:
        flask_thread.start()
        if not wait_for_flask(app.config["INSTANCE_ID"], flask_thread):
            raise RuntimeError("AVNetworkingTools did not respond after startup. The desktop window was not opened.")
        start_manufacturer_database_update()
        webview.create_window("AVNetworkingTools", f"http://127.0.0.1:{DEFAULT_PORT}",
                              width=1280, height=800, resizable=True, text_select=True,
                              background_color="#242424")
        webview.start()
    finally:
        stop_background_tasks()
        _server_stopping.set()
        server.task_dispatcher.shutdown()
        server.close()
        if flask_thread.is_alive():
            flask_thread.join(timeout=2)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--packaged-smoke":
        from packaged_smoke import run as run_packaged_smoke
        raise SystemExit(run_packaged_smoke(sys.modules["__main__"], sys.argv[2]))
    try:
        run_desktop()
    except PortInUseError as exc:
        message = str(exc)
        print(message)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "AVNetworkingTools", 0x40)
        raise SystemExit(0) from None
    except Exception as exc:
        message = str(exc)
        print(message)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "AVNetworkingTools startup failed", 0x10)
        raise SystemExit(1) from exc
