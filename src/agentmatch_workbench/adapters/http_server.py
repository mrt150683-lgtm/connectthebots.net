"""Loopback-only HTTP adapter for the workbench. Standard library only.

What this adapter enforces, and why:

- **Loopback bind.** ``serve`` refuses any address other than 127.0.0.1 or
  ::1, so the prototype is not reachable from the network by default.
- **Host allowlist.** Requests must name ``127.0.0.1:<port>`` or
  ``localhost:<port>``. This blocks DNS-rebinding pages from talking to the
  server under another host name.
- **Origin check.** API requests that carry an ``Origin`` must come from the
  same loopback origin; state-changing requests must carry one. Cross-site
  ``Sec-Fetch-Site`` values are refused.
- **Per-launch token.** Every ``/api/`` request needs ``X-Workbench-Token``.
  The token is random per process and is only placed in the served page,
  which other origins cannot read. It is a CSRF and drive-by guard, **not
  authentication**: anyone who can read the page can act as any fictional
  owner, by design of the demo perspective switch.
- **Strict bodies.** POST bodies must be ``application/json``, at most 64 KiB,
  free of duplicate keys and non-finite numbers. The service then rejects
  unknown fields.
- **Allowlisted responses.** Handlers return what ``WorkbenchService`` returns,
  which is built only from ``application.views``. Responses are ``no-store``
  with a restrictive Content-Security-Policy.

What it does not do: TLS, user accounts, rate limiting, persistence or
protection against other software running as the same local user.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Callable

from agentmatch.numeric import strict_json_loads
from ..application.service import WorkbenchError, WorkbenchService
from .seed import build_service

MAX_BODY = 64 * 1024
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
}
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                ".html": "text/html; charset=utf-8"}
TOKEN_PLACEHOLDER = "__WORKBENCH_TOKEN__"
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_\-/]+\.(js|css|svg)$")

Handler = Callable[[WorkbenchService, str, dict[str, Any]], dict[str, Any]]


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"


def _json(status: int, value: Any) -> Response:
    return Response(status, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _error(status: int, code: str, message: str) -> Response:
    return _json(status, {"error": {"code": code, "message": message, "fields": []}})


OWNER_POSTS: dict[str, Handler] = {
    "brief": lambda s, o, b: s.save_brief(o, b),
    "publication/preview": lambda s, o, b: s.preview_publication(o, b),
    "publication/approve": lambda s, o, b: s.approve_publication(o, b),
    "publication/withdraw": lambda s, o, b: s.withdraw_notice(o, b),
    "search": lambda s, o, b: s.search(o, b),
    "explain": lambda s, o, b: s.explain(o, b),
    "block-notice": lambda s, o, b: s.block_notice(o, b),
    "introductions": lambda s, o, b: s.propose_introduction(o, b),
    "information-requests": lambda s, o, b: s.request_information(o, b),
}
INTRO_ACTIONS = {
    "accept": WorkbenchService.accept, "decline": WorkbenchService.decline,
    "withdraw-acceptance": WorkbenchService.withdraw_acceptance, "change": WorkbenchService.change,
    "block": WorkbenchService.block_introduction, "step-propose": WorkbenchService.propose_step,
    "step-agree": WorkbenchService.agree_step, "step-report": WorkbenchService.report_step,
    "close": WorkbenchService.close_introduction,
}
_OWNER_ROUTE = re.compile(r"^/api/owners/([a-z0-9_-]{1,40})/(.+)$")
_INTRO_ROUTE = re.compile(r"^introductions/([a-z0-9_-]{1,40})/([a-z-]{1,24})$")
_INFORMATION_ROUTE = re.compile(r"^information-requests/([a-z0-9_-]{1,40})/decline$")


class WorkbenchApp:
    """Transport-independent request handling, so tests can call it directly."""

    def __init__(self, *, port: int, ui_dir: Path | None, token: str | None = None,
                 factory: Callable[[], WorkbenchService] = build_service):
        self.port, self.ui_dir, self.factory = port, ui_dir, factory
        self.token = token or secrets.token_urlsafe(32)
        self.service = factory()
        self.lock = threading.Lock()
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}

    # ----- request gates -------------------------------------------------

    def _gate(self, method: str, path: str, headers: dict[str, str]) -> Response | None:
        if headers.get("host", "").lower() not in self.allowed_hosts:
            return _error(421, "HOST_NOT_ALLOWED", "Use the loopback address printed when the workbench started.")
        if not path.startswith("/api/"):
            return None if method == "GET" else _error(405, "METHOD_NOT_ALLOWED", "Static files are read-only.")
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return _error(403, "ORIGIN_NOT_ALLOWED", "Requests must come from the workbench page itself.")
        if headers.get("sec-fetch-site") not in (None, "same-origin"):
            return _error(403, "ORIGIN_NOT_ALLOWED", "Requests must come from the workbench page itself.")
        if method == "POST" and origin is None:
            return _error(403, "ORIGIN_REQUIRED", "State-changing requests must include their origin.")
        if not hmac.compare_digest(headers.get("x-workbench-token", ""), self.token):
            return _error(403, "TOKEN_REQUIRED", "Reload the workbench page; this request was not issued by it.")
        if method == "POST" and not headers.get("content-type", "").lower().startswith("application/json"):
            return _error(415, "JSON_REQUIRED", "Send JSON.")
        return None

    # ----- dispatch --------------------------------------------------------

    def handle(self, method: str, path: str, headers: dict[str, str], body: bytes) -> Response:
        headers = {key.lower(): value for key, value in headers.items()}
        path = path.split("?", 1)[0]
        refused = self._gate(method, path, headers)
        if refused is not None:
            return refused
        if not path.startswith("/api/"):
            return self._static(path)
        if method == "POST":
            if len(body) > MAX_BODY:
                return _error(413, "BODY_TOO_LARGE", "The request is too large.")
            try:
                payload = strict_json_loads(body or b"{}", max_bytes=MAX_BODY)
            except ValueError:
                return _error(400, "INVALID_JSON", "The request body is not valid JSON.")
        elif method == "GET":
            payload = None
        else:
            return _error(405, "METHOD_NOT_ALLOWED", "Unsupported method.")
        with self.lock:
            try:
                return _json(200, self._route(method, path, payload))
            except WorkbenchError as exc:
                return _json(exc.status, exc.to_dict())
            except LookupError:
                return _error(404, "NOT_FOUND", "No such endpoint.")

    def _route(self, method: str, path: str, payload: Any) -> dict[str, Any]:
        service = self.service
        if method == "GET" and path == "/api/session":
            return service.session()
        if method == "POST" and path == "/api/simulation/advance":
            if not isinstance(payload, dict) or set(payload) != {"days"} or type(payload["days"]) is not int or not 1 <= payload["days"] <= 30:
                raise WorkbenchError("INVALID_REQUEST")
            service.clock.advance(timedelta(days=payload["days"]))  # type: ignore[attr-defined]
            return service.session()
        if method == "POST" and path == "/api/simulation/reset":
            if payload != {}:
                raise WorkbenchError("INVALID_REQUEST")
            self.service = self.factory()
            return self.service.session()
        match = _OWNER_ROUTE.match(path)
        if not match:
            raise LookupError(path)
        owner_id, rest = match.groups()
        if method == "GET" and rest == "workspace":
            return service.workspace(owner_id)
        if method != "POST":
            raise LookupError(path)
        if rest in OWNER_POSTS:
            return OWNER_POSTS[rest](service, owner_id, payload)
        intro = _INTRO_ROUTE.match(rest)
        if intro and intro.group(2) in INTRO_ACTIONS:
            return INTRO_ACTIONS[intro.group(2)](service, owner_id, intro.group(1), payload)
        information = _INFORMATION_ROUTE.match(rest)
        if information:
            return service.decline_information(owner_id, information.group(1), payload)
        raise LookupError(path)

    def _static(self, path: str) -> Response:
        if self.ui_dir is None:
            return _error(404, "NO_INTERFACE", "No interface directory configured.")
        if path in ("/", "/index.html"):
            page = self.ui_dir / "static" / "index.html"
            if not (self.ui_dir / "dist" / "main.js").is_file():
                return Response(503, ("<!doctype html><title>Build needed</title><p>The interface has not been built. "
                                      "Run <code>npm ci &amp;&amp; npm run build</code> in <code>workbench/ui</code>, then reload.</p>").encode(),
                                STATIC_TYPES[".html"])
            html = page.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, self.token)
            return Response(200, html.encode("utf-8"), STATIC_TYPES[".html"])
        relative = path.lstrip("/")
        if not _SAFE_PATH.fullmatch(relative) or ".." in relative.split("/"):
            return _error(404, "NOT_FOUND", "Not found.")
        if relative.startswith("app/"):
            target = self.ui_dir / "dist" / relative[4:]
        else:
            target = self.ui_dir / "static" / relative
        root = (self.ui_dir / ("dist" if relative.startswith("app/") else "static")).resolve()
        target = target.resolve()
        if root not in target.parents or not target.is_file():
            return _error(404, "NOT_FOUND", "Not found.")
        return Response(200, target.read_bytes(), STATIC_TYPES[target.suffix])


def _request_handler(app: WorkbenchApp) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "AgentMatchWorkbench"
        sys_version = ""

        def _respond(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        def _body(self) -> bytes | None:
            length = self.headers.get("Content-Length", "0")
            if not length.isdigit() or int(length) > MAX_BODY:
                return None
            return self.rfile.read(int(length))

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self._respond(app.handle("GET", self.path, dict(self.headers.items()), b""))

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            if body is None:
                self._respond(_error(413, "BODY_TOO_LARGE", "The request is too large."))
                return
            self._respond(app.handle("POST", self.path, dict(self.headers.items()), body))

        def log_message(self, format: str, *args: Any) -> None:
            # Do not log request paths with owner ids or bodies; keep only method and status.
            if len(args) >= 2:
                print(f"{self.command} -> {args[1]}")

    return RequestHandler


def serve(*, host: str = "127.0.0.1", port: int = 8765, ui_dir: Path | None = None) -> None:
    if host not in LOOPBACK_HOSTS or not ipaddress.ip_address(host).is_loopback:
        raise SystemExit("REFUSED: the workbench only binds to 127.0.0.1 or ::1")
    app = WorkbenchApp(port=port, ui_dir=ui_dir)
    server_class = ThreadingHTTPServer
    if ":" in host:
        import socket

        class V6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        server_class = V6Server
    server = server_class((host, port), _request_handler(app))
    shown = f"[{host}]" if ":" in host else host
    print("AgentMatch local collaboration workbench (fictional prototype; not R1/R2; no authentication)")
    print(f"Open http://{shown}:{port}/  (Ctrl+C to stop; all state is in memory and is discarded on exit)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the fictional local collaboration workbench on loopback.")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default) or ::1; other addresses are refused")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ui-dir", type=Path, default=None, help="workbench/ui directory (built with npm run build)")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, ui_dir=args.ui_dir)
    return 0
