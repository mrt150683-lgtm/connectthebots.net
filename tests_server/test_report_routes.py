"""Real HTTP boundaries for report ownership, confirmation and dashboard states."""
from datetime import datetime, timedelta, timezone
from html import unescape
from http.cookies import SimpleCookie
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
    from connectthebots_server.report_schema import parse_paste
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time, token_hash


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class ReportWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://reports.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-only-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.auth, self.db, self.reports = self.app.state.auth, self.app.state.database, self.app.state.reports
        self.tokens = {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon"):
                connection.execute(
                    "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "fictional-" + owner, owner + "@example.test", owner, storage_time(self.now), storage_time(self.now)),
                )
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        self.use("ama")

    def use(self, owner):
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=False)

    def field(self, response, name):
        match = re.search(r'name="' + name + r'" value="([^"]*)"', response.text)
        self.assertIsNotNone(match, response.text)
        return unescape(match.group(1))

    def retained_report(self, response):
        match = re.search(r'<textarea\b[^>]*id="report-text"[^>]*>(.*?)</textarea>', response.text, re.DOTALL)
        self.assertIsNotNone(match, response.text)
        return unescape(match.group(1))

    def pasted(self, kind="question"):
        common = "Kind: " + kind + "\nChecked at: 2026-10-30T12:00:00Z\nPlaces checked: Board | https://board.example.test/\nPosts read: 3\n"
        return common + ("Question: <script>alert(1)</script>\nWhy: A detail is missing.\n" if kind == "question" else "")

    def payload(self, kind="nothing"):
        result = {"kind": kind, "checked_at": "2026-10-30T12:00:00Z", "places_checked": [], "posts_read": 0}
        if kind == "question":
            result["question"] = {"text": "Which location?", "why": "It changes the next step."}
        if kind == "connection":
            result["connection"] = {"who": "Fictional Robin", "where": "https://board.example.test/robin",
                "why_now": "They asked for help.", "they_bring": "A project.", "they_could_gain": "A careful review.",
                "i_bring": "Experience.", "i_could_gain": "A useful collaboration.", "evidence": [
                    {"quote_or_link": "A public request", "matches": "The agreed project scope."}],
                "still_unknown": "Their available time.", "first_step": "Ask about availability."}
        return result

    def preview(self):
        response = self.post("/app/reports/preview", report_text=self.pasted())
        self.assertEqual(response.status_code, 200, response.text)
        return self.field(response, "preview_token")

    def state(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200, response.text)
        return re.search(r'data-dashboard-state="([^"]+)"', response.text).group(1)

    def expiry(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash(self.tokens["ama"]),)).fetchone()[0]

    def test_preview_is_escaped_and_does_not_store_report_or_receipt(self):
        response = self.post("/app/reports/preview", report_text=self.pasted())
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nothing has been saved", response.text)
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertIn('rel="nofollow noopener ugc"', response.text)
        self.assertEqual(self.reports.list_reports("ama"), [])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_receipts").fetchone()[0], 0)

    def test_confirmation_saves_once_and_same_confirm_replays(self):
        token = self.preview()
        for _ in range(2):
            response = self.post("/app/reports/confirm", preview_token=token)
            self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.reports.list_reports("ama")), 1)
        self.assertEqual(self.state(), "question")

    def test_confirmation_rejects_tampering_expiry_and_another_owner(self):
        token = self.preview()
        self.assertEqual(self.post("/app/reports/confirm", preview_token=token[:-1] + ("0" if token[-1] != "0" else "1")).status_code, 400)
        self.use("joon")
        self.assertEqual(self.post("/app/reports/confirm", preview_token=token).status_code, 400)
        self.use("ama")
        self.now += timedelta(minutes=10)
        self.assertEqual(self.post("/app/reports/confirm", preview_token=token).status_code, 400)
        self.assertEqual(self.reports.list_reports("ama"), [])
        self.assertEqual(self.reports.list_reports("joon"), [])

    def test_preview_and_confirm_require_csrf_origin_and_exact_fields(self):
        token = self.preview()
        for route, fields in (("preview", {"report_text": self.pasted()}), ("confirm", {"preview_token": token})):
            response = self.client.post("/app/reports/" + route, data={"csrf": "a" * 64, **fields})
            self.assertEqual(response.status_code, 403)
            response = self.client.post("/app/reports/" + route, data={"csrf": self.csrf, **fields}, headers={"Origin": "https://foreign.example"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.post("/app/reports/" + route, user_id="joon", **fields).status_code, 400)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_invalid_paste_keeps_escaped_text_in_editable_report_form(self):
        text = self.pasted() + 'Unexpected: </textarea><script>privateDraft()</script>\n'
        response = self.post("/app/reports/preview", report_text=text)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.retained_report(response), text)
        self.assertIn('role="alert"', response.text)
        self.assertIn("unknown, empty or repeated fields", response.text)
        self.assertNotIn("Sign-in needs another try", response.text)
        self.assertNotIn("<script>privateDraft()", response.text)
        self.assertNotIn("<script>alert(1)", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.reports.list_reports("ama"), [])
        self.assertEqual(self.field(response, "csrf"), self.csrf)
        corrected = self.post("/app/reports/preview", report_text=self.pasted())
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(self.post("/app/reports/confirm", preview_token=self.field(corrected, "preview_token")).status_code, 303)
        self.assertEqual(len(self.reports.list_reports("ama")), 1)

    def test_stale_csrf_retains_same_origin_paste_and_issues_current_token(self):
        old_csrf = self.csrf
        with self.db.transaction() as connection:
            self.tokens["ama"] = self.auth.sessions.create(connection, "ama", self.tokens["ama"])
        self.use("ama")
        text = self.pasted()
        response = self.client.post("/app/reports/preview", data={"csrf": old_csrf, "report_text": text},
                                    headers={"Origin": self.auth.settings.public_base_url})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.retained_report(response), text)
        self.assertEqual(self.field(response, "csrf"), self.csrf)
        self.assertNotEqual(self.field(response, "csrf"), old_csrf)
        self.assertIn("Nothing was saved", response.text)
        self.assertEqual(self.reports.list_reports("ama"), [])
        retried = self.post("/app/reports/preview", report_text=self.retained_report(response))
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_untrusted_or_unauthenticated_paste_is_not_retained(self):
        marker = "Fictional private report marker"
        for origin in (None, "https://foreign.example", "null"):
            headers = {} if origin is None else {"Origin": origin}
            response = self.client.post("/app/reports/preview", data={"csrf": "0" * 64, "report_text": marker}, headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(marker, response.text)
        response = self.client.post("/app/reports/preview", data={"csrf": self.csrf, "report_text": marker},
                                    headers={"Origin": "https://foreign.example"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(marker, response.text)
        self.client.cookies.clear()
        response = self.client.post("/app/reports/preview", data={"csrf": self.csrf, "report_text": marker},
                                    headers={"Origin": self.auth.settings.public_base_url}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn(marker, response.text)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_expired_preview_preserves_each_report_kind_but_requires_new_confirmation(self):
        from tests_server.test_report_schema import PASTE_CONNECTION
        for original in (self.pasted("nothing"), self.pasted(), PASTE_CONNECTION):
            with self.subTest(kind=parse_paste(original)["kind"]):
                preview = self.post("/app/reports/preview", report_text=original)
                token = self.field(preview, "preview_token")
                count = len(self.reports.list_reports("ama"))
                self.now += timedelta(minutes=10)
                expired = self.post("/app/reports/confirm", preview_token=token)
                self.assertEqual(expired.status_code, 400)
                self.assertIn("preview expired after ten minutes", expired.text)
                self.assertNotIn("Sign-in needs another try", expired.text)
                recovered = self.retained_report(expired)
                self.assertEqual(parse_paste(recovered), parse_paste(original))
                self.assertNotIn("<script>alert(1)", expired.text)
                self.assertEqual(len(self.reports.list_reports("ama")), count)
                fresh = self.post("/app/reports/preview", report_text=recovered)
                fresh_token = self.field(fresh, "preview_token")
                self.assertNotEqual(fresh_token, token)
                self.assertEqual(len(self.reports.list_reports("ama")), count)
                self.assertEqual(self.post("/app/reports/confirm", preview_token=fresh_token).status_code, 303)
                self.assertEqual(len(self.reports.list_reports("ama")), count + 1)
                self.assertEqual(self.post("/app/reports/confirm", preview_token=token).status_code, 400)
                self.assertEqual(len(self.reports.list_reports("ama")), count + 1)

    def test_stale_csrf_confirmation_recovers_only_authentic_owner_preview(self):
        token = self.preview()
        headers = {"Origin": self.auth.settings.public_base_url}
        response = self.client.post("/app/reports/confirm", data={"csrf": "0" * 64, "preview_token": token}, headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(parse_paste(self.retained_report(response)), parse_paste(self.pasted()))
        self.assertEqual(self.reports.list_reports("ama"), [])
        changed = token[:-1] + ("0" if token[-1] != "0" else "1")
        response = self.client.post("/app/reports/confirm", data={"csrf": "0" * 64, "preview_token": changed}, headers=headers)
        self.assertEqual(self.retained_report(response), "")
        self.use("joon")
        response = self.client.post("/app/reports/confirm", data={"csrf": "0" * 64, "preview_token": token}, headers=headers)
        self.assertEqual(self.retained_report(response), "")
        self.now += timedelta(minutes=10)
        response = self.post("/app/reports/confirm", preview_token=token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.retained_report(response), "")
        self.assertEqual(self.reports.list_reports("joon"), [])

    def test_long_paste_editing_does_not_expire_session_csrf(self):
        self.client.get("/app/reports/paste")
        self.now += timedelta(minutes=11)
        self.assertEqual(self.post("/app/reports/preview", report_text=self.pasted()).status_code, 200)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_keys_and_personalised_prompt_are_shown_once_and_download_without_javascript(self):
        response = self.post("/app/keys", name="My chosen assistant")
        self.assertEqual(response.status_code, 200, response.text)
        key = re.search(r"ctb_[A-Z2-7]{52}", response.text).group(0)
        token = self.field(response, "download_token")
        self.assertIn("https://reports.example.test/api/v1/reports", response.text)
        download = self.post("/app/keys/prompt-download", download_token=token)
        self.assertEqual(download.status_code, 200)
        self.assertIn(key, download.text)
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(download.headers["cache-control"], "no-store")
        for route in ("/app", "/app/keys", "/app/account"):
            self.assertNotIn(key, self.client.get(route).text)
        with self.db.connect() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM report_keys")]
        self.assertNotIn(key, json.dumps(rows))
        self.use("joon")
        self.assertEqual(self.post("/app/keys/prompt-download", download_token=token).status_code, 400)

    def test_new_account_connects_its_agent_with_one_click(self):
        dashboard = self.client.get("/app")
        self.assertIn("Connect your agent.", dashboard.text)
        form = re.search(r'<form method="post" action="/app/keys" data-full-prompt>(.*?)</form>', dashboard.text, re.DOTALL)
        self.assertIsNotNone(form, dashboard.text)
        self.assertIn('name="csrf" value="' + self.csrf + '"', form.group(1))
        self.assertIn('name="name" value="My agent"', form.group(1))
        created = self.post("/app/keys", name="My agent")
        self.assertEqual(created.status_code, 200, created.text)
        self.assertIn("Copy this into your assistant.", created.text)
        self.assertRegex(created.text, r"ctb_[A-Z2-7]{52}")
        self.assertNotIn("data-full-prompt", self.client.get("/app").text)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT name, agent_api FROM report_keys WHERE user_id='ama'").fetchone()[:], ("My agent", 1))

    def test_maximum_keys_and_owner_bound_rename_revoke(self):
        for name in ("One", "Two", "Three"):
            self.assertEqual(self.post("/app/keys", name=name).status_code, 200)
        self.assertEqual(self.post("/app/keys", name="Four").status_code, 409)
        foreign = self.reports.create_key("joon", "Foreign")
        self.assertEqual(self.post(f"/app/keys/{foreign['id']}/revoke").status_code, 404)
        self.assertEqual(self.post(f"/app/keys/{foreign['id']}/rename", name="Taken").status_code, 404)
        own = self.reports.list_keys("ama")[0]
        self.assertEqual(self.post(f"/app/keys/{own['id']}/rename", name="Renamed").status_code, 303)
        self.assertEqual(self.post(f"/app/keys/{own['id']}/revoke").status_code, 303)
        self.assertEqual(self.post("/app/keys", name="Replacement").status_code, 200)

    def test_bearer_api_cannot_use_session_cookie_and_me_has_no_email_or_secret(self):
        key = self.reports.create_key("ama", "API")
        body = json.dumps(self.payload()).encode()
        self.assertNotEqual(self.client.post("/api/v1/reports", content=body, headers={"Content-Type": "application/json", "Idempotency-Key": "attempt-one"}).status_code, 201)
        headers = {"Authorization": "Bearer " + key["key"], "Content-Type": "application/json", "Idempotency-Key": "attempt-one"}
        first = self.client.post("/api/v1/reports", content=body, headers=headers)
        second = self.client.post("/api/v1/reports", content=body, headers=headers)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())
        result = self.client.get("/api/v1/me", headers={"Authorization": headers["Authorization"]})
        self.assertEqual(result.json(), {"owner": {"id": "ama", "display_name": "ama"}, "posting": None, "posting_available": False})
        self.assertNotIn("@", result.text)
        self.assertNotIn(key["key"], result.text)
        self.reports.revoke_key("ama", key["id"])
        self.assertEqual(self.client.post("/api/v1/reports", content=body, headers=headers).status_code, 401)

    def test_api_refuses_non_json_and_duplicate_credentials_or_idempotency(self):
        key = self.reports.create_key("ama", "API")
        headers = [("Authorization", "Bearer " + key["key"]), ("Idempotency-Key", "one"), ("Content-Type", "application/json")]
        for additional in (("Authorization", "Bearer " + key["key"]), ("Idempotency-Key", "two")):
            self.assertEqual(self.client.post("/api/v1/reports", content=json.dumps(self.payload()), headers=headers + [additional]).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/reports", content="not json", headers={"Authorization": "Bearer " + key["key"], "Idempotency-Key": "one", "Content-Type": "text/plain"}).status_code, 415)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_seven_states_follow_rows_with_pause_and_pending_precedence(self):
        self.assertEqual(self.state(), "setup")
        self.reports.create_key("ama", "Assistant")
        self.assertEqual(self.state(), "setup")
        self.reports.save_paste("ama", self.payload(), "nothing-one")
        self.assertEqual(self.state(), "quiet")
        question = self.reports.save_paste("ama", self.payload("question"), "question-one")
        self.assertEqual(self.state(), "question")
        connection = self.reports.save_paste("ama", self.payload("connection"), "connection-one")
        self.assertEqual(self.state(), "connection")
        self.assertEqual(self.post("/app/settings", paused="true", cadence="1440").status_code, 303)
        self.assertEqual(self.state(), "paused")
        self.assertIn("does not pause your assistant", self.client.get("/app").text)
        self.post("/app/settings", paused="false", cadence="1440")
        self.now += timedelta(hours=30)
        self.assertEqual(self.state(), "connection")
        for receipt in (connection, question):
            self.assertEqual(self.post(f"/app/reports/{receipt['report_id']}/handled").status_code, 303)
        self.assertEqual(self.state(), "failed")
        self.post("/app/settings", paused="false", cadence="none")
        self.assertEqual(self.state(), "working")

    def test_new_quiet_report_does_not_dismiss_pending_question(self):
        self.reports.save_paste("ama", self.payload("question"), "question")
        self.reports.save_paste("ama", self.payload(), "nothing")
        self.assertEqual(self.state(), "question")

    def test_only_owner_can_mark_report_reviewed_and_unknown_fields_refused(self):
        receipt = self.reports.save_paste("joon", self.payload("question"), "foreign-report")
        self.assertEqual(self.post(f"/app/reports/{receipt['report_id']}/handled").status_code, 404)
        self.assertIsNone(self.reports.list_reports("joon")[0]["handled_at"])
        self.assertEqual(self.post("/app/settings", paused="false", cadence="1440", user_id="joon").status_code, 400)
        self.assertEqual(self.post("/app/settings", paused="false", cadence="15").status_code, 422)
        self.assertIsNone(self.reports.settings("ama")["cadence_minutes"])

    def test_valid_activity_slides_cookie_but_bad_forms_do_not_and_absolute_limit_remains(self):
        created = self.now
        original = self.expiry()
        self.now += timedelta(days=29)
        self.client.post("/app/keys", data={"name": "Bad", "csrf": "a" * 64})
        self.assertEqual(self.expiry(), original)
        self.post("/app/settings", paused="false", cadence="invalid")
        self.assertEqual(self.expiry(), original)
        response = self.client.get("/app")
        self.assertEqual(self.expiry(), storage_time(self.now + timedelta(days=30)))
        cookies = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookies.load(header)
        self.assertEqual(int(cookies[SESSION_COOKIE]["max-age"]), 30 * 86400)
        for day in (58, 87):
            self.now = created + timedelta(days=day)
            self.assertEqual(self.client.get("/app/keys").status_code, 200)
        self.assertEqual(self.expiry(), storage_time(created + timedelta(days=90)))
        self.now = created + timedelta(days=90)
        self.assertEqual(self.client.get("/app", follow_redirects=False).status_code, 303)

    def test_paused_intake_rejects_confirmation_without_persisting(self):
        token = self.preview()
        self.post("/app/settings", paused="true", cadence="none")
        self.assertEqual(self.post("/app/reports/confirm", preview_token=token).status_code, 409)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_secret_download_cannot_be_used_as_a_report_confirmation(self):
        response = self.post("/app/keys", name="Assistant")
        token = self.field(response, "download_token")
        self.assertEqual(self.post("/app/reports/confirm", preview_token=token).status_code, 400)
        self.assertEqual(self.reports.list_reports("ama"), [])


if __name__ == "__main__":
    unittest.main()
