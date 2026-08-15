from flask import Flask
from waitress import serve

from config import SECRET_KEY
from routes import main_bp
from version import APP_VERSION

import threading
import time
import webview
import socket
from manufacturer_db import start_manufacturer_database_update


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.jinja_env.globals["app_version"] = APP_VERSION
    app.register_blueprint(main_bp)
    return app


app = create_app()


def start_flask():
    serve(
        app,
        host="127.0.0.1",
        port=5050,
        threads=8,
    )


def wait_for_flask(host="127.0.0.1", port=5050, timeout=10):
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)

    return False


if __name__ == "__main__":

    start_manufacturer_database_update()

    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    wait_for_flask()

    webview.create_window(
        "AVNetKit",
        "http://127.0.0.1:5050",
        width=1500,
        height=950,
        resizable=True,
        text_select=True,
        background_color="#242424",
    )

    webview.start()
