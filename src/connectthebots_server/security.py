"""Small ASGI boundary: bounded bodies, same-origin writes and safe failures.

This is not session authentication or CSRF protection. Those controls are
implemented with the account routes, not implied by this foundation.
"""

import logging
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from .config import Settings


API_BODY_LIMIT = 32 * 1024
FORM_BODY_LIMIT = 64 * 1024
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Chromium uses the document's referrer policy when producing Origin for a
    # native form. no-referrer turns even same-origin forms into Origin:null.
    # same-origin keeps our CSRF origin check usable without external leakage.
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
logger = logging.getLogger("connectthebots_server")


class SecurityBoundary:
    """Wrap outside Starlette's error middleware, including its error responses.

    The body is fully bounded before route execution, so a route cannot commit
    an action and only afterwards discover that a chunked request is too large.
    No exception content, URL, header, request body or client address is logged.
    """

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.state = app.state
        self.router = app.router

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = dict(SECURITY_HEADERS)
                if scope["path"] == "/auth/google/callback":
                    # Do not carry the one-use code/state URL even to another
                    # same-origin page. The destination sets its own policy.
                    headers["Referrer-Policy"] = "no-referrer"
                if self.settings.app_env == "production":
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                protected = {name.lower().encode("ascii") for name in headers}
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", []) if key.lower() not in protected
                ] + [(key.lower().encode("ascii"), value.encode("ascii")) for key, value in headers.items()]
            await send(message)

        async def refuse(code: int, detail: str):
            await JSONResponse({"error": detail}, status_code=code)(scope, receive, secured_send)

        raw_headers = scope.get("headers", [])
        hosts = [value.decode("latin-1") for key, value in raw_headers if key.lower() == b"host"]
        origins = [value.decode("latin-1") for key, value in raw_headers if key.lower() == b"origin"]
        lengths = [value.decode("latin-1") for key, value in raw_headers if key.lower() == b"content-length"]
        encodings = [value.decode("latin-1") for key, value in raw_headers if key.lower() == b"content-encoding"]
        expected = urlsplit(self.settings.public_base_url)
        accepted_hosts = {expected.netloc.lower()}
        if expected.port is None:
            accepted_hosts.add(expected.netloc.lower() + (":443" if expected.scheme == "https" else ":80"))
        # The operator's local liveness probe is harmless and needs no public DNS.
        if scope["path"] == "/healthz":
            accepted_hosts.update({"localhost", "localhost:8100", "127.0.0.1", "127.0.0.1:8100", "[::1]", "[::1]:8100"})
            if expected.hostname and "." in expected.hostname and not expected.hostname.startswith("www."):
                accepted_hosts.add("www." + expected.netloc.lower())
        if len(hosts) != 1 or hosts[0].lower() not in accepted_hosts:
            await refuse(400, "Invalid host")
            return
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and (
            len(origins) > 1 or (origins and origins[0] != self.settings.public_base_url)
        ):
            await refuse(403, "Cross-site request refused")
            return
        if encodings and (len(encodings) != 1 or encodings[0].lower() != "identity"):
            await refuse(415, "Encoded request bodies are not supported")
            return
        limit = API_BODY_LIMIT if scope["path"].startswith("/api/") else FORM_BODY_LIMIT
        if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit())):
            await refuse(400, "Invalid content length")
            return
        # Do not feed an unbounded attacker-controlled decimal to int(). Python
        # rejects very long integers, and that refusal is not an HTTP response.
        if lengths and (len(lengths[0]) > 10 or int(lengths[0]) > limit):
            await refuse(413, "Request body is too large")
            return

        try:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > limit:
                    await refuse(413, "Request body is too large")
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, secured_send)
        except Exception:
            logger.error("Request failed; no request data recorded")
            if not started:
                await refuse(500, "Service temporarily unavailable")
            # Starlette re-raises after its generic 500 response. Consume that
            # exception here so Uvicorn cannot log sensitive exception details.
