import secrets
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
    app.config["INSTANCE_ID"] = secrets.token_urlsafe(24)
    install_request_protection(app)
    app.register_blueprint(main_bp)

    @app.get("/_instance_health")
    def instance_health():
        return jsonify(instance_id=app.config["INSTANCE_ID"])

    return app


app = create_app()


DEFAULT_PORT = 49780


def start_flask(server):
    server.run()


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
        server = create_server(app, host="127.0.0.1", port=DEFAULT_PORT, threads=8)
    except OSError as exc:
        raise RuntimeError(f"Cannot start AVNetworkingTools on port {DEFAULT_PORT}. Close the application using that port and try again.") from exc
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
        server.close()
        if flask_thread.is_alive():
            flask_thread.join(timeout=2)


if __name__ == "__main__":
    try:
        run_desktop()
    except Exception as exc:
        message = str(exc)
        print(message)
        if __import__("sys").platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "AVNetworkingTools startup failed", 0x10)
        raise SystemExit(1) from exc
