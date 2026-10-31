"""Real operator/moderation transactions, authority, replay and CLI contracts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from connectthebots_server.admin import main
from connectthebots_server.board import BoardError, BoardService
from connectthebots_server.config import Settings
from connectthebots_server.db import Database
from connectthebots_server.interactions import InteractionError, InteractionService
from connectthebots_server.moderation import ModerationError, ModerationService
from connectthebots_server.notifications import NotificationService
from connectthebots_server.operator import OperatorError, OperatorService
from connectthebots_server.operator_alerts import OperatorAlerts
from connectthebots_server.report_store import ReportService
from connectthebots_server.sessions import Sessions, random_token, storage_time, token_hash


class OperatorFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.clock = lambda: self.now
        self.settings = Settings.from_env({"APP_ENV": "staging", "PUBLIC_BASE_URL": "https://fictional.example.test", "DATABASE_PATH": str(Path(self.temp.name) / "operator.db"), "SESSION_SECRET": "fictional-operator-secret-" + "x" * 32, "ACCOUNT_SIGNIN_ENABLED": "true", "GOOGLE_CLIENT_ID": "fictional", "GOOGLE_CLIENT_SECRET": "fictional"})
        self.settings = replace(self.settings, operator_email="operator@fictional.example.test")
        self.db = Database(self.settings.database_path, self.clock)
        self.db.migrate()
        self.board = BoardService(self.db, self.clock)
        self.alerts = OperatorAlerts(self.db, self.clock, self.settings)
        self.notifications = NotificationService(self.db, self.clock, self.settings, operator_alerts=self.alerts)
        self.interactions = InteractionService(self.db, self.clock, self.board, notifications=self.notifications)
        self.moderation = ModerationService(self.db, self.clock, self.board, self.interactions, self.alerts)
        self.operator = OperatorService(self.db, self.clock, self.board, self.interactions, self.notifications, self.moderation)
        self.reports = ReportService(self.db, self.clock)
        self.postings = {}
        for owner in ("one", "two", "three"):
            with self.db.transaction() as connection:
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)", (owner, "private-sub-" + owner, owner + "@private.example.test", "Private Google " + owner, storage_time(self.now), storage_time(self.now)))
            draft = self.board.save_draft(owner, {"text": "This fictional project studies gardens. We offer useful tools. We seek careful field testing.", "tags": ["gardens"], "looking_for": "Field testing", "offering": "Useful tools"})
            self.postings[owner] = self.board.publish(owner, draft["revision"], draft["payload_hash"])

    def request(self, sender="one", recipient="two"):
        preview = self.interactions.prepare_request(sender, self.postings[recipient]["slug"], "Private introduction note", "Private introduction reason")
        return self.interactions.request(sender, preview, random_token())

    def conversation(self):
        intro = self.request()
        return self.interactions.accept("two", intro["id"], intro["context_hash"])["conversation"]

    def submit(self, owner="one", peer="two", *, nonce=None, details="Fictional private report detail"):
        target = self.moderation.target(owner, "posting", self.postings[peer]["slug"])
        return self.moderation.submit(owner, target["kind"], target["id"], "spam", details, nonce or random_token(), target["context_hash"])

    def rows(self, table):
        self.assertIn(table, {"abuse_reports", "moderation_receipts", "operator_alerts", "operator_subscriptions", "email_outbox", "invites", "notification_events"})
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY rowid")]

    def error(self, status, operation, *args, **kwargs):
        with self.assertRaises((ModerationError, OperatorError, BoardError, InteractionError)) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)
        return caught.exception


class ModerationTests(OperatorFixture):
    def test_report_targets_no_profile_and_read_is_readonly(self):
        with patch.object(self.db, "transaction", side_effect=AssertionError("GET must not write")):
            target = self.moderation.target("one", "posting", self.postings["two"]["slug"])
        self.assertEqual(set(target), {"kind", "id", "label", "url", "context_hash"})
        for secret in ("private-sub", "Private Google", "private.example.test"):
            self.assertNotIn(secret, json.dumps(target))
        for target_id in (self.postings["one"]["slug"], "' OR 1=1 --", "missing"):
            self.error(404, self.moderation.target, "one", "posting", target_id)
        self.error(401, self.moderation.target, "unknown", "posting", self.postings["two"]["slug"])
        self.interactions.block_posting("one", self.postings["two"]["slug"])
        self.error(404, self.moderation.target, "one", "posting", self.postings["two"]["slug"])

    def test_closed_blocked_conversation_can_be_reported_by_participant_only(self):
        conversation = self.conversation()
        self.interactions.block_conversation("one", conversation["id"])
        self.operator.user_suspend("two")
        target = self.moderation.target("one", "conversation", conversation["id"])
        receipt = self.moderation.submit("one", "conversation", conversation["id"], "harassment", "Private details", random_token(), target["context_hash"])
        self.assertTrue(receipt["id"])
        self.error(404, self.moderation.target, "three", "conversation", conversation["id"])
        with self.db.transaction() as connection:
            self.notifications.erase_owner("two", connection=connection)
            self.interactions.erase_owner("two", connection=connection)
            connection.execute("DELETE FROM users WHERE id='two'")
        self.error(404, self.moderation.target, "one", "conversation", conversation["id"])
        self.assertEqual(self.rows("abuse_reports"), [])

    def test_report_atomic_idempotent_restart_and_conflicting_replay(self):
        nonce = random_token()
        first = self.submit(nonce=nonce)
        self.moderation = ModerationService(Database(self.db.path, self.clock), self.clock, self.board, self.interactions, self.alerts)
        replay = self.submit(nonce=nonce)
        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["replayed"])
        self.error(409, self.submit, nonce=nonce, details="Different private detail")
        self.assertEqual([len(self.rows(table)) for table in ("abuse_reports", "moderation_receipts", "operator_alerts", "email_outbox")], [1, 1, 1, 1])
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_alert BEFORE INSERT ON operator_alerts BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit()
        self.assertEqual([len(self.rows(table)) for table in ("abuse_reports", "moderation_receipts", "operator_alerts", "email_outbox")], [1, 1, 1, 1])

    def test_concurrent_same_form_creates_one_report_and_alert(self):
        nonce = random_token()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.submit(nonce=nonce), range(2)))
        self.assertEqual(len({item["id"] for item in results}), 1)
        self.assertEqual(len(self.rows("email_outbox")), 1)

    def test_changed_exact_publication_rejects_old_target_context(self):
        target = self.moderation.target("one", "posting", self.postings["two"]["slug"])
        self.board.unpublish("two")
        draft = self.board.draft("two")
        self.board.publish("two", draft["revision"], draft["payload_hash"])
        self.error(409, self.moderation.submit, "one", "posting", target["id"], "spam", "", random_token(), target["context_hash"])
        self.assertEqual(self.rows("abuse_reports"), [])

    def test_report_limits_strict_input_and_day_boundary(self):
        target = self.moderation.target("one", "posting", self.postings["two"]["slug"])
        for reason, details in (("invented", ""), ("spam", "x" * 2001), ("spam", "bad\x00text"), ("spam", "<script>bad</script>"), ("spam", "score: 98"), ("spam", "90% certain")):
            self.error(422, self.moderation.submit, "one", "posting", target["id"], reason, details, random_token(), target["context_hash"])
        for _ in range(10):
            self.submit()
        failure = self.error(429, self.submit)
        self.assertEqual(failure.retry_after, 86400)
        self.now += timedelta(days=1)
        self.assertFalse(self.submit()["replayed"])

    def test_exports_and_deletion_do_not_retain_other_peoples_reports(self):
        first = self.submit()
        other = self.submit(owner="three", peer="one", details="Second private details")
        mine = self.moderation.export_owner("one")
        self.assertEqual([row["id"] for row in mine], [first["id"]])
        self.assertNotIn(other["id"], json.dumps(mine))
        self.assertEqual(self.moderation.export_owner("two"), [])
        with self.db.transaction() as connection:
            self.moderation.erase_owner("one", connection=connection)
        for table in ("abuse_reports", "moderation_receipts", "operator_alerts", "email_outbox"):
            self.assertEqual(self.rows(table), [])


class OperatorTests(OperatorFixture):
    def test_invite_secret_only_once_expiry_revocation_and_safe_listing(self):
        invite = self.operator.invite_create()
        self.assertEqual(invite["expires_at"], storage_time(self.now + timedelta(days=7)))
        listing = self.operator.invite_list()
        raw = self.rows("invites")[0]
        self.assertEqual(raw["code_hash"], token_hash(invite["code"]))
        self.assertNotIn(invite["code"], json.dumps(raw))
        self.assertNotIn("code_hash", json.dumps(listing))
        self.assertNotIn(invite["code"], json.dumps(listing))
        self.operator.invite_revoke(invite["id"])
        self.assertIsNotNone(self.rows("invites")[0]["revoked_at"])
        self.error(404, self.operator.invite_revoke, "' OR 1=1 --")
        self.error(422, self.operator.invite_create, 0)

    def test_hide_unhide_terminally_withdraws_without_intermediate_read(self):
        intro = self.request()
        slug = self.postings["two"]["slug"]
        with self.db.connect() as connection:
            grant = connection.execute("SELECT active_grant_id FROM board_postings WHERE slug=?", (slug,)).fetchone()[0]
        self.operator.posting_hide(slug)
        self.operator.posting_unhide(slug)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT active_grant_id FROM board_postings WHERE slug=?", (slug,)).fetchone()[0], grant)
            self.assertEqual(connection.execute("SELECT status FROM introductions WHERE id=?", (intro["id"],)).fetchone()[0], "withdrawn")
        self.error(410, self.interactions.accept, "two", intro["id"], intro["context_hash"])
        self.assertEqual(self.board.get_public(slug)["slug"], slug)

    def test_hiding_preserves_accepted_conversation_and_unhide_requires_exact_grant(self):
        conversation = self.conversation()
        slug = self.postings["two"]["slug"]
        self.operator.posting_hide(slug)
        self.assertIsNone(self.interactions.get_conversation("one", conversation["id"])["closed_at"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE board_revisions SET payload_json='{}' WHERE user_id='two'")
        self.error(409, self.operator.posting_unhide, slug)
        self.assertNotIn(slug, json.dumps(self.board.public_list()))

    def test_suspend_unsuspend_does_not_revive_credentials_contact_or_email(self):
        conversation = self.conversation()
        pending = self.request("three", "two")
        key = self.reports.create_key("two", "Fictional key")
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,absolute_expires_at) VALUES('hashed-fictional','two',?,?,?)", (storage_time(self.now), storage_time(self.now + timedelta(days=30)), storage_time(self.now + timedelta(days=90))))
        self.notifications.update_preferences("two", {kind: True for kind in self.notifications.preferences("two")})
        self.operator.user_suspend("two")
        self.error(403, self.board.draft, "two")
        self.operator.user_unsuspend("two")
        self.assertEqual(self.operator.user_show("two")["active_sessions"], 0)
        self.assertEqual(self.operator.user_show("two")["active_keys"], 0)
        self.assertTrue(self.board.owner_posting("two")["hidden"])
        self.assertIsNotNone(self.interactions.get_conversation("two", conversation["id"])["closed_at"])
        self.error(410, self.interactions.accept, "two", pending["id"], pending["context_hash"])
        self.assertFalse(any(self.notifications.preferences("two").values()))

    def test_suspend_rolls_back_all_actions_on_failure(self):
        intro = self.request()
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_suspend BEFORE UPDATE ON introductions BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.operator.user_suspend("two")
        self.assertIsNone(self.operator.user_show("two")["suspended_at"])
        self.assertFalse(self.board.owner_posting("two")["hidden"])
        self.assertEqual(self.interactions.get_introduction("two", intro["id"])["state"], "requested")

    def test_private_posting_can_unhide_without_publication_then_get_fresh_approval(self):
        slug = self.postings["two"]["slug"]
        self.board.unpublish("two")
        self.operator.user_suspend("two")
        self.operator.user_unsuspend("two")
        outcome = self.operator.posting_unhide(slug)
        self.assertFalse(outcome["visible"])
        self.assertFalse(self.board.owner_posting("two")["hidden"])
        self.error(410, self.board.get_public, slug)
        draft = self.board.draft("two")
        self.board.publish("two", draft["revision"], draft["payload_hash"])
        self.assertEqual(self.board.get_public(slug)["slug"], slug)

    def test_recovery_hold_blocks_access_changes_and_old_secret_after_release(self):
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        self.error(503, self.operator.user_unsuspend, "two")
        self.error(503, self.operator.user_suspend, "two")
        self.error(503, self.operator.invite_create)
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=0,restore_secret_hash=? WHERE singleton=1", (token_hash(self.settings.session_secret),))
        self.error(503, self.operator.user_unsuspend, "two")

    def test_cli_machine_output_no_implicit_migration_or_unconfirmed_delete(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("connectthebots_server.config.Settings.from_env", return_value=self.settings), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            self.assertEqual(main(["invite", "create", "--days", "2"]), 0)
            created = json.loads(stdout.getvalue())
            stdout.seek(0)
            stdout.truncate()
            self.assertEqual(main(["invite", "list"]), 0)
            self.assertNotIn(created["code"], stdout.getvalue())
            self.assertEqual(main(["user", "delete", "one", "--confirm", "two"]), 2)
        self.assertIsNotNone(self.operator.user_show("one"))
        missing = replace(self.settings, database_path=Path(self.temp.name) / "missing.db")
        with patch("connectthebots_server.config.Settings.from_env", return_value=missing), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["stats"]), 2)
        self.assertFalse(missing.database_path.exists())

    def test_operator_report_listing_bounded_and_details_explicit(self):
        receipt = self.submit()
        listing = self.operator.reports_list(limit=1)
        self.assertNotIn("details", listing["items"][0])
        self.assertEqual(self.operator.report_show(receipt["id"])["details"], "Fictional private report detail")
        self.assertEqual(self.operator.stats()["abuse_reports"], 1)
        self.error(422, self.operator.reports_list, limit=101)

    def test_cli_confirmed_delete_uses_shared_erasure_and_removes_report_alerts(self):
        self.submit()
        self.submit(owner="three", peer="one")
        stdout = io.StringIO()
        with patch("connectthebots_server.config.Settings.from_env", return_value=self.settings), patch("sys.stdout", stdout):
            self.assertEqual(main(["user", "delete", "one", "--confirm", "one"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"deleted": True})
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM users WHERE id='one'").fetchone())
            self.assertEqual(connection.execute("SELECT erasure_generation FROM recovery_state").fetchone()[0], 1)
            receipt = dict(connection.execute("SELECT * FROM deletion_receipts").fetchone())
            self.assertEqual(set(receipt), {"deleted_on"})
        for table in ("abuse_reports", "moderation_receipts", "operator_alerts", "email_outbox"):
            self.assertEqual(self.rows(table), [])


if __name__ == "__main__":
    unittest.main()
