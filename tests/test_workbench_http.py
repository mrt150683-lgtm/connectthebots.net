"""Tests for the workbench's loopback HTTP adapter.

They call ``WorkbenchApp.handle`` directly, so no socket is opened and the
suite still runs under ``tools/isolated_exec.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentmatch_workbench.adapters.http_server import MAX_BODY, SECURITY_HEADERS, WorkbenchApp, serve

PORT = 8765
TOKEN = "test-token-not-secret"
ORIGIN = f"http://127.0.0.1:{PORT}"


def make_ui(root: Path, *, built: bool = True) -> Path:
    (root / "static").mkdir(parents=True)
    (root / "static" / "index.html").write_text(
        '<!doctype html><meta name="workbench-token" content="__WORKBENCH_TOKEN__"><div id="app"></div>', encoding="utf-8")
    (root / "static" / "styles.css").write_text("body{}", encoding="utf-8")
    (root / "secret.txt").write_text("outside the served roots", encoding="utf-8")
    if built:
        (root / "dist" / "components").mkdir(parents=True)
        (root / "dist" / "main.js").write_text("export {};", encoding="utf-8")
        (root / "dist" / "components" / "briefEditor.js").write_text("export {};", encoding="utf-8")
    return root


class HttpAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = WorkbenchApp(port=PORT, ui_dir=make_ui(Path(self.tmp.name)), token=TOKEN)

    def headers(self, **overrides: str | None) -> dict[str, str]:
        base: dict[str, str | None] = {"Host": f"127.0.0.1:{PORT}", "Origin": ORIGIN, "X-Workbench-Token": TOKEN,
                                       "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
        base.update(overrides)
        return {key: value for key, value in base.items() if value is not None}

    def post(self, path: str, body: object | bytes, **overrides: str | None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        return self.app.handle("POST", path, self.headers(**overrides), raw)

    def code(self, response) -> str:
        return json.loads(response.body)["error"]["code"]

    def test_T_WB_HTTP_001_session_requires_token_and_loopback_host(self) -> None:
        ok = self.app.handle("GET", "/api/session", self.headers(Origin=None), b"")
        self.assertEqual(ok.status, 200)
        no_token = self.app.handle("GET", "/api/session", self.headers(**{"X-Workbench-Token": None}), b"")
        self.assertEqual((no_token.status, self.code(no_token)), (403, "TOKEN_REQUIRED"))
        wrong = self.app.handle("GET", "/api/session", self.headers(**{"X-Workbench-Token": "guess"}), b"")
        self.assertEqual(self.code(wrong), "TOKEN_REQUIRED")
        rebinding = self.app.handle("GET", "/api/session", self.headers(Host=f"attacker.example:{PORT}"), b"")
        self.assertEqual((rebinding.status, self.code(rebinding)), (421, "HOST_NOT_ALLOWED"))
        other_port = self.app.handle("GET", "/", self.headers(Host="127.0.0.1:9999"), b"")
        self.assertEqual(other_port.status, 421)

    def test_T_WB_HTTP_002_state_changes_need_same_origin(self) -> None:
        body = {"days": 1}
        missing = self.post("/api/simulation/advance", body, Origin=None)
        self.assertEqual((missing.status, self.code(missing)), (403, "ORIGIN_REQUIRED"))
        foreign = self.post("/api/simulation/advance", body, Origin="http://attacker.example")
        self.assertEqual(self.code(foreign), "ORIGIN_NOT_ALLOWED")
        cross_site = self.post("/api/simulation/advance", body, **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(self.code(cross_site), "ORIGIN_NOT_ALLOWED")
        localhost = self.post("/api/simulation/advance", body, Origin=f"http://localhost:{PORT}", Host=f"localhost:{PORT}")
        self.assertEqual(localhost.status, 200)

    def test_T_WB_HTTP_003_bodies_are_strict_json(self) -> None:
        wrong_type = self.post("/api/simulation/advance", {"days": 1}, **{"Content-Type": "text/plain"})
        self.assertEqual((wrong_type.status, self.code(wrong_type)), (415, "JSON_REQUIRED"))
        invalid = self.post("/api/simulation/advance", b"{days: 1}")
        self.assertEqual((invalid.status, self.code(invalid)), (400, "INVALID_JSON"))
        duplicate = self.post("/api/simulation/advance", b'{"days": 1, "days": 2}')
        self.assertEqual(self.code(duplicate), "INVALID_JSON")
        too_large = self.post("/api/simulation/advance", b" " * (MAX_BODY + 1))
        self.assertEqual((too_large.status, self.code(too_large)), (413, "BODY_TOO_LARGE"))
        extra = self.post("/api/simulation/advance", {"days": 1, "owner": "marlow"})
        self.assertEqual((extra.status, self.code(extra)), (400, "INVALID_REQUEST"))
        out_of_range = self.post("/api/simulation/advance", {"days": 31})
        self.assertEqual(self.code(out_of_range), "INVALID_REQUEST")

    def test_T_WB_HTTP_004_unknown_fields_and_owners_are_refused(self) -> None:
        unknown_field = self.post("/api/owners/juniper/search", {"text": "", "limit": 5, "everyone": True})
        self.assertGreaterEqual(unknown_field.status, 400)
        self.assertLess(unknown_field.status, 500)
        unknown_owner = self.app.handle("GET", "/api/owners/nobody/workspace", self.headers(), b"")
        self.assertEqual(unknown_owner.status, 404)
        unknown_route = self.post("/api/owners/juniper/everything", {})
        self.assertEqual((unknown_route.status, self.code(unknown_route)), (404, "NOT_FOUND"))
        put = self.app.handle("PUT", "/api/session", self.headers(), b"{}")
        self.assertEqual(put.status, 405)

    def test_T_WB_HTTP_005_page_carries_token_and_static_paths_are_confined(self) -> None:
        page = self.app.handle("GET", "/", self.headers(Origin=None, **{"X-Workbench-Token": None}), b"")
        self.assertEqual(page.status, 200)
        self.assertIn(TOKEN.encode(), page.body)
        self.assertNotIn(b"__WORKBENCH_TOKEN__", page.body)
        mixed_case = self.app.handle("GET", "/app/components/briefEditor.js", self.headers(), b"")
        self.assertEqual(mixed_case.status, 200)
        for path in ("/../secret.txt", "/app/../static/styles.css", "/%2e%2e/secret.txt", "/secret.txt",
                     "/static/index.html", "/app/main.js.map", "/app//etc/passwd.js"):
            with self.subTest(path=path):
                self.assertEqual(self.app.handle("GET", path, self.headers(), b"").status, 404)
        write = self.app.handle("POST", "/styles.css", self.headers(), b"{}")
        self.assertEqual(write.status, 405)

    def test_T_WB_HTTP_006_unbuilt_interface_explains_the_build_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = WorkbenchApp(port=PORT, ui_dir=make_ui(Path(tmp), built=False), token=TOKEN)
            page = app.handle("GET", "/", self.headers(), b"")
        self.assertEqual(page.status, 503)
        self.assertIn(b"npm run build", page.body)
        self.assertNotIn(TOKEN.encode(), page.body)

    def test_T_WB_HTTP_007_reset_restores_the_seed(self) -> None:
        self.post("/api/simulation/advance", {"days": 3})
        advanced = json.loads(self.app.handle("GET", "/api/session", self.headers(), b"").body)
        reset = self.post("/api/simulation/reset", {})
        self.assertEqual(reset.status, 200)
        self.assertNotEqual(json.loads(reset.body)["now"], advanced["now"])
        refused = self.post("/api/simulation/reset", {"keep": True})
        self.assertEqual(self.code(refused), "INVALID_REQUEST")

    def test_T_WB_HTTP_008_policy_headers_and_loopback_bind(self) -> None:
        self.assertEqual(SECURITY_HEADERS["Cache-Control"], "no-store")
        self.assertEqual(SECURITY_HEADERS["X-Content-Type-Options"], "nosniff")
        csp = SECURITY_HEADERS["Content-Security-Policy"]
        for directive in ("default-src 'none'", "script-src 'self'", "frame-ancestors 'none'", "form-action 'none'"):
            self.assertIn(directive, csp)
        self.assertNotIn("unsafe-inline", csp)
        for host in ("0.0.0.0", "192.168.1.20", "::", "localhost"):
            with self.subTest(host=host), self.assertRaises(SystemExit) as raised:
                serve(host=host, port=PORT, ui_dir=None)
            self.assertIn("REFUSED", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
