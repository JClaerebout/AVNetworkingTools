"""Per-process request protection for the loopback desktop application."""
import secrets
from urllib.parse import urlsplit

from flask import jsonify, request


def install_request_protection(app):
    token = secrets.token_urlsafe(32)
    app.config["ACTION_TOKEN"] = token
    app.jinja_env.globals["action_token"] = token

    @app.before_request
    def check_request():
        host = urlsplit(request.host_url).hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return jsonify(success=False, message="Unrecognized local application host."), 403
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify(success=False, message="Request origin is not this application."), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(success=False, message="Cross-site requests are not allowed."), 403
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("X-AV-Token") or request.form.get("_action_token", "")
            if not secrets.compare_digest(supplied, token):
                return jsonify(success=False, message="Request expired or invalid. Reload the application page."), 403

    @app.after_request
    def protect_response(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response
