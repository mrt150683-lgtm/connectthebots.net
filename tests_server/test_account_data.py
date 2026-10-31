"""Account data isolation, CSRF, confirmation and complete current-schema deletion."""
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.notifications import NotificationError
    from connectthebots_server.sessions import SESSION_COOKIE, random_token, storage_time, token_hash


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class AccountDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://accounts.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-test-only-" + "x" * 32,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.auth = self.app.state.auth
        self.db = self.app.state.database
        self.tokens = {}
        self.emails = {"ama": "ama@example.test", "joon": "joon@example.test"}
        with self.db.transaction() as connection:
            for user, email in self.emails.items():
                connection.execute(
                    "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (user, "fictional-" + user, email, "<script>alert(1)</script>" if user == "ama" else "Joon",
                     storage_time(self.now), storage_time(self.now)),
                )
                self.tokens[user] = self.auth.sessions.create(connection, user)
                connection.execute("INSERT INTO email_outbox(id,user_id,kind,created_at,payload_json) VALUES(?,?,?,?,?)",
                                   ("event-" + user, user, "security_first_login", storage_time(self.now), "{}"))
        self.use("ama")

    def use(self, user):
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[user])
        self.csrf = self.auth.sessions.current(self.tokens[user]).csrf

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=False)

    def stored_expiry(self):
        with self.db.connect() as connection:
            return dict(connection.execute("SELECT expires_at, absolute_expires_at FROM sessions WHERE token_hash = ?", (token_hash(self.tokens["ama"]),)).fetchone())

    def cookie_lifetime(self, response):
        cookies = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookies.load(header)
        return int(cookies[SESSION_COOKIE]["max-age"])

    def test_export_contains_only_current_owner_and_no_authentication_material(self):
        result = self.post("/app/account/export")
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data["profile"]["google_sub"], "fictional-ama")
        self.assertEqual(data["profile"]["email"], self.emails["ama"])
        self.assertEqual(len(data["email_outbox"]), 1)
        self.assertEqual(data["notification_events"], [])
        self.assertNotIn(self.emails["joon"], result.text)
        for secret in (self.tokens["ama"], token_hash(self.tokens["ama"]), self.csrf):
            self.assertNotIn(secret, result.text)
        self.assertIn("attachment", result.headers["content-disposition"])
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_supplied_owner_and_unknown_fields_are_rejected(self):
        for route in ("export", "delete"):
            result = self.post("/app/account/" + route, user_id="joon", confirmation="DELETE")
            self.assertEqual(result.status_code, 400)
        self.assertIsNotNone(self.auth.sessions.current(self.tokens["joon"]))

    def test_export_and_delete_require_session_csrf_and_same_origin(self):
        for route in ("export", "delete"):
            for request in (
                {"data": {"csrf": "x" * 64, "confirmation": "DELETE"} if route == "delete" else {"csrf": "x" * 64}},
                {"data": {"csrf": self.csrf}, "headers": {"Origin": "https://foreign.example"}},
            ):
                response = self.client.post("/app/account/" + route, **request, follow_redirects=False)
                self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.post("/app/account/export").status_code, 303)

    def test_delete_requires_explicit_confirmation_and_refuses_get(self):
        self.assertEqual(self.post("/app/account/delete").status_code, 422)
        self.assertEqual(self.post("/app/account/delete", confirmation="delete").status_code, 422)
        self.assertEqual(self.client.get("/app/account/delete").status_code, 405)
        self.assertIsNotNone(self.auth.sessions.current(self.tokens["ama"]))

    def test_delete_removes_all_current_owner_rows_and_keeps_only_date_receipt(self):
        with self.db.transaction() as connection:
            second_token = self.auth.sessions.create(connection, "ama")
            connection.execute("INSERT INTO waitlist(id,email,consent_text,created_at,removal_hash) VALUES(?,?,?,?,?)",
                               ("waiting", self.emails["ama"], "fictional consent", storage_time(self.now), token_hash(random_token())))
        result = self.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 200)
        self.assertIn("Your account is deleted", result.text)
        self.assertIsNone(self.auth.sessions.current(self.tokens["ama"]))
        self.assertIsNone(self.auth.sessions.current(second_token))
        self.assertIsNotNone(self.auth.sessions.current(self.tokens["joon"]))
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM users WHERE id = ?", ("ama",)).fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM email_outbox WHERE user_id = ?", ("ama",)).fetchone())
            self.assertIsNotNone(connection.execute("SELECT 1 FROM waitlist WHERE email = ?", (self.emails["ama"],)).fetchone())
            receipts = [dict(row) for row in connection.execute("SELECT * FROM deletion_receipts")]
            self.assertEqual(receipts, [{"deleted_on": "2026-10-30"}])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIn("Max-Age=0", result.headers["set-cookie"])

    def test_same_email_accounts_cannot_export_or_delete_a_separate_waitlist_request(self):
        consent = "Fictional private waitlist consent for a separate request"
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET email = ? WHERE id = ?", (self.emails["ama"], "joon"))
            connection.execute("INSERT INTO waitlist(id,email,consent_text,created_at,removal_hash) VALUES(?,?,?,?,?)",
                               ("separate-request", self.emails["ama"], consent, storage_time(self.now), token_hash(random_token())))
        for user in ("ama", "joon"):
            self.use(user)
            exported = self.post("/app/account/export")
            self.assertEqual(exported.status_code, 200)
            self.assertEqual(exported.json()["profile"]["google_sub"], "fictional-" + user)
            self.assertNotIn("waitlist", exported.json())
            self.assertNotIn(consent, exported.text)
            deleted = self.post("/app/account/delete", confirmation="DELETE")
            self.assertEqual(deleted.status_code, 200)
            self.assertIn("/auth/waitlist/remove", deleted.text)
            with self.db.connect() as connection:
                self.assertEqual(connection.execute("SELECT consent_text FROM waitlist WHERE id = ?", ("separate-request",)).fetchone()[0], consent)

    def test_display_name_is_escaped_on_account_data_page(self):
        result = self.client.get("/app/account")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", result.text)
        self.assertIn("&lt;script&gt;", result.text)

    def report_data(self):
        service = self.app.state.reports
        keys, receipts = {}, {}
        for owner in ("ama", "joon"):
            keys[owner] = service.create_key(owner, "Fictional assistant " + owner)
            body = json.dumps({
                "kind": "nothing", "checked_at": self.now.isoformat(),
                "places_checked": [{"name": "Fictional guild " + owner, "url": "https://example.test/guild"}],
                "posts_read": 3,
            }).encode("utf-8")
            receipts[owner] = service.ingest("Bearer " + keys[owner]["key"], body, "first-report")
            service.update_settings(owner, paused=False, cadence_minutes=1440)
        return keys, receipts

    def test_report_export_includes_owned_payload_and_key_metadata_without_key_secrets(self):
        from connectthebots_server.report_keys import key_hash
        keys, receipts = self.report_data()
        result = self.post("/app/account/export")
        self.assertEqual(result.status_code, 200)
        exported = result.json()
        self.assertEqual([key["id"] for key in exported["keys"]], [keys["ama"]["id"]])
        self.assertEqual([report["id"] for report in exported["reports"]], [receipts["ama"]["report_id"]])
        self.assertEqual(exported["reports"][0]["payload"]["places_checked"][0]["name"], "Fictional guild ama")
        self.assertEqual(exported["check_in_settings"]["cadence_minutes"], 1440)
        self.assertFalse(exported["check_in_settings"]["paused"])
        for excluded in (keys["ama"]["key"], key_hash(keys["ama"]["key"]), keys["joon"]["id"],
                         receipts["joon"]["report_id"], "Fictional guild joon"):
            self.assertNotIn(excluded, result.text)

    def test_account_deletion_revokes_keys_and_removes_reports_receipts_preferences_for_only_owner(self):
        from connectthebots_server.report_schema import ReportError
        keys, receipts = self.report_data()
        result = self.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 200)
        with self.db.connect() as connection:
            # Explicit table names keep this check independent of an export list.
            for query in (
                "SELECT user_id FROM report_keys", "SELECT user_id FROM reports",
                "SELECT user_id FROM report_receipts", "SELECT user_id FROM report_settings",
            ):
                self.assertEqual([row[0] for row in connection.execute(query)], ["joon"])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(ReportError) as denied:
            self.app.state.reports.authenticate("Bearer " + keys["ama"]["key"])
        self.assertEqual(denied.exception.status, 401)
        self.assertEqual(self.app.state.reports.authenticate("Bearer " + keys["joon"]["key"])["user_id"], "joon")

    def test_suspended_and_deleted_sessions_cannot_export(self):
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at = ? WHERE id = ?", (storage_time(self.now), "ama"))
        self.assertEqual(self.post("/app/account/export").status_code, 303)

    def board_data(self):
        result = {}
        for owner in ("ama", "joon"):
            payload = {"text": "I build fictional tools for " + owner + ". I offer careful testing. I seek a collaborator.",
                       "tags": ["testing"], "looking_for": "A fictional collaborator", "offering": "Testing"}
            draft = self.app.state.board.save_draft(owner, payload)
            published = self.app.state.board.publish(owner, draft["revision"], draft["payload_hash"])
            private = {**payload, "text": "This private revision belongs to " + owner + ". It has not been approved. It must stay private."}
            self.app.state.board.save_draft(owner, private)
            result[owner] = published
        return result

    def test_board_export_has_owned_draft_publication_history_and_exact_grants(self):
        published = self.board_data()
        result = self.post("/app/account/export")
        self.assertEqual(result.status_code, 200)
        exported = result.json()["postings"]
        self.assertEqual(exported["posting"]["published"], published["ama"])
        self.assertEqual([row["revision"] for row in exported["revisions"]], [1, 2])
        self.assertIn("private revision belongs to ama", exported["posting"]["draft"]["payload"]["text"])
        self.assertEqual(exported["publication_grants"][0]["payload_hash"], exported["revisions"][0]["payload_hash"])
        self.assertNotIn("private revision belongs to joon", result.text)
        self.assertNotIn(published["joon"]["slug"], result.text)

    def test_delete_removes_owned_board_history_and_immediately_updates_public_feed(self):
        published = self.board_data()
        before = self.client.get("/board.json").json()["updated_at"]
        result = self.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 200)
        feed = self.client.get("/board.json").json()
        self.assertGreater(feed["updated_at"], before)
        self.assertEqual([item["slug"] for item in feed["items"]], [published["joon"]["slug"]])
        self.assertEqual(self.client.get("/board/" + published["ama"]["slug"]).status_code, 404)
        with self.db.connect() as connection:
            for table in ("board_postings", "board_revisions", "board_publication_grants"):
                self.assertEqual({row[0] for row in connection.execute("SELECT user_id FROM " + table)}, {"joon"})
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_bearer_me_only_returns_its_owners_current_visible_approved_posting(self):
        keys, _ = self.report_data()
        published = self.board_data()
        headers = {"Authorization": "Bearer " + keys["ama"]["key"]}
        result = self.client.get("/api/v1/me", headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["posting"], published["ama"])
        self.assertTrue(result.json()["posting_available"])
        self.assertNotIn("private revision", result.text)
        self.assertNotIn(published["joon"]["slug"], result.text)
        self.app.state.board.unpublish("ama")
        withdrawn = self.client.get("/api/v1/me", headers=headers).json()
        self.assertIsNone(withdrawn["posting"])
        self.assertFalse(withdrawn["posting_available"])

    def interaction_data(self):
        self.board_data()
        service = self.app.state.interactions
        target = self.app.state.board.owner_posting("joon")["slug"]
        preview = service.prepare_request("ama", target, "Fictional introduction note canary.", "Fictional reason canary.")
        introduction = service.request("ama", preview, "fictional-introduction-nonce")
        accepted = service.accept("joon", introduction["id"], introduction["context_hash"])
        conversation = accepted["conversation"]["id"]
        service.send_message("ama", conversation, "Fictional erased-author message canary.", "ama-message-nonce")
        service.send_message("joon", conversation, "Fictional surviving-author message canary.", "joon-message-nonce")
        return introduction, conversation

    def test_export_includes_shared_introductions_but_only_own_message_bodies(self):
        introduction, conversation = self.interaction_data()
        result = self.post("/app/account/export")
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual([item["id"] for item in data["introductions"]], [introduction["id"]])
        self.assertEqual([item["id"] for item in data["conversations"]], [conversation])
        self.assertEqual([item["text"] for item in data["messages"]], ["Fictional erased-author message canary."])
        self.assertNotIn("Fictional surviving-author message canary.", result.text)
        self.assertNotIn(self.emails["joon"], result.text)

    def notification_data(self):
        service = self.app.state.notifications
        for owner in ("ama", "joon"):
            values = service.preferences(owner)
            values.update(introduction_request=True, introduction_accepted=True, new_message=True)
            service.update_preferences(owner, values)
        token = service.unsubscribe_token("ama", "new_message")
        introduction, conversation = self.interaction_data()
        self.now += timedelta(hours=1)
        service.run_due()
        return token, introduction, conversation

    def test_notification_export_includes_owned_choices_and_metadata_without_capabilities(self):
        token, _, _ = self.notification_data()
        result = self.post("/app/account/export")
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertTrue(data["notification_preferences"]["new_message"])
        self.assertFalse(data["notification_preferences"]["weekly_summary"])
        self.assertCountEqual([row["kind"] for row in data["notification_events"]],
                              ["introduction_accepted", "new_message"])
        self.assertCountEqual([row["kind"] for row in data["email_outbox"]],
                              ["security_first_login", "introduction_accepted", "new_message"])
        notification_export = json.dumps({key: data[key] for key in (
            "notification_preferences", "notification_events", "email_outbox")})
        for forbidden in (token, self.emails["joon"], "Fictional surviving-author message canary.",
                          "Fictional introduction note canary.", "payload_json", "lease_id", "dedupe_key"):
            self.assertNotIn(forbidden, notification_export)

    def test_deletion_removes_peer_notification_links_and_owner_unsubscribe_capability(self):
        token, introduction, conversation = self.notification_data()
        result = self.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 200)
        with self.assertRaises(NotificationError):
            self.app.state.notifications.token_info(token)
        with self.db.connect() as connection:
            for table in ("notification_events", "notification_mail_windows"):
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)
            for table in ("notification_preferences", "notification_subscriptions", "email_outbox"):
                self.assertIsNone(connection.execute("SELECT 1 FROM " + table + " WHERE user_id=?", ("ama",)).fetchone())
            self.assertEqual(connection.execute("SELECT kind FROM email_outbox WHERE user_id=?", ("joon",)).fetchall()[0][0], "security_first_login")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_deletion_keeps_notifications_and_preferences_in_same_transaction(self):
        token, _, _ = self.notification_data()
        service = self.app.state.notifications
        before = service.export_owner("ama")
        peer_before = service.export_owner("joon")
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_notification_erasure BEFORE INSERT ON deletion_receipts BEGIN SELECT RAISE(ABORT, 'fictional failure'); END")
        self.assertEqual(self.post("/app/account/delete", confirmation="DELETE").status_code, 500)
        self.assertEqual(service.export_owner("ama"), before)
        self.assertEqual(service.export_owner("joon"), peer_before)
        self.assertEqual(service.token_info(token)["kind"], "new_message")

    def test_pending_introduction_takes_dashboard_priority_over_quiet_report(self):
        self.report_data()
        published = self.board_data()
        service = self.app.state.interactions
        preview = service.prepare_request("ama", published["joon"]["slug"], "A fictional hello.", "A fictional shared project.")
        service.request("ama", preview, "fictional-dashboard-nonce")
        self.use("joon")
        result = self.client.get("/app")
        self.assertEqual(result.status_code, 200)
        self.assertIn("Someone would like an introduction.", result.text)
        self.assertIn("You have not agreed yet.", result.text)
        self.assertNotIn('<h2 class="attention-title">Nothing worth your attention.</h2>', result.text)
        self.assertNotIn(self.emails["ama"], result.text)
        self.assertNotIn("&lt;script&gt;alert(1)&lt;/script&gt;", result.text)

    def test_export_retains_own_authored_declined_request_but_not_withdrawn_peer_context(self):
        published = self.board_data()
        service = self.app.state.interactions
        preview = service.prepare_request("ama", published["joon"]["slug"], "Fictional authored request history.", "Fictional authored reason history.")
        request = service.request("ama", preview, "fictional-export-history-nonce")
        service.decline("joon", request["id"])
        service.block_introduction("ama", request["id"])
        exported = self.post("/app/account/export").json()
        self.assertEqual(exported["introductions"][0]["own_note"], "Fictional authored request history.")
        self.assertEqual(exported["introductions"][0]["own_reason"], "Fictional authored reason history.")
        self.assertEqual(exported["introductions"][0]["own_posting_snapshot"], published["ama"])
        self.assertIsNone(exported["introductions"][0]["recipient_posting"])
        self.assertEqual(exported["blocks"], [{"created_at": storage_time(self.now), "blocked_posting_slug": published["joon"]["slug"]}])
        self.use("joon")
        other = self.post("/app/account/export").json()
        self.assertIsNone(other["introductions"][0]["own_note"])
        self.assertEqual(other["blocks"], [])
        self.assertNotIn("Fictional authored request history.", json.dumps(other))

    def test_delete_erases_shared_request_context_and_own_messages_but_keeps_survivor_history(self):
        introduction, conversation = self.interaction_data()
        result = self.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 200)
        with self.db.connect() as connection:
            request = dict(connection.execute("SELECT * FROM introductions WHERE id=?", (introduction["id"],)).fetchone())
            self.assertIsNone(request["requester_id"])
            self.assertEqual(request["recipient_id"], "joon")
            for name in ("requester_snapshot_json", "recipient_snapshot_json", "requester_grant_id",
                         "recipient_grant_id", "note", "reason", "context_hash"):
                self.assertIsNone(request[name], name)
            messages = [dict(row) for row in connection.execute("SELECT sender_id,body FROM messages WHERE conversation_id=? ORDER BY body", (conversation,))]
            self.assertCountEqual(messages, [{"sender_id": None, "body": "[deleted]"},
                                            {"sender_id": "joon", "body": "Fictional surviving-author message canary."}])
            self.assertIsNotNone(connection.execute("SELECT closed_at FROM conversations WHERE id=?", (conversation,)).fetchone()[0])
            for table in ("intro_receipts", "message_receipts"):
                self.assertIsNone(connection.execute("SELECT 1 FROM " + table + " WHERE owner_id=?", ("ama",)).fetchone())
            dump = "\n".join(connection.iterdump())
            for erased in ("Fictional erased-author message canary.", "Fictional introduction note canary.",
                           "Fictional reason canary.", "private revision belongs to ama", self.emails["ama"]):
                self.assertNotIn(erased, dump)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.use("joon")
        history = self.client.get("/app/conversations/" + conversation)
        self.assertEqual(history.status_code, 200)
        self.assertIn("[deleted]", history.text)
        self.assertIn("Fictional surviving-author message canary.", history.text)
        self.assertNotIn("Fictional erased-author message canary.", history.text)
        self.assertEqual(self.post("/app/account/delete", confirmation="DELETE").status_code, 200)
        with self.db.connect() as connection:
            for table in ("introductions", "conversations", "messages", "intro_receipts", "message_receipts"):
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_failed_deletion_rolls_back_shared_redaction_and_account_removal_together(self):
        introduction, conversation = self.interaction_data()
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_erasure_receipt BEFORE INSERT ON deletion_receipts BEGIN SELECT RAISE(ABORT, 'fictional receipt failure'); END")
        result = self.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 500)
        self.assertNotIn("fictional receipt failure", result.text)
        self.assertIsNotNone(self.auth.sessions.current(self.tokens["ama"]))
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT note FROM introductions WHERE id=?", (introduction["id"],)).fetchone()[0], "Fictional introduction note canary.")
            self.assertIsNone(connection.execute("SELECT closed_at FROM conversations WHERE id=?", (conversation,)).fetchone()[0])
            self.assertEqual(connection.execute("SELECT body FROM messages WHERE sender_id=?", ("ama",)).fetchone()[0], "Fictional erased-author message canary.")

    def test_account_page_slides_session_at_day_twenty_nine_and_survives_day_thirty_one(self):
        created = self.now
        self.now = created + timedelta(days=29)
        result = self.client.get("/app/account", follow_redirects=False)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.stored_expiry()["expires_at"], storage_time(created + timedelta(days=59)))
        self.assertEqual(self.cookie_lifetime(result), 30 * 86400)
        self.now = created + timedelta(days=31)
        self.assertEqual(self.client.get("/app/account", follow_redirects=False).status_code, 200)

    def test_successful_export_slides_both_database_session_and_cookie(self):
        created = self.now
        self.now = created + timedelta(days=29)
        result = self.post("/app/account/export")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.stored_expiry()["expires_at"], storage_time(created + timedelta(days=59)))
        self.assertEqual(self.cookie_lifetime(result), 30 * 86400)
        self.now = created + timedelta(days=31)
        self.assertIsNotNone(self.auth.sessions.current(self.tokens["ama"], refresh=False))

    def test_account_activity_never_extends_absolute_ninety_day_limit(self):
        created = self.now
        for day in (29, 58, 87):
            self.now = created + timedelta(days=day)
            page = self.client.get("/app/account", follow_redirects=False)
            self.assertEqual(page.status_code, 200)
        self.assertEqual(self.stored_expiry()["expires_at"], storage_time(created + timedelta(days=90)))
        self.assertEqual(self.stored_expiry()["absolute_expires_at"], storage_time(created + timedelta(days=90)))
        self.assertEqual(self.cookie_lifetime(page), 3 * 86400)
        export = self.post("/app/account/export")
        self.assertEqual(export.status_code, 200)
        self.assertEqual(self.cookie_lifetime(export), 3 * 86400)
        self.now = created + timedelta(days=90)
        self.assertEqual(self.client.get("/app/account", follow_redirects=False).status_code, 303)

    def test_invalid_csrf_and_unconfirmed_delete_do_not_refresh_session(self):
        original = self.stored_expiry()
        self.now += timedelta(days=29)
        for path in ("/app/account/export", "/app/account/delete"):
            result = self.client.post(path, data={"csrf": "0" * 64}, follow_redirects=False)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(self.stored_expiry(), original)
            self.assertNotIn("set-cookie", result.headers)
        result = self.post("/app/account/delete", confirmation="not-confirmed")
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.stored_expiry(), original)
        self.assertNotIn("set-cookie", result.headers)


if __name__ == "__main__":
    unittest.main()
