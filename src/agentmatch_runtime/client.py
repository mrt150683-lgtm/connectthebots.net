"""Small agent client: standard-library HTTP, no automatic effect retries.

Give agents only their own bearer credential, never the administrator vault key.
Caller retains the same idempotency key when explicitly recovering a lost reply.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class AgentAPIError(Exception):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        super().__init__(payload.get("error", {}).get("code", "API_ERROR"))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None  # A redirected request must never forward a bearer secret.


class AgentClient:
    def __init__(self, base_url, token, *, timeout=15):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("Only a plain loopback server URL is supported.")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, path, data=None):
        encoded = None if data is None else json.dumps(data, allow_nan=False).encode()
        request = Request(self.base_url + path, data=encoded,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as reply:
                return json.load(reply)
        except HTTPError as exc:
            try:
                payload = json.load(exc)
            except (ValueError, UnicodeError):
                payload = {"error": {"code": "HTTP_ERROR"}}
            raise AgentAPIError(exc.code, payload) from None

    def capabilities(self):
        return self._request("/v1/capabilities")

    def operations(self):
        return self._request("/v1/operations")

    def workspace(self):
        return self._request("/v1/workspace")

    def receipts(self, *, after=0, limit=50, order="asc", before=None):
        if (type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100
                or order not in ("asc", "desc")
                or (before is not None and (type(before) is not int or before < 1))
                or (order == "asc" and before is not None) or (order == "desc" and after != 0)):
            raise ValueError("Use a valid page size and cursor for the selected order.")
        query = f"order={order}&limit={limit}"
        query += f"&after={after}" if order == "asc" else (f"&before={before}" if before is not None else "")
        return self._request("/v1/receipts?" + query)

    def execute(self, operation, arguments, *, expected_version=None, idempotency_key=None):
        data = {"operation": operation, "arguments": arguments}
        if expected_version is not None:
            data["expected_version"] = expected_version
        if idempotency_key is not None:
            data["idempotency_key"] = idempotency_key
        return self._request("/v1/commands", data)
