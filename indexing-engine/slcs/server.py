"""Local HTTP API + static UI for the desktop shell.

Security model (see docs/planning section 40): binds to loopback only; every /api request needs a per-process secret
token (X-SLCS-Token); Host and Origin must be loopback (blocks DNS-rebinding and cross-site requests); state-changing
calls require JSON content-type; the client never sends file paths for navigation - only chunk ids. The token protects
against *websites* talking to the local server, not against hostile processes already running as the same user.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_from_directory

from .errors import (CorruptIndexError, EditorUnavailable, EmbeddingError, IncompatibleIndexError, ModelMismatchError,
                     PathSecurityError, QueryError, RepositoryError, SlcsError)
from .service import AppService

LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


def ui_dir() -> Path:
    env = os.environ.get("SLCS_UI_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "desktop" / "ui"


def _status_for(exc: SlcsError) -> int:
    if isinstance(exc, QueryError):
        return 400
    if isinstance(exc, RepositoryError):
        return {"unknown_repository": 404, "already_registered": 409, "already_running": 409}.get(exc.code, 400)
    if isinstance(exc, PathSecurityError):
        if exc.code in ("unknown_result", "file_missing"):
            return 404
        return 403 if exc.code in ("path_not_allowed", "setting_not_allowed", "remote_embedding_blocked") else 400
    if isinstance(exc, ModelMismatchError):
        return 409
    if isinstance(exc, EmbeddingError):
        return 503
    if isinstance(exc, EditorUnavailable):
        return 422
    if isinstance(exc, (CorruptIndexError, IncompatibleIndexError)):
        return 500
    return 500


def create_app(service: AppService, token: str | None = None) -> Flask:
    token = token or secrets.token_urlsafe(32)
    app = Flask(__name__, static_folder=None)
    app.config["SLCS_TOKEN"] = token
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

    @app.before_request
    def guard():
        host = (request.host or "").rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
        if host not in LOOPBACK:
            return Response("Forbidden host", status=403)
        origin = request.headers.get("Origin")
        if origin:
            oh = urlparse(origin).hostname or ""
            if oh not in {"127.0.0.1", "localhost", "::1"}:
                return Response("Forbidden origin", status=403)
        if request.path.startswith("/api/"):
            given = request.headers.get("X-SLCS-Token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                return jsonify(error={"code": "unauthorized", "message": "Missing or invalid token", "recoverable": False}), 401
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.content_length:
                if not (request.mimetype == "application/json"):
                    return jsonify(error={"code": "bad_content_type", "message": "JSON required", "recoverable": True}), 415

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self'; script-src 'self'; "
                                                   "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.errorhandler(SlcsError)
    def handle_slcs(exc: SlcsError):
        return jsonify(error={"code": exc.code, "message": str(exc), "recoverable": exc.recoverable}), _status_for(exc)

    @app.errorhandler(404)
    def nf(_):
        return jsonify(error={"code": "not_found", "message": "Not found", "recoverable": True}), 404

    @app.errorhandler(Exception)
    def internal(exc):
        app.logger.exception("unhandled error")
        return jsonify(error={"code": "internal_error", "message": "Unexpected error (details in the local log)",
                              "recoverable": False}), 500

    def body() -> dict:
        data = request.get_json(silent=True)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise QueryError("JSON object expected", code="invalid_body")
        return data

    # ------------------------------------------------------------------ static UI
    @app.get("/")
    def index():
        html = (ui_dir() / "index.html").read_text(encoding="utf-8").replace("__SLCS_TOKEN__", token)
        return Response(html, mimetype="text/html")

    @app.get("/assets/<path:name>")
    def assets(name):
        return send_from_directory(ui_dir(), name)

    # ------------------------------------------------------------------ API
    @app.get("/api/health")
    def health():
        return jsonify(status="ok", model=service.model_status(), settings=service.get_settings())

    @app.get("/api/repositories")
    def repos():
        return jsonify(repositories=service.list_repositories())

    @app.post("/api/repositories")
    def add_repo():
        b = body()
        if not isinstance(b.get("path"), str):
            raise QueryError("'path' is required", code="invalid_body")
        r = service.register_repository(b["path"], b.get("name"))
        return jsonify(repository=service.repository_status(r["id"])), 201

    @app.get("/api/repositories/<rid>")
    def repo(rid):
        return jsonify(repository=service.repository_status(rid))

    @app.delete("/api/repositories/<rid>")
    def del_repo(rid):
        service.remove_repository(rid)
        return jsonify(removed=True, note="Index data deleted. Your source files were not touched.")

    @app.post("/api/repositories/<rid>/index")
    def start(rid):
        mode = body().get("mode", "incremental")
        service.start_index(rid, mode)
        return jsonify(started=True), 202

    @app.post("/api/repositories/<rid>/cancel")
    def cancel(rid):
        return jsonify(cancelled=service.cancel_index(rid))

    @app.post("/api/search")
    def search():
        b = body()
        return jsonify(service.search(b.get("query"), b.get("repository_ids"), b.get("languages"),
                                      b.get("limit", service.settings.result_limit), b.get("mode", "auto")))

    @app.post("/api/open")
    def open_():
        b = body()
        if not isinstance(b.get("chunk_id"), str):
            raise QueryError("'chunk_id' is required", code="invalid_body")
        return jsonify(service.open_result(b["chunk_id"], b.get("line")))

    @app.get("/api/model")
    def model():
        return jsonify(service.model_status())

    @app.post("/api/model/reindex-all")
    def reindex_all():
        service.reindex_all_for_current_model()
        return jsonify(reset=True, note="Vectors cleared; re-index each repository. Source files were not touched.")

    @app.get("/api/settings")
    def get_settings():
        return jsonify(service.get_settings())

    @app.put("/api/settings")
    def put_settings():
        return jsonify(service.update_settings(body()))

    @app.get("/api/diagnostics")
    def diagnostics():
        return jsonify(service.diagnostics())

    return app


def serve(service: AppService, host: str = "127.0.0.1", port: int = 47615, token: str | None = None) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise PathSecurityError("The server only binds to loopback addresses", code="path_not_allowed")
    token = token or secrets.token_urlsafe(32)
    app = create_app(service, token)
    print(f"Semantic Local Codebase Search running at http://{host}:{port}/  (local only)")
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


def start_background(service: AppService, port: int = 0, token: str | None = None):
    """Run the server on a loopback port in a thread (tests, screenshots). Returns (base_url, token, stop_fn)."""
    import threading
    from werkzeug.serving import make_server
    token = token or secrets.token_urlsafe(32)
    srv = make_server("127.0.0.1", port, create_app(service, token), threaded=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{srv.server_port}", token, lambda: (srv.shutdown(), th.join(5))
