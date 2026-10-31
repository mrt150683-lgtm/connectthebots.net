"""Loopback agent transport, with a separately authenticated read-only viewer.

No credentials are embedded in HTML. Native agents may omit Origin, browsers
must be same-origin; a bearer secret is always required for private endpoints.
This is a local development adapter, not an internet-facing server.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

from agentmatch.numeric import strict_json_loads
from agentmatch_workbench.adapters.http_server import Response, SECURITY_HEADERS
from .catalogue import catalogue, openapi
from .runtime import AgentRuntimeError

MAX_BODY = 65536


def response(status, data):
    return Response(status, json.dumps(data, ensure_ascii=True, allow_nan=False).encode())


def error(status, code, message):
    return response(status, {"error": {"code": code, "message": message, "details": {}}})


class AgentApp:
    def __init__(self, runtime, *, port, viewer_dir=None):
        self.runtime, self.port = runtime, port
        self.viewer_dir = Path(viewer_dir) if viewer_dir else None
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def handle(self, method, target, headers, body=b""):
        headers = {k.lower(): v for k, v in headers.items()}
        if headers.get("host", "").lower() not in self.hosts:
            return error(421, "HOST_NOT_ALLOWED", "Use the local server address.")
        if headers.get("origin") is not None:
            # Build from the fixed host allowlist; never trust a supplied Host as
            # an origin grant (DNS rebinding and cross-origin browser requests).
            if headers["origin"] not in {"http://" + h for h in self.hosts}:
                return error(403, "ORIGIN_NOT_ALLOWED", "Only same-origin browser access is supported.")
        if headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            return error(403, "ORIGIN_NOT_ALLOWED", "Cross-site requests are refused.")
        path = urlsplit(target).path
        query = parse_qs(urlsplit(target).query, keep_blank_values=True)
        if method == "GET" and path == "/openapi.json" and not query:
            return response(200, openapi())
        if not path.startswith("/v1/"):
            if method != "GET" or query or self.viewer_dir is None:
                return error(404, "NOT_FOUND", "No such endpoint.")
            name = {"/": "index.html", "/index.html": "index.html", "/viewer.js": "viewer.js", "/style.css": "style.css"}.get(path)
            if name is None:
                return error(404, "NOT_FOUND", "No such file.")
            file = (self.viewer_dir / name).resolve()
            if file.parent != self.viewer_dir.resolve() or not file.is_file():
                return error(404, "NOT_FOUND", "Viewer file unavailable.")
            return Response(200, file.read_bytes(), {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix] + "; charset=utf-8")
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 512:
            return error(401, "AUTH_REQUIRED", "Supply an owner-bound bearer credential.")
        token = auth[7:]
        try:
            if method == "GET":
                if body:
                    return error(400, "INVALID_REQUEST", "GET requests have no body.")
                if path == "/v1/receipts":
                    if set(query) - {"after", "before", "limit", "order"} or any(len(v) != 1 for v in query.values()):
                        return error(400, "INVALID_REQUEST", "Use single cursor, order and limit values.")
                    try:
                        after, limit = int(query.get("after", ["0"])[0]), int(query.get("limit", ["50"])[0])
                        before = int(query["before"][0]) if "before" in query else None
                    except ValueError:
                        return error(400, "INVALID_REQUEST", "Cursor and limit must be integers.")
                    order = query.get("order", ["asc"])[0]
                    if order == "desc" and "after" in query:
                        return error(400, "INVALID_CURSOR", "Descending pages use before; ascending pages use after.")
                    return response(200, self.runtime.receipts(token, after=after, limit=limit, order=order, before=before))
                if query:
                    return error(400, "INVALID_REQUEST", "Query parameters are not accepted here.")
                if path == "/v1/workspace":
                    return response(200, self.runtime.execute(token, "workspace", {}))
                if path == "/v1/capabilities":
                    return response(200, self.runtime.capabilities(token))
                if path == "/v1/operations":
                    capability = self.runtime.capabilities(token)
                    return response(200, {"version": capability["version"], "result": catalogue()})
            elif method == "POST" and path == "/v1/commands" and not query:
                if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return error(415, "JSON_REQUIRED", "Send application/json.")
                if len(body) > MAX_BODY:
                    return error(413, "BODY_TOO_LARGE", "Command limit is 64 KiB.")
                try:
                    data = strict_json_loads(body, max_bytes=MAX_BODY)
                except (ValueError, UnicodeError, RecursionError):
                    return error(400, "INVALID_JSON", "Use finite JSON without duplicate keys.")
                if not isinstance(data, dict) or not {"operation", "arguments"} <= set(data) or set(data) - {"operation", "arguments", "idempotency_key", "expected_version"}:
                    return error(400, "INVALID_REQUEST", "Supply operation, arguments and mutation concurrency fields.")
                return response(200, self.runtime.execute(token, **data))
            else:
                return error(405, "METHOD_NOT_ALLOWED", "Use GET for views and POST /v1/commands for agent commands.")
            return error(404, "NOT_FOUND", "No such endpoint.")
        except AgentRuntimeError as exc:
            return response(exc.status, exc.to_dict())
        except sqlite3.OperationalError as exc:
            # Extended SQLite codes retain the base error in their low byte.
            # Classify contention only; unrelated SQL faults are server defects.
            code = getattr(exc, "sqlite_errorcode", 0) & 0xff
            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            return response(503, {"error": {
                "code": "STORAGE_UNAVAILABLE",
                "message": "The store is busy. Retry the same request and retry key after a short delay.",
                "details": {"retryable": True, "retry_after_seconds": 1}}})
        except (OSError, ValueError):
            return error(503, "STORAGE_UNAVAILABLE", "The encrypted store could not complete this request.")


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentMatchLocal"

        def log_message(self, *_args):
            pass  # Never print URLs, bearer secrets or attacker-controlled text.

        def do_GET(self):
            self._request()

        def do_POST(self):
            self._request()

        def do_OPTIONS(self):
            self._request()

        def _request(self):
            self.connection.settimeout(10)
            headers = {k: v for k, v in self.headers.items()}
            duplicate = any(len(self.headers.get_all(k, [])) > 1 for k in ("Host", "Authorization", "Content-Length", "Content-Type", "Origin"))
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            if duplicate or self.headers.get("Transfer-Encoding") or size < 0:
                result = error(400, "INVALID_REQUEST", "Ambiguous request framing is refused.")
            elif size > MAX_BODY:
                result = error(413, "BODY_TOO_LARGE", "Command limit is 64 KiB.")
            else:
                try:
                    body = self.rfile.read(size) if size else b""
                    result = (app.handle(self.command, self.path, headers, body) if len(body) == size else
                              error(400, "INVALID_REQUEST", "Incomplete request body."))
                except TimeoutError:
                    result = error(408, "REQUEST_TIMEOUT", "Request timed out.")
                except Exception:
                    result = error(500, "INTERNAL_ERROR", "Request failed; no diagnostic payload is exposed.")
            self.send_response(result.status)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Content-Type", result.content_type)
            self.send_header("Content-Length", str(len(result.body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(result.body)
            self.close_connection = True
    return Handler


def serve(runtime, *, port=8766, viewer_dir=None):
    app = AgentApp(runtime, port=port, viewer_dir=viewer_dir)
    with ThreadingHTTPServer(("127.0.0.1", port), handler(app)) as server:
        app.port = server.server_port
        app.hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
        print(json.dumps({"url": f"http://127.0.0.1:{app.port}/", "fictional_only": True}), flush=True)
        server.serve_forever()
