"""Notification ownership, native preference forms and narrow unsubscribe tokens."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.notifications import KINDS
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time, token_hash


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class NotificationWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://notifications.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-notifications-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(self.settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.public_base_url))
        self.auth, self.db = self.app.state.auth, self.app.state.database
        self.notifications, self.interactions, self.board = self.app.state.notifications, self.app.state.interactions, self.app.state.board
        self.tokens, self.postings, self.first_events = {}, {}, {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon", "sora"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "private-google-sub-" + owner, owner + "@private.example.test", "PrivateGoogleName" + owner,
                     storage_time(self.now), storage_time(self.now)))
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
                self.first_events[owner] = self.notifications.record_event(connection, owner, "security_first_login")
        for owner in self.tokens:
            draft = self.board.save_draft(owner, {"text": "I build fictional garden tools. I seek a thoughtful collaborator. I offer a small trial.",
                                                "tags": ["Research"], "looking_for": "A shared experiment", "offering": "Careful prototyping"})
            self.postings[owner] = self.board.publish(owner, draft["revision"], draft["payload_hash"])
        self.use("ama")

    def use(self, owner):
        self.owner = owner
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=False)

    def introduction(self):
        preview = self.interactions.prepare_request("ama", self.postings["joon"]["slug"], "Private-note-canary <script>alert(1)</script>", "Private-reason-canary")
        return self.interactions.request("ama", preview, "fictional-intro-nonce-0001")

    def conversation(self):
        intro = self.introduction()
        return self.interactions.accept("joon", intro["id"], intro["context_hash"])["conversation"]

    def subscribe(self, owner, *kinds):
        self.notifications.update_preferences(owner, {kind: kind in kinds for kind in KINDS})

    def token_url(self, owner="ama", kind="new_message"):
        return "/email/unsubscribe/" + self.notifications.unsubscribe_token(owner, kind)

    def expiry(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash(self.tokens["ama"]),)).fetchone()[0]

    def test_preferences_default_to_security_only_and_save_native_checkboxes(self):
        page = self.client.get("/app/notifications/preferences")
        self.assertEqual(page.status_code, 200)
        checked = re.findall(r'<input[^>]+type="checkbox"[^>]+name="([^"]+)"[^>]* checked', page.text)
        self.assertEqual(checked, ["security_first_login"])
        self.assertIn('data-email-mode="outbox"', page.text)
        self.assertIn("no email will be sent until delivery is enabled", page.text)
        response = self.post("/app/notifications/preferences", introduction_request="on", weekly_summary="on")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/app/notifications/preferences")
        self.assertEqual(self.notifications.preferences("ama"), {kind: kind in {"introduction_request", "weekly_summary"} for kind in KINDS})
        self.assertEqual(self.notifications.preferences("joon"), {kind: kind == "security_first_login" for kind in KINDS})
        self.assertEqual(self.post("/app/notifications/preferences").status_code, 303)
        self.assertFalse(any(self.notifications.preferences("ama").values()))

    def test_bell_counts_in_app_events_without_email_opt_in_on_all_private_pages(self):
        self.introduction()
        self.use("joon")
        self.assertFalse(self.notifications.preferences("joon")["introduction_request"])
        self.assertEqual(self.notifications.unread_count("joon"), 2)
        for path in ("/app", "/app/keys", "/app/reports/paste", "/app/posting", "/app/introductions", "/app/conversations", "/app/account"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('aria-label="Notifications, 2 unread"', response.text)
                self.assertRegex(response.text, r'data-notification-count>2</span>')
        self.client.cookies.clear()
        self.assertNotIn("data-notification-count", self.client.get("/signin").text)
        self.assertNotIn("data-notification-count", self.client.get("/board").text)

    def test_list_is_generic_and_read_does_not_happen_on_get(self):
        intro = self.introduction()
        self.use("joon")
        page = self.client.get("/app/notifications")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Someone would like an introduction.", page.text)
        self.assertIn('href="/app/introductions/' + intro["id"] + '"', page.text)
        for private in ("Private-note-canary", "Private-reason-canary", "PrivateGoogleNameama", "ama@private.example.test", "private-google-sub-ama"):
            self.assertNotIn(private, page.text)
        self.assertEqual(self.notifications.unread_count("joon"), 2)
        event = next(item for item in self.notifications.list_items("joon")["items"] if item["kind"] == "introduction_request")
        self.assertEqual(self.client.get(f'/app/notifications/{event["id"]}/read').status_code, 405)
        self.assertEqual(self.post(f'/app/notifications/{event["id"]}/read').status_code, 303)
        self.assertEqual(self.notifications.unread_count("joon"), 1)
        self.assertEqual(self.post(f'/app/notifications/{event["id"]}/read').status_code, 303)
        self.assertEqual(self.notifications.unread_count("joon"), 1)

    def test_read_and_preferences_require_current_owner_csrf_origin_and_strict_fields(self):
        before = self.notifications.preferences("ama")
        for path, values in (("/app/notifications/preferences", {"new_message": "on"}),
                             ("/app/notifications/" + self.first_events["ama"] + "/read", {})):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data={"csrf": "0" * 64, **values}).status_code, 403)
                self.assertEqual(self.client.post(path, data={"csrf": self.csrf, **values}, headers={"Origin": "https://foreign.example"}).status_code, 403)
                self.assertEqual(self.post(path, owner_id="joon", **values).status_code, 400)
                self.assertEqual(self.post(path + "?owner=joon", **values).status_code, 400)
        self.assertEqual(self.post("/app/notifications/preferences", new_message="true").status_code, 400)
        self.assertEqual(self.client.post("/app/notifications/preferences", content="csrf=" + self.csrf + "&new_message=on&new_message=on", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code, 400)
        self.assertEqual(self.notifications.preferences("ama"), before)
        self.assertEqual(self.notifications.unread_count("ama"), 1)
        self.assertEqual(self.post("/app/notifications/" + self.first_events["joon"] + "/read").status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/app/notifications/" + self.first_events["ama"] + "/read", data={"csrf": self.csrf}, follow_redirects=False).status_code, 303)

    def test_pagination_reaches_older_items_and_mark_read_keeps_requested_page(self):
        conversation = self.conversation()
        for number in range(21):
            self.interactions.send_message("joon", conversation["id"], "Private-message-canary", f"fictional-message-{number:08d}")
        page1 = self.client.get("/app/notifications")
        self.assertIn("Next page", page1.text)
        self.assertNotIn(self.first_events["ama"], page1.text)
        page2 = self.client.get("/app/notifications?page=2")
        self.assertIn(self.first_events["ama"], page2.text)
        self.assertIn("Previous page", page2.text)
        self.assertNotIn("Private-message-canary", page1.text)
        response = self.post("/app/notifications/" + self.first_events["ama"] + "/read", page="2")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/app/notifications?page=2")
        for query in ("page=0", "page=10001", "page=1&page=2", "owner=joon", "page=https://foreign.example"):
            self.assertEqual(self.client.get("/app/notifications?" + query).status_code, 400)

    def test_unsubscribe_get_and_head_are_scanner_safe_and_do_not_refresh_session(self):
        self.subscribe("ama", "security_first_login", "new_message", "weekly_summary")
        url = self.token_url()
        before = self.notifications.preferences("ama")
        original_expiry = self.expiry()
        self.now += timedelta(days=29)
        for method in ("GET", "HEAD"):
            response = self.client.request(method, url)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(self.notifications.preferences("ama"), before)
        page = self.client.get(url)
        self.assertIn("Stop these emails?", page.text)
        self.assertNotIn("@private.example.test", page.text)
        self.assertNotIn("PrivateGoogleName", page.text)
        self.assertNotIn(self.tokens["ama"], page.text)
        self.assertEqual(self.expiry(), original_expiry)

    def test_cookie_free_one_click_changes_only_token_owner_and_kind_without_redirect(self):
        for owner in ("ama", "joon"):
            self.subscribe(owner, *KINDS)
        url = self.token_url()
        self.use("joon")  # Unrelated browser identity must have no authority here.
        response = self.client.post(url, data={"List-Unsubscribe": "One-Click"}, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)
        self.assertNotIn("set-cookie", response.headers)
        self.assertIn("Email preference updated.", response.text)
        self.assertEqual(self.notifications.preferences("ama"), {kind: kind != "new_message" for kind in KINDS})
        self.assertTrue(all(self.notifications.preferences("joon").values()))
        self.client.cookies.clear()
        repeated = self.client.post(url, data={"List-Unsubscribe": "One-Click"}, follow_redirects=False)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.notifications.unread_count("ama"), 1)

    def test_rfc_multipart_one_field_works_without_cookie(self):
        self.subscribe("ama", *KINDS)
        url = self.token_url(kind="security_first_login")
        self.client.cookies.clear()
        response = self.client.post(url, files={"List-Unsubscribe": (None, "One-Click")}, follow_redirects=False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.notifications.preferences("ama")["security_first_login"])
        self.assertTrue(self.notifications.preferences("ama")["new_message"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT state FROM email_outbox WHERE user_id='ama' AND kind='security_first_login'").fetchone()[0], "cancelled")

    def test_unsubscribe_rejects_files_extra_duplicate_wrong_and_oversized_fields(self):
        self.subscribe("ama", *KINDS)
        url = self.token_url()
        cases = [
            {"data": {"List-Unsubscribe": "One-Click", "owner_id": "joon"}},
            {"data": {"List-Unsubscribe": "Other"}},
            {"data": {"csrf": self.csrf}},
            {"content": "List-Unsubscribe=One-Click&List-Unsubscribe=One-Click", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
            {"json": {"List-Unsubscribe": "One-Click"}},
            {"files": {"List-Unsubscribe": ("note.txt", "One-Click")}},
            {"files": [("List-Unsubscribe", (None, "One-Click")), ("List-Unsubscribe", (None, "One-Click"))]},
            {"files": {"List-Unsubscribe": (None, "One-Click"), "owner": (None, "joon")}},
            {"content": b'--test\r\nContent-Disposition: form-data; name="List-Unsubscribe"; name="owner"\r\n\r\nOne-Click\r\n--test--\r\n', "headers": {"Content-Type": "multipart/form-data; boundary=test"}},
            {"content": b'--test\r\nContent-Disposition: form-data; name="List-Unsubscribe"\r\nContent-Type: multipart/mixed; boundary=inner\r\n\r\n--inner\r\n\r\nOne-Click\r\n--inner--\r\n--test--\r\n', "headers": {"Content-Type": "multipart/form-data; boundary=test"}},
            {"content": b"x" * 1025, "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
        ]
        for values in cases:
            with self.subTest(values=list(values)):
                response = self.client.post(url, **values)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertNotIn("@private.example.test", response.text)
                self.assertTrue(self.notifications.preferences("ama")["new_message"])
        self.assertEqual(self.client.post(url, data={"List-Unsubscribe": "One-Click"}, headers={"Origin": "https://foreign.example"}).status_code, 403)
        self.assertEqual(self.client.get(url + "?owner=joon").status_code, 400)
        self.assertEqual(self.client.put(url, data={"List-Unsubscribe": "One-Click"}).status_code, 405)

    def test_invalid_and_deleted_owner_tokens_are_generic_without_side_effects(self):
        self.subscribe("ama", *KINDS)
        url = self.token_url()
        changed = url[:-1] + ("0" if url[-1] != "0" else "1")
        for invalid in (changed, url.replace(".new_message.", ".weekly_summary."), "/email/unsubscribe/invalid"):
            for method in ("GET", "POST"):
                response = self.client.request(method, invalid, data={"List-Unsubscribe": "One-Click"} if method == "POST" else None)
                self.assertEqual(response.status_code, 400)
                self.assertIn("This email link is not available.", response.text)
                self.assertNotIn("@private.example.test", response.text)
        self.assertTrue(self.notifications.preferences("ama")["new_message"])
        self.assertEqual(self.post("/app/account/delete", confirmation="DELETE").status_code, 200)
        self.assertEqual(self.client.post(url, data={"List-Unsubscribe": "One-Click"}).status_code, 400)

    def test_closed_source_notifications_are_cancelled_instead_of_exposing_stale_links(self):
        conversation = self.conversation()
        self.interactions.send_message("joon", conversation["id"], "Private-message-canary", "fictional-message-cancel")
        self.assertIn("You have new messages.", self.client.get("/app/notifications").text)
        self.interactions.close_conversation("ama", conversation["id"])
        response = self.client.get("/app/notifications")
        self.assertNotIn("You have new messages.", response.text)
        self.assertNotIn('href="/app/conversations/' + conversation["id"] + '"', response.text)

    def test_email_mode_copy_never_claims_delivery_or_opens_transport(self):
        with patch("smtplib.SMTP") as smtp:
            page = self.client.get("/app/notifications/preferences")
            self.assertIn("no email will be sent until delivery is enabled", page.text)
            # Only the display setting changes here; transport is tested separately.
            self.auth.settings = replace(self.settings, email_mode="smtp")
            page = self.client.get("/app/notifications/preferences")
            self.assertIn('data-email-mode="smtp"', page.text)
            self.assertIn("We’ll use your account email for the updates you select.", page.text)
            smtp.assert_not_called()

    def test_weekly_notification_displays_actual_fixed_period_counts_without_private_words(self):
        self.subscribe("ama", "weekly_summary")
        self.now += timedelta(days=6)
        conversation = self.conversation()
        self.interactions.send_message("joon", conversation["id"], "Private-message-canary", "fictional-summary-message")
        self.now += timedelta(days=1, seconds=1)
        self.notifications.run_due()
        weekly = next(item for item in self.notifications.list_items("ama")["items"] if item["kind"] == "weekly_summary")
        response = self.client.get("/app/notifications")
        self.assertIn("data-notification-summary", response.text)
        self.assertIn(weekly["summary"], response.text)
        self.assertRegex(weekly["summary"], r"0 reports received; 1 available introduction updates?; 1 messages? received")
        self.assertNotIn("Private-message-canary", response.text)
        self.assertNotIn("Private-note-canary", response.text)
        self.now += timedelta(seconds=1)
        self.interactions.send_message("joon", conversation["id"], "After-period-canary", "fictional-after-summary")
        self.now += timedelta(days=1)
        after = next(item for item in self.notifications.list_items("ama")["items"] if item["kind"] == "weekly_summary")
        self.assertEqual(after["summary"], weekly["summary"])

    def test_valid_owner_activity_renews_but_invalid_forms_and_token_posts_do_not(self):
        original = self.expiry()
        self.now += timedelta(days=29)
        self.assertEqual(self.post("/app/notifications/preferences", csrf="0" * 64, new_message="on").status_code, 403)
        self.assertEqual(self.expiry(), original)
        self.assertEqual(self.client.get("/app/notifications?page=0").status_code, 400)
        self.assertEqual(self.expiry(), original)
        self.assertEqual(self.client.get("/app/notifications").status_code, 200)
        self.assertGreater(self.expiry(), original)
        for _ in range(2):
            self.now += timedelta(days=29)
            self.assertEqual(self.client.get("/app/notifications/preferences").status_code, 200)
        self.assertEqual(self.expiry(), storage_time(datetime(2026, 10, 30, 12, tzinfo=timezone.utc) + timedelta(days=90)))


if __name__ == "__main__":
    unittest.main()
