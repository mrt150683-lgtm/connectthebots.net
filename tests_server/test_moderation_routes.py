"""Human reporting authority, exact target forms and private receipts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import urlencode

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.moderation_routes import ModerationTokens
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time, token_hash


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class ModerationWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://moderation.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-moderation-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(self.settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.public_base_url))
        self.auth, self.db = self.app.state.auth, self.app.state.database
        self.moderation = self.app.state.moderation
        self.interactions, self.board = self.app.state.interactions, self.app.state.board
        self.tokens, self.postings = {}, {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon", "sora"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "private-sub-" + owner, owner + "@private.example.test", "PrivateGoogleName" + owner,
                     storage_time(self.now), storage_time(self.now)))
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        for owner in self.tokens:
            draft = self.board.save_draft(owner, self.payload("fictional garden tools"))
            self.postings[owner] = self.board.publish(owner, draft["revision"], draft["payload_hash"])
        self.use("ama")

    @staticmethod
    def payload(subject):
        return {"text": f"I build {subject}. I seek a thoughtful collaborator. I offer a small trial.",
                "tags": ["Research"], "looking_for": "A shared experiment", "offering": "Careful prototyping"}

    def use(self, owner):
        self.owner = owner
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def post(self, token, **fields):
        return self.client.post("/app/report", data={"csrf": self.csrf, "report_token": token,
                                                     "reason": "other", "details": "A concern to review.", **fields},
                                follow_redirects=False)

    def form(self, kind="posting", target=None):
        target = target or self.postings["joon"]["slug"]
        response = self.client.get("/app/report?" + urlencode({"kind": kind, "target": target}))
        self.assertEqual(response.status_code, 200, response.text)
        token = unescape(re.search(r'name="report_token" value="([^"]+)"', response.text).group(1))
        return token, response

    def submitted(self, owner="ama"):
        return self.moderation.export_owner(owner)

    def conversation(self):
        preview = self.interactions.prepare_request("ama", self.postings["joon"]["slug"], "A small experiment?", "Careful research, useful tools")
        intro = self.interactions.request("ama", preview, "fictional-report-intro-nonce")
        return self.interactions.accept("joon", intro["id"], intro["context_hash"])["conversation"]

    def expiry(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash(self.tokens["ama"]),)).fetchone()[0]

    def test_native_form_is_read_only_and_receipt_replay_is_private(self):
        token, page = self.form()
        self.assertEqual(self.submitted(), [])
        self.assertIn('method="post" action="/app/report"', page.text)
        self.assertIn('maxlength="2000"', page.text)
        self.assertEqual(re.findall(r'<option value="([a-z_]+)"', page.text),
                         ["spam", "harassment", "impersonation", "privacy", "unsafe_content", "other"])
        self.assertNotIn("PrivateGoogleNamejoon", page.text)
        self.assertNotIn("joon@private.example.test", page.text)
        first, repeated = self.post(token), self.post(token)
        self.assertEqual((first.status_code, repeated.status_code), (200, 200))
        self.assertIn("Report received.", first.text)
        first_id = re.search(r'data-report-id="([^"]+)"', first.text).group(1)
        self.assertIn('data-report-id="' + first_id + '"', repeated.text)
        self.assertEqual(len(self.submitted()), 1)
        self.assertEqual(self.submitted("joon"), [])
        self.assertNotIn("A concern to review.", first.text)
        self.assertNotIn("joon@private.example.test", first.text)
        self.assertEqual(self.post(token, details="Different words.").status_code, 409)
        self.assertEqual(len(self.submitted()), 1)

    def test_every_posting_and_closed_conversation_has_a_report_link(self):
        board = self.client.get("/board")
        for posting in self.postings.values():
            self.assertIn('/app/report?kind=posting&amp;target=' + posting["slug"], board.text)
        detail = self.client.get("/board/" + self.postings["joon"]["slug"])
        self.assertIn('>Report</a>', detail.text)
        conversation = self.conversation()
        self.interactions.close_conversation("ama", conversation["id"])
        detail = self.client.get("/app/conversations/" + conversation["id"])
        self.assertIn('/app/report?kind=conversation&amp;target=' + conversation["id"], detail.text)

    def test_csrf_origin_and_extra_fields_fail_without_saving(self):
        token, _ = self.form()
        fields = {"report_token": token, "reason": "spam", "details": "Please review."}
        for csrf in (None, "0" * 64, "é" * 64):
            with self.subTest(csrf=csrf):
                response = self.client.post("/app/report", data={**fields, **({"csrf": csrf} if csrf else {})})
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/app/report", data={"csrf": self.csrf, **fields},
                                         headers={"Origin": "https://foreign.example"}).status_code, 403)
        for name in ("owner", "user_id", "subject_user_id", "target", "kind", "context_hash"):
            self.assertEqual(self.post(token, **{name: "sora"}).status_code, 400)
        body = urlencode({"csrf": self.csrf, **fields}) + "&reason=privacy"
        self.assertEqual(self.client.post("/app/report", content=body,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code, 400)
        self.assertEqual(self.client.post("/app/report?owner=sora", data={"csrf": self.csrf, **fields}).status_code, 400)
        self.assertEqual(self.client.post("/app/report", json={"csrf": self.csrf, **fields}).status_code, 400)
        self.assertEqual(self.submitted(), [])

    def test_owner_bound_tamper_expiry_and_different_purpose_tokens_fail(self):
        token, _ = self.form()
        changed = token[:-1] + ("0" if token[-1] != "0" else "1")
        for invalid in (changed, "not-a-token", "é", "x" * 2001):
            self.assertEqual(self.post(invalid).status_code, 400)
        self.use("sora")
        self.assertEqual(self.post(token).status_code, 400)
        self.use("ama")
        from connectthebots_server.interaction_routes import InteractionTokens
        other = InteractionTokens(self.auth).issue("ama", "submit_report", self.moderation.target("ama", "posting", self.postings["joon"]["slug"]))
        self.assertEqual(self.post(other).status_code, 400)
        self.now += timedelta(minutes=10)
        self.assertEqual(self.post(token).status_code, 400)
        self.assertEqual(self.submitted(), [])

    def test_target_context_changes_and_withdrawal_refuse_old_forms(self):
        token, _ = self.form()
        draft = self.board.save_draft("joon", self.payload("a different fictional project"))
        self.board.publish("joon", draft["revision"], draft["payload_hash"])
        self.assertEqual(self.post(token).status_code, 409)
        current, _ = self.form()
        self.board.unpublish("joon")
        self.assertIn(self.post(current).status_code, (404, 410))
        self.assertEqual(self.submitted(), [])

    def test_conversation_idor_is_refused_but_reporting_after_block_does_not_reopen(self):
        conversation = self.conversation()
        path = "/app/report?kind=conversation&target=" + conversation["id"]
        self.use("sora")
        self.assertEqual(self.client.get(path).status_code, 404)
        # Even a correctly signed document never replaces the service's owner check.
        target = self.moderation.target("ama", "conversation", conversation["id"])
        borrowed = ModerationTokens(self.auth).issue("sora", target)
        self.assertEqual(self.post(borrowed).status_code, 404)
        self.use("ama")
        self.interactions.block_conversation("ama", conversation["id"])
        token, page = self.form("conversation", conversation["id"])
        self.assertIn("Reporting does not", page.text)
        self.assertEqual(self.post(token, reason="harassment").status_code, 200)
        self.assertEqual(self.interactions.get_conversation("ama", conversation["id"])["state"], "closed")
        self.assertEqual(len(self.submitted()), 1)
        self.use("joon")
        token, _ = self.form("conversation", conversation["id"])
        self.assertEqual(self.post(token, reason="other").status_code, 200)
        self.assertEqual(len(self.submitted("joon")), 1)

    def test_strict_reason_details_and_native_line_endings(self):
        token, _ = self.form()
        for reason in ("", "SPAM", "unapproved", "<script>"):
            self.assertEqual(self.post(token, reason=reason).status_code, 422)
        for details in ("x" * 2001, "bad\x00text", "bad\rtext", "<script>alert(1)</script>", "99% match"):
            response = self.post(token, details=details)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("<script>alert", response.text)
        self.assertEqual(self.submitted(), [])
        self.assertEqual(self.post(token, details="First line\r\nSecond line").status_code, 200)
        exported = self.submitted()
        self.assertEqual(exported[0]["details"], "First line\nSecond line")
        empty, _ = self.form()
        self.assertEqual(self.post(empty, details="").status_code, 200)

    def test_anonymous_bearer_and_disabled_accounts_cannot_report(self):
        token, _ = self.form()
        self.client.cookies.clear()
        for method, path, fields in (("get", "/app/report?kind=posting&target=" + self.postings["joon"]["slug"], {}),
                                     ("post", "/app/report", {"data": {"csrf": self.csrf, "report_token": token, "reason": "spam"}})):
            response = getattr(self.client, method)(path, headers={"Authorization": "Bearer fictional-report-key"},
                                                     follow_redirects=False, **fields)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/signin")
        self.assertEqual(self.submitted(), [])
        self.auth.settings = replace(self.auth.settings, account_signin_enabled=False)
        response = self.client.get("/app/report?kind=posting&target=" + self.postings["joon"]["slug"])
        self.assertEqual(response.status_code, 503)
        self.assertIn("Reporting is not open yet.", response.text)

    def test_invalid_target_queries_never_refresh_a_session(self):
        self.now += timedelta(days=1)
        before = self.expiry()
        for suffix in ("", "?kind=posting", "?kind=user&target=joon", "?kind=posting&target=missing",
                       "?kind=posting&kind=posting&target=" + self.postings["joon"]["slug"],
                       "?kind=posting&target=" + self.postings["joon"]["slug"] + "&owner=sora"):
            self.assertIn(self.client.get("/app/report" + suffix).status_code, (400, 404, 422))
            self.assertEqual(self.expiry(), before)
        token, _ = self.form()
        self.assertGreater(self.expiry(), before)
        before = self.expiry()
        self.now += timedelta(hours=1)
        self.assertEqual(self.post(token, csrf="0" * 64).status_code, 403)
        self.assertEqual(self.expiry(), before)

    def test_rolling_limit_returns_retry_after_and_replay_does_not_consume_quota(self):
        tokens = []
        for _ in range(10):
            token, _ = self.form()
            tokens.append(token)
            self.assertEqual(self.post(token).status_code, 200)
        self.assertEqual(self.post(tokens[0]).status_code, 200)
        token, _ = self.form()
        response = self.post(token)
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.assertEqual(len(self.submitted()), 10)
        self.now += timedelta(days=1, seconds=1)
        token, _ = self.form()
        self.assertEqual(self.post(token).status_code, 200)

    def test_no_report_history_or_admin_web_route_exists(self):
        token, _ = self.form()
        receipt = self.post(token)
        report_id = re.search(r'data-report-id="([^"]+)"', receipt.text).group(1)
        for path in ("/app/reports/abuse", "/app/report/" + report_id, "/admin", "/admin/reports", "/api/v1/moderation"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.put("/app/report", data={"csrf": self.csrf}).status_code, 405)
