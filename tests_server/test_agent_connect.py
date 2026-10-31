"""One-link agent connection: the person copies one message, the agent connects itself."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time

LINK = re.compile(r"https://connect\.example\.test/api/v1/connect/(ctbc_[A-Za-z0-9_-]{32})")


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class AgentConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://connect.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-only-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.auth, self.db = self.app.state.auth, self.app.state.database
        self.tokens = {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon"):
                connection.execute(
                    "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "fictional-" + owner, owner + "@example.test", owner, storage_time(self.now), storage_time(self.now)))
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        self.use("ama")

    def use(self, owner):
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def connect(self):
        response = self.client.post("/app/connect", data={"csrf": self.csrf}, follow_redirects=False)
        self.assertEqual(response.status_code, 200, response.text)
        message = unescape(re.search(r"<textarea[^>]*data-connect-message>(.*?)</textarea>", response.text, re.DOTALL).group(1))
        return message, LINK.search(message).group(1)

    def claim(self, code, **extra):
        return self.client.post("/api/v1/agent/connect", content=json.dumps({"code": code, **extra}),
                                headers={"Content-Type": "application/json"})

    def test_dashboard_offers_one_click_connection_with_full_prompt_fallback(self):
        page = self.client.get("/app").text
        self.assertRegex(page, r'<form method="post" action="/app/connect" data-connect-agent><input type="hidden" name="csrf" value="' + re.escape(self.csrf) + '">')
        self.assertIn("Connect my agent", page)
        self.assertIn('action="/app/keys" data-full-prompt', page)
        # Issuing a link is a POST with CSRF, never a GET.
        self.assertEqual(self.client.post("/app/connect", data={"csrf": "wrong"}).status_code, 403)
        self.assertNotEqual(self.client.get("/app/connect").status_code, 200)

    def test_the_whole_handoff_is_one_short_message_and_the_agent_connects_itself(self):
        message, code = self.connect()
        self.assertLess(len(message), 300)
        self.assertNotRegex(message, r"ctb_[A-Z2-7]{52}")
        # Reading the link page never consumes it and never reveals a key.
        for _ in range(2):
            page = self.client.get("/api/v1/connect/" + code)
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn("text/markdown", page.headers["content-type"])
            self.assertEqual(page.headers["cache-control"], "no-store")
            self.assertIn("noindex", page.headers["x-robots-tag"])
            self.assertIn('"code": "' + code + '"', page.text)
            self.assertIn("POST", page.text)
            self.assertNotRegex(page.text, r"ctb_[A-Z2-7]{52}")
        claimed = self.claim(code)
        self.assertEqual(claimed.status_code, 201, claimed.text)
        body = claimed.json()
        self.assertTrue(body["connected"])
        key = body["agent_key"]
        self.assertRegex(key, r"\Actb_[A-Z2-7]{52}\Z")
        self.assertIn(key, body["guide"])
        self.assertIn("GET /api/v1/agent/interview", body["next_step"]["call"])
        me = self.client.get("/api/v1/agent/me", headers={"Authorization": "Bearer " + key})
        self.assertEqual(me.status_code, 200, me.text)
        self.assertTrue(me.json()["connected"])
        # The dashboard now shows the agent as connected.
        self.assertIn('data-agent-connected="yes"', self.client.get("/app").text)
        with self.db.connect() as connection:
            row = connection.execute("SELECT name,agent_api,user_id FROM report_keys").fetchone()
            self.assertEqual(tuple(row), ("Connected agent", 1, "ama"))
            stored = json.dumps([dict(item) for item in connection.execute("SELECT * FROM agent_connect_links")])
        self.assertNotIn(code, stored)
        self.assertNotIn(key, stored)

    def test_a_link_works_once(self):
        _, code = self.connect()
        self.assertEqual(self.claim(code).status_code, 201)
        again = self.claim(code)
        self.assertEqual(again.status_code, 410)
        self.assertIn("already used or has expired", again.json()["error"])
        self.assertEqual(self.client.get("/api/v1/connect/" + code).status_code, 410)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_keys").fetchone()[0], 1)

    def test_a_link_expires_after_an_hour(self):
        _, code = self.connect()
        self.now += timedelta(minutes=59)
        self.assertEqual(self.client.get("/api/v1/connect/" + code).status_code, 200)
        self.now += timedelta(minutes=1)
        self.assertEqual(self.client.get("/api/v1/connect/" + code).status_code, 410)
        self.assertEqual(self.claim(code).status_code, 410)

    def test_a_new_link_replaces_an_unclaimed_one(self):
        _, first = self.connect()
        _, second = self.connect()
        self.assertEqual(self.claim(first).status_code, 410)
        self.assertEqual(self.claim(second).status_code, 201)

    def test_links_belong_to_the_person_who_made_them(self):
        _, code = self.connect()
        key = self.claim(code).json()["agent_key"]
        self.use("joon")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_keys WHERE user_id='joon'").fetchone()[0], 0)
        me = self.client.get("/api/v1/agent/me", headers={"Authorization": "Bearer " + key})
        self.assertEqual(me.status_code, 200)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT user_id FROM report_keys").fetchone()[0], "ama")

    def test_the_three_key_limit_is_explained_before_and_at_claim(self):
        _, code = self.connect()
        for name in ("One", "Two"):
            self.assertEqual(self.client.post("/app/keys", data={"csrf": self.csrf, "name": name}).status_code, 200)
        self.assertEqual(self.claim(code).status_code, 201)
        refused = self.client.post("/app/connect", data={"csrf": self.csrf})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("three active agent keys", refused.text)

    def test_suspended_accounts_cannot_be_connected(self):
        _, code = self.connect()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='ama'", (storage_time(self.now),))
        self.assertEqual(self.client.get("/api/v1/connect/" + code).status_code, 410)
        self.assertEqual(self.claim(code).status_code, 410)

    def test_malformed_claims_are_refused_without_effect(self):
        _, code = self.connect()
        for bad in ("", "ctbc_short", "ctb_" + "A" * 52, code + "x", "../" + code):
            with self.subTest(bad=bad):
                self.assertEqual(self.claim(bad).status_code, 410)
        self.assertEqual(self.claim(code, extra="field").status_code, 422)
        wrong_type = self.client.post("/api/v1/agent/connect", content=json.dumps({"code": code}), headers={"Content-Type": "text/plain"})
        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(self.client.get("/api/v1/connect/not-a-code").status_code, 410)
        # None of those consumed the real link.
        self.assertEqual(self.claim(code).status_code, 201)

    def test_closed_service_refuses_connection(self):
        _, code = self.connect()
        self.auth.settings = replace(self.auth.settings, account_signin_enabled=False)
        self.assertFalse(self.auth.enabled)
        self.assertEqual(self.client.get("/api/v1/connect/" + code).status_code, 503)
        self.assertEqual(self.claim(code).status_code, 503)

if __name__ == "__main__":
    unittest.main()
