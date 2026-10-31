"""Notification consent, durable batching, authority and delivery uncertainty."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from connectthebots_server.board import BoardService
from connectthebots_server.config import Settings
from connectthebots_server.db import Database, load_migrations
from connectthebots_server.interactions import InteractionService
from connectthebots_server.notifications import KINDS, NotificationError, NotificationService, main
from connectthebots_server.report_store import ReportService
from connectthebots_server.sessions import random_token, storage_time


class FakeTransport:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [{"status": "accepted", "code": "accepted"}])
        self.messages = []

    def send(self, envelope):
        self.messages.append(envelope)
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.settings = Settings.from_env({"APP_ENV": "staging", "PUBLIC_BASE_URL": "https://fictional.example.test", "DATABASE_PATH": str(Path(self.temp.name) / "notifications.db"), "SESSION_SECRET": "fictional-notification-secret-" + "x" * 32, "ACCOUNT_SIGNIN_ENABLED": "true", "GOOGLE_CLIENT_ID": "fictional", "GOOGLE_CLIENT_SECRET": "fictional"})
        self.db = Database(self.settings.database_path, lambda: self.now)
        self.db.migrate()
        self.board = BoardService(self.db, lambda: self.now)
        self.service = NotificationService(self.db, lambda: self.now, self.settings)
        self.interactions = InteractionService(self.db, lambda: self.now, self.board, notifications=self.service)
        self.reports = ReportService(self.db, lambda: self.now)
        self.postings = {}
        for owner in ("ama", "joon", "cora"):
            with self.db.transaction() as connection:
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)", (owner, "private-sub-" + owner, owner + "@private.example.test", "Private Google " + owner, storage_time(self.now), storage_time(self.now)))
            draft = self.board.save_draft(owner, {"text": "This fictional project studies gardens. We offer useful tools. We seek careful field testing.", "tags": ["gardens"], "looking_for": "Field testing", "offering": "Useful tools"})
            self.postings[owner] = self.board.publish(owner, draft["revision"], draft["payload_hash"])

    def enable(self, owner, **overrides):
        values = {kind: True for kind in KINDS}
        values.update(overrides)
        return self.service.update_preferences(owner, values)

    def request(self, requester="ama", recipient="joon"):
        preview = self.interactions.prepare_request(requester, self.postings[recipient]["slug"], "Private note that never belongs in email", "Our private reason")
        return self.interactions.request(requester, preview, random_token())

    def conversation(self):
        request = self.request()
        return self.interactions.accept("joon", request["id"], request["context_hash"])["conversation"]

    def rows(self, table="email_outbox"):
        self.assertIn(table, {"email_outbox", "notification_events", "notification_mail_windows", "notification_subscriptions"})
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY rowid")]

    def security(self, owner="ama"):
        with self.db.transaction() as connection:
            return self.service.record_event(connection, owner, "security_first_login")

    def smtp(self):
        # Explicit Python-only fixture: environment validation forbids this
        # staging SMTP configuration, and real transport remains unavailable.
        self.service.settings = replace(self.settings, email_mode="smtp", smtp_from_address="support@fictional.example.test")

    def error(self, status, operation, *args, **kwargs):
        with self.assertRaises(NotificationError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def test_default_preferences_security_only_and_deduped_generic_email(self):
        self.assertEqual(self.service.preferences("ama"), {kind: kind == "security_first_login" for kind in KINDS})
        event = self.security()
        self.assertEqual(self.security(), event)
        self.assertEqual(len(self.rows()), 1)
        payload = json.loads(self.rows()[0]["payload_json"])
        self.assertEqual(self.rows()[0]["state"], "outbox")
        self.assertIsNone(self.rows()[0]["sent_at"])
        self.assertIn("List-Unsubscribe", payload["headers"])
        self.assertEqual(payload["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertNotIn("@private.example.test", json.dumps(payload))
        self.assertNotIn("Private Google", json.dumps(payload))
        self.assertEqual(self.service.unread_count("ama"), 1)

    def test_real_domain_hooks_atomic_deduped_bell_independent_of_email(self):
        preview, nonce = self.interactions.prepare_request("ama", self.postings["joon"]["slug"], "Private note", "Private reason"), random_token()
        intro = self.interactions.request("ama", preview, nonce)
        self.interactions.request("ama", preview, nonce)
        conversation = self.interactions.accept("joon", intro["id"], intro["context_hash"])["conversation"]
        self.interactions.accept("joon", intro["id"], intro["context_hash"])
        nonce = random_token()
        self.interactions.send_message("ama", conversation["id"], "Private message", nonce)
        self.interactions.send_message("ama", conversation["id"], "Private message", nonce)
        self.assertEqual(len(self.rows("notification_events")), 3)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.service.unread_count("ama"), 1)
        self.assertEqual(self.service.unread_count("joon"), 1)
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_notification BEFORE INSERT ON notification_events BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.interactions.send_message("ama", conversation["id"], "Must roll back", random_token())
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_preferences_strict_and_disable_cancels_queue_without_erasing_bell(self):
        self.enable("joon")
        self.request()
        self.assertEqual(len(self.rows()), 1)
        values = self.service.preferences("joon")
        self.error(422, self.service.update_preferences, "joon", {**values, "unknown": True})
        self.error(422, self.service.update_preferences, "joon", {**values, "new_message": 1})
        values["introduction_request"] = False
        self.service.update_preferences("joon", values)
        self.assertEqual(self.rows()[0]["state"], "cancelled")
        self.assertEqual(self.service.unread_count("joon"), 1)
        self.assertEqual(self.service.preferences("ama")["introduction_request"], False)

    def test_messages_coalesce_at_exact_hour_and_do_not_requeue_old_messages(self):
        self.enable("joon")
        conversation = self.conversation()
        self.interactions.send_message("ama", conversation["id"], "Private first message", random_token())
        self.now += timedelta(minutes=30)
        self.interactions.send_message("ama", conversation["id"], "Private second message", random_token())
        self.now += timedelta(minutes=29, seconds=59)
        self.service.run_due()
        self.assertFalse(any(row["kind"] == "new_message" for row in self.rows()))
        self.now += timedelta(seconds=1)
        self.assertEqual(self.service.run_due()["recorded"], 1)
        mail = [row for row in self.rows() if row["kind"] == "new_message"]
        self.assertEqual(len(mail), 1)
        self.assertNotIn("Private first message", mail[0]["payload_json"])
        self.assertEqual(self.service.run_due()["queued"], 0)
        self.interactions.send_message("ama", conversation["id"], "Next batch", random_token())
        self.now += timedelta(hours=1)
        self.service.run_due()
        self.assertEqual(len([row for row in self.rows() if row["kind"] == "new_message"]), 2)

    def test_missed_checkin_explicit_cadence_six_hour_grace_pause_and_dedupe(self):
        self.enable("ama")
        self.assertEqual(self.service.run_due()["queued"], 0)
        self.reports.update_settings("ama", paused=False, cadence_minutes=60)
        self.now += timedelta(hours=7, microseconds=-1)
        self.assertEqual(self.service.run_due()["queued"], 0)
        self.now += timedelta(microseconds=1)
        self.assertEqual(self.service.run_due()["queued"], 1)
        self.assertEqual(self.service.run_due()["queued"], 0)
        self.reports.update_settings("ama", paused=True, cadence_minutes=60)
        self.assertEqual(self.service.unread_count("ama"), 0)
        self.service.run_due()
        self.assertEqual(self.rows()[0]["state"], "cancelled")
        self.reports.update_settings("ama", paused=False, cadence_minutes=60)
        self.now += timedelta(hours=7)
        self.assertEqual(self.service.run_due()["queued"], 1)

    def test_weekly_requires_optin_counts_real_activity_and_excludes_blocked_peers(self):
        self.enable("ama")
        self.now += timedelta(days=6)
        conversation = self.conversation()
        self.interactions.send_message("joon", conversation["id"], "Extremely private message", random_token())
        self.reports.save_paste("ama", {"kind": "nothing", "checked_at": storage_time(self.now), "places_checked": [], "posts_read": 0}, random_token())
        self.now += timedelta(days=1)
        self.service.run_due()
        weekly = [row for row in self.rows() if row["kind"] == "weekly_summary"]
        self.assertEqual(len(weekly), 1)
        text = json.loads(weekly[0]["payload_json"])["text"]
        self.assertIn("1 report received; 1 available introduction update; 1 message received", text)
        self.assertNotIn("Extremely private", text)
        self.assertNotIn("private.example.test", text)
        self.interactions.block_conversation("ama", conversation["id"])
        self.now += timedelta(days=7)
        self.service.run_due()
        latest = [row for row in self.rows() if row["kind"] == "weekly_summary"][-1]
        self.assertIn("0 available introduction updates; 0 messages received", json.loads(latest["payload_json"])["text"])
        self.assertFalse(any(row["user_id"] == "joon" and row["kind"] == "weekly_summary" for row in self.rows()))

    def test_unsubscribe_signature_kind_scope_replay_readonly_lookup_and_delete(self):
        self.enable("ama")
        self.enable("joon")
        token = self.service.unsubscribe_token("ama", "new_message")
        before = self.service.preferences("ama")
        self.assertEqual(self.service.token_info(token)["kind"], "new_message")
        self.assertEqual(self.service.preferences("ama"), before)
        self.error(400, self.service.unsubscribe, token.replace("new_message", "weekly_summary"))
        self.error(400, self.service.unsubscribe, token[:-1] + ("a" if token[-1] != "a" else "b"))
        self.error(400, self.service.unsubscribe, "λ" * 160)
        self.assertEqual(self.service.unsubscribe(token), self.service.unsubscribe(token))
        self.assertFalse(self.service.preferences("ama")["new_message"])
        self.assertTrue(self.service.preferences("ama")["weekly_summary"])
        self.assertTrue(self.service.preferences("joon")["new_message"])
        with self.db.transaction() as connection:
            self.service.erase_owner("ama", connection=connection)
            self.interactions.erase_owner("ama", connection=connection)
            connection.execute("DELETE FROM users WHERE id='ama'")
        self.error(400, self.service.token_info, token)

    def test_owner_bound_read_and_paginated_lists(self):
        conversation = self.conversation()
        for index in range(22):
            self.interactions.send_message("ama", conversation["id"], "Private " + str(index), random_token())
        first = self.service.list_items("joon")
        second = self.service.list_items("joon", page=2)
        self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (22, 20, 2))
        item = first["items"][0]
        self.error(404, self.service.mark_read, "cora", item["id"])
        self.service.mark_read("joon", item["id"])
        self.service.mark_read("joon", item["id"])
        self.assertEqual(self.service.unread_count("joon"), 21)
        self.assertNotIn("Private ", json.dumps(first))

    def test_nav_count_is_readonly_and_filters_revoked_context_without_refresh(self):
        intro = self.request()
        self.assertEqual(self.service.unread_count("joon"), 1)
        self.board.unpublish("ama")
        with patch.object(self.db, "transaction", side_effect=AssertionError("nav must be read-only")):
            self.assertEqual(self.service.unread_count("joon"), 0)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM introductions WHERE id=?", (intro["id"],)).fetchone()[0], "requested")

    def test_outbox_never_invokes_injected_or_real_transport(self):
        self.security()
        transport = FakeTransport()
        with patch("smtplib.SMTP", side_effect=AssertionError("outbox must not connect")):
            self.service.run_due(transport=transport)
        self.assertEqual(transport.messages, [])

    def test_smtp_acceptance_retry_bound_and_stable_message_id(self):
        self.smtp()
        self.security()
        transport = FakeTransport([{"status": "retry", "code": "smtp_temporary"}, {"status": "accepted", "code": "accepted"}])
        self.assertEqual(self.service.run_due(transport=transport)["retried"], 1)
        self.assertEqual(self.rows()[0]["state"], "pending")
        self.assertEqual(self.service.run_due(transport=transport)["accepted"], 0)
        self.now += timedelta(minutes=5)
        self.assertEqual(self.service.run_due(transport=transport)["accepted"], 1)
        self.assertEqual(transport.messages[0]["headers"]["Message-ID"], transport.messages[1]["headers"]["Message-ID"])
        self.assertEqual(self.rows()[0]["state"], "accepted")
        self.assertIsNotNone(self.rows()[0]["sent_at"])
        self.security("cora")
        retry = FakeTransport([{"status": "retry", "code": "smtp_temporary"}])
        for _ in range(5):
            self.service.run_due(transport=retry)
            self.now += timedelta(hours=1)
        failed = [row for row in self.rows() if row["user_id"] == "cora"][0]
        self.assertEqual((failed["state"], failed["attempts"], failed["last_error_code"]), ("failed", 5, "retry_exhausted"))

    def test_uncertain_and_expired_leases_never_automatically_resend(self):
        self.smtp()
        self.security()
        transport = FakeTransport([{"status": "uncertain", "code": "acceptance_unknown"}])
        self.assertEqual(self.service.run_due(transport=transport)["uncertain"], 1)
        self.now += timedelta(days=1)
        self.service.run_due(transport=transport)
        self.assertEqual(len(transport.messages), 1)
        self.security("joon")
        with self.db.transaction() as connection:
            connection.execute("UPDATE email_outbox SET state='sending',lease_id='fictional',lease_until=? WHERE user_id='joon'", (storage_time(self.now + timedelta(minutes=15)),))
        self.service.run_due(transport=transport)
        self.assertEqual(len(transport.messages), 1)
        self.now += timedelta(minutes=15)
        self.assertEqual(self.service.run_due(transport=transport)["uncertain"], 1)
        self.assertEqual(len(transport.messages), 1)

    def test_concurrent_dispatch_claims_only_once(self):
        self.smtp()
        self.security()
        transport = FakeTransport()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.service.run_due(transport=transport), range(2)))
        self.assertEqual(sum(result["accepted"] for result in results), 1)
        self.assertEqual(len(transport.messages), 1)

    def test_block_withdraw_and_suspension_cancel_queued_email_at_use_time(self):
        self.smtp()
        self.enable("joon")
        intro = self.request()
        self.interactions.withdraw("ama", intro["id"])
        transport = FakeTransport()
        self.service.run_due(transport=transport)
        self.assertEqual(transport.messages, [])
        self.assertEqual(self.rows()[0]["state"], "cancelled")
        intro = self.request()
        self.interactions.block_introduction("joon", intro["id"])
        self.service.run_due(transport=transport)
        self.assertEqual(transport.messages, [])
        self.security("cora")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='cora'", (storage_time(self.now),))
        self.service.run_due(transport=transport)
        self.assertEqual(transport.messages, [])

    def test_delete_scrubs_peer_events_outbox_and_capabilities_atomically(self):
        self.enable("ama")
        self.enable("joon")
        conversation = self.conversation()
        self.interactions.send_message("ama", conversation["id"], "Private message", random_token())
        self.now += timedelta(hours=1)
        self.service.run_due()
        token = self.service.unsubscribe_token("ama", "new_message")
        original = self.service.export_owner("joon")
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.db.transaction() as connection:
                self.service.erase_owner("ama", connection=connection)
                self.interactions.erase_owner("ama", connection=connection)
                connection.execute("DELETE FROM users WHERE id='ama'")
                self.assertEqual(self.service.export_owner("joon", connection=connection)["items"], [])
                raise RuntimeError("rollback")
        self.assertEqual(self.service.export_owner("joon"), original)
        with self.db.transaction() as connection:
            self.service.erase_owner("ama", connection=connection)
            self.interactions.erase_owner("ama", connection=connection)
            connection.execute("DELETE FROM users WHERE id='ama'")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows("notification_events"), [])
        self.error(400, self.service.token_info, token)

    def test_export_contains_metadata_no_tokens_addresses_or_private_content(self):
        self.security()
        exported = self.service.export_owner("ama")
        self.assertEqual(set(exported), {"preferences", "items", "email_outbox"})
        for forbidden in ("payload_json", "List-Unsubscribe", "private.example.test", "subscription", "lease_id"):
            self.assertNotIn(forbidden, json.dumps(exported))

    def test_historical_outbox_rows_remain_recorded_after_migration(self):
        path = Path(self.temp.name) / "legacy.db"
        old = Database(path, lambda: self.now, migrations=load_migrations()[:6])
        old.migrate()
        with old.transaction() as connection:
            connection.execute("INSERT INTO email_outbox(id,kind,created_at,payload_json) VALUES('historical','security_first_login',?,'{}')", (storage_time(self.now),))
        upgraded = Database(path, lambda: self.now)
        upgraded.migrate()
        NotificationService(upgraded, lambda: self.now, self.settings).run_due()
        with upgraded.connect() as connection:
            self.assertEqual(connection.execute("SELECT state FROM email_outbox").fetchone()[0], "recorded")

    def test_disabled_signup_skips_optional_jobs_and_production_rejects_injection(self):
        self.enable("ama")
        self.reports.update_settings("ama", paused=False, cadence_minutes=60)
        self.now += timedelta(days=8)
        self.service.settings = replace(self.settings, account_signin_enabled=False)
        self.assertEqual(self.service.run_due()["queued"], 0)
        self.service.settings = replace(self.settings, app_env="production")
        with self.assertRaisesRegex(ValueError, "forbidden"):
            self.service.run_due(transport=FakeTransport())

    def test_cli_refuses_missing_database_without_creating_it(self):
        missing = Path(self.temp.name) / "must-not-exist.db"
        with patch("connectthebots_server.config.Settings.from_env", return_value=replace(self.settings, database_path=missing)):
            with patch("sys.stderr") as stderr:
                self.assertEqual(main(["tick", "--limit", "20"]), 2)
        self.assertFalse(missing.exists())

    def test_weekly_bell_and_email_share_fixed_period_with_upper_bound(self):
        self.enable("ama")
        self.now += timedelta(days=6)
        self.reports.save_paste("ama", {"kind": "nothing", "checked_at": storage_time(self.now), "places_checked": [], "posts_read": 0}, random_token())
        self.now += timedelta(days=1)
        self.service.run_due()
        weekly = next(item for item in self.service.list_items("ama")["items"] if item["kind"] == "weekly_summary")
        email = next(row for row in self.rows() if row["kind"] == "weekly_summary")
        self.assertIn(weekly["summary"], json.loads(email["payload_json"])["text"])
        self.now += timedelta(days=2)
        self.reports.save_paste("ama", {"kind": "nothing", "checked_at": storage_time(self.now), "places_checked": [], "posts_read": 0}, random_token())
        later = next(item for item in self.service.list_items("ama")["items"] if item["id"] == weekly["id"])
        self.assertEqual(later["summary"], weekly["summary"])
        self.assertIn("1 report received", later["summary"])

    def test_email_change_between_claim_and_final_check_uses_current_address(self):
        self.smtp()
        self.security()
        original = self.db.transaction
        @contextmanager
        def with_change_after_claim():
            with original() as connection:
                yield connection
            with self.db.connect() as connection:
                connection.execute("UPDATE users SET email='current@fictional.example.test' WHERE id='ama' AND EXISTS (SELECT 1 FROM email_outbox WHERE user_id='ama' AND state='sending')")
        transport = FakeTransport()
        with patch.object(self.db, "transaction", with_change_after_claim):
            self.service.run_due(transport=transport)
        self.assertEqual(transport.messages[0]["recipient"], "current@fictional.example.test")

    def test_shared_email_does_not_merge_unsubscribe_authority(self):
        self.enable("ama")
        self.enable("joon")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET email=(SELECT email FROM users WHERE id='ama') WHERE id='joon'")
        token = self.service.unsubscribe_token("ama", "weekly_summary")
        self.service.unsubscribe(token)
        self.assertFalse(self.service.preferences("ama")["weekly_summary"])
        self.assertTrue(self.service.preferences("joon")["weekly_summary"])

    def test_readonly_count_and_weekly_summary_require_exact_unrevoked_board_grant(self):
        intro = self.request()
        with self.db.transaction() as connection:
            event_id = self.service.record_event(connection, "joon", "weekly_summary", event_key="fictional-summary")
            event = connection.execute("SELECT * FROM notification_events WHERE id=?", (event_id,)).fetchone()
            original = connection.execute("SELECT payload_json FROM board_revisions WHERE user_id='ama' AND revision=1").fetchone()[0]
        self.assertEqual(self.service.unread_count("joon"), 2)
        for mutation in ("revoked", "payload"):
            with self.subTest(mutation=mutation):
                with self.db.transaction() as connection:
                    if mutation == "revoked":
                        connection.execute("UPDATE board_publication_grants SET revoked_at=? WHERE user_id='ama'", (storage_time(self.now),))
                    else:
                        connection.execute("UPDATE board_revisions SET payload_json='{}' WHERE user_id='ama' AND revision=1")
                with patch.object(self.db, "transaction", side_effect=AssertionError("must remain read-only")):
                    self.assertEqual(self.service.unread_count("joon"), 1)
                    with self.db.connect() as connection:
                        connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE} else sqlite3.SQLITE_OK)
                        self.assertIn("0 available introduction updates", self.service._summary(connection, event))
                        self.assertEqual(connection.execute("SELECT status FROM introductions WHERE id=?", (intro["id"],)).fetchone()[0], "requested")
                with self.db.transaction() as connection:
                    connection.execute("UPDATE board_publication_grants SET revoked_at=NULL WHERE user_id='ama'")
                    connection.execute("UPDATE board_revisions SET payload_json=? WHERE user_id='ama' AND revision=1", (original,))

    def test_weekly_bell_default_off_email_still_records_without_outbox(self):
        self.security()
        self.now += timedelta(days=7)
        self.service.run_due()
        weekly = [item for item in self.service.list_items("ama")["items"] if item["kind"] == "weekly_summary"]
        self.assertEqual(len(weekly), 1)
        self.assertIsNotNone(weekly[0]["summary"])
        self.assertFalse(any(row["kind"] == "weekly_summary" for row in self.rows()))
        self.service.run_due()
        self.assertEqual(len([row for row in self.rows("notification_events") if row["user_id"] == "ama" and row["kind"] == "weekly_summary"]), 1)

    def test_weekly_email_toggle_and_unsubscribe_do_not_reschedule_bell(self):
        self.security()
        origin = self.now
        self.now += timedelta(days=2)
        self.enable("ama")
        self.now += timedelta(days=2)
        prefs = self.service.preferences("ama")
        prefs["weekly_summary"] = False
        self.service.update_preferences("ama", prefs)
        self.now += timedelta(days=1)
        self.service.unsubscribe(self.service.unsubscribe_token("ama", "weekly_summary"))
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT weekly_next_at FROM notification_preferences WHERE user_id='ama'").fetchone()[0], storage_time(origin + timedelta(days=7)))
        self.now = origin + timedelta(days=7)
        self.service.run_due()
        self.assertFalse(any(row["kind"] == "weekly_summary" for row in self.rows()))
        self.now += timedelta(days=1)
        self.enable("ama")
        self.service.run_due()
        self.assertFalse(any(row["kind"] == "weekly_summary" for row in self.rows()))
        self.now = origin + timedelta(days=14)
        self.service.run_due()
        self.assertEqual(len([row for row in self.rows() if row["kind"] == "weekly_summary"]), 1)

    def test_legacy_preferences_start_fresh_week_without_catchup(self):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO notification_preferences(user_id) VALUES('ama')")
        self.now += timedelta(days=100)
        self.service.run_due()
        self.assertEqual(self.rows("notification_events"), [])
        with self.db.connect() as connection:
            due = connection.execute("SELECT weekly_next_at FROM notification_preferences WHERE user_id='ama'").fetchone()[0]
        self.assertEqual(due, storage_time(self.now + timedelta(days=7)))


if __name__ == "__main__":
    unittest.main()
