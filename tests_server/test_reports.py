"""Durable report authority, concurrency, retry and quota behavior."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from connectthebots_server.db import Database
from connectthebots_server.report_keys import key_hash
from connectthebots_server.report_schema import ReportError, parse_paste
from connectthebots_server.report_store import ReportService
from connectthebots_server.sessions import storage_time
from tests_server.test_report_schema import PASTE_CONNECTION, sample


class ReportStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        self.db = Database(Path(self.temp.name) / "reports.db", lambda: self.now)
        self.db.migrate()
        self.service = ReportService(self.db, lambda: self.now)
        with self.db.transaction() as connection:
            for owner in ("ama", "joon"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (owner, "fictional-" + owner, owner + "@example.test", owner.title(), storage_time(self.now), storage_time(self.now)))
        self.key = self.service.create_key("ama", "Fictional research assistant")
        self.authorization = "Bearer " + self.key["key"]
        self.body = json.dumps(sample()).encode()

    def status(self, expected, operation, *args, **kwargs):
        with self.assertRaises(ReportError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, expected)
        return caught.exception

    def count(self, table):
        with self.db.connect() as connection:
            # Only test-owned constants, never request data, are used as tables.
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def send(self, nonce="one", body=None, authorization=None):
        return self.service.ingest(self.authorization if authorization is None else authorization, self.body if body is None else body, nonce)

    def test_key_format_one_time_secret_and_hashed_database_storage(self):
        self.assertRegex(self.key["key"], r"^ctb_[A-Z2-7]{52}$")
        self.assertEqual(self.key["last_four"], self.key["key"][-4:])
        with self.db.connect() as connection:
            row = dict(connection.execute("SELECT * FROM report_keys WHERE id = ?", (self.key["id"],)).fetchone())
            dump = "\n".join(connection.iterdump())
        self.assertEqual(row["key_hash"], key_hash(self.key["key"]))
        self.assertNotIn(self.key["key"], dump)
        metadata = self.service.list_keys("ama")[0]
        self.assertNotIn("key", metadata)
        self.assertNotIn("key_hash", metadata)

    def test_three_active_keys_limit_and_revocation_frees_a_slot(self):
        self.service.create_key("ama", "Second")
        self.service.create_key("ama", "Third")
        self.status(409, self.service.create_key, "ama", "Fourth")
        self.service.revoke_key("ama", self.key["id"])
        self.service.create_key("ama", "Replacement")
        self.assertEqual(sum(key["revoked_at"] is None for key in self.service.list_keys("ama")), 3)
        self.service.create_key("joon", "Separate owner allowance")

    def test_concurrent_key_creation_cannot_exceed_limit(self):
        def create(index):
            try:
                return self.service.create_key("ama", "Worker " + str(index))
            except ReportError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(create, range(5)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 2)
        self.assertEqual(results.count(409), 3)

    def test_key_operations_are_owner_bound_and_sql_injection_is_inert(self):
        other = self.service.create_key("joon", "Other assistant")
        self.status(404, self.service.revoke_key, "ama", other["id"])
        self.status(404, self.service.rename_key, "ama", other["id"], "Hijacked")
        self.status(404, self.service.revoke_key, "ama", "' OR 1=1 --")
        malicious_name = "'; DROP TABLE reports; --"
        self.assertEqual(self.service.rename_key("ama", self.key["id"], malicious_name)["name"], malicious_name)
        self.assertEqual(self.count("users"), 2)
        self.assertEqual(len(self.service.list_keys("ama")), 1)
        self.assertEqual(self.service.authenticate("Bearer " + other["key"])["user_id"], "joon")

    def test_missing_malformed_unknown_and_cookie_style_auth_are_refused(self):
        for value in (None, "", self.key["key"], "Basic " + self.key["key"], "Bearer " + self.key["key"] + " ", "Bearer ctb_" + "A" * 52, "__Host-ctb_session=fictional"):
            with self.subTest(value=bool(value)):
                self.status(401, self.service.ingest, value, self.body, "one")
        self.assertEqual(self.count("reports"), 0)

    def test_each_kind_has_atomic_receipt_and_key_last_used_metadata(self):
        for kind in ("nothing", "question", "connection"):
            receipt = self.send(kind, json.dumps(sample(kind)).encode())
            self.assertEqual(receipt["status"], "received")
            self.assertEqual(receipt["received_at"], storage_time(self.now))
        self.assertEqual(self.count("reports"), 3)
        self.assertEqual(self.count("report_receipts"), 3)
        self.assertEqual(self.service.list_keys("ama")[0]["last_used_at"], storage_time(self.now))
        self.assertEqual([row["payload"]["kind"] for row in self.service.list_reports("ama")], ["connection", "question", "nothing"])
        self.assertEqual(self.service.list_reports("joon"), [])

    def test_exact_replay_survives_restart_and_distinct_bytes_conflict(self):
        first = self.send()
        self.now += timedelta(seconds=30)
        restarted = ReportService(Database(self.db.path, lambda: self.now), lambda: self.now)
        self.assertEqual(restarted.ingest(self.authorization, self.body, "one"), first)
        self.assertEqual(self.service.list_keys("ama")[0]["last_used_at"], storage_time(self.now))
        self.status(409, restarted.ingest, self.authorization, self.body + b" ", "one")
        self.assertEqual(self.count("reports"), 1)
        self.assertEqual(self.count("report_receipts"), 1)
        with self.db.connect() as connection:
            stored = connection.execute("SELECT * FROM report_receipts").fetchone()
            self.assertEqual(stored["body_hash"], sha256(self.body).hexdigest())
            self.assertEqual(stored["idempotency_hash"], sha256(b"one").hexdigest())

    def test_successful_key_check_updates_last_use_but_revoked_and_failed_uses_do_not(self):
        self.now += timedelta(hours=1)
        self.assertEqual(self.service.authenticate(self.authorization), {"user_id": "ama", "key_id": self.key["id"]})
        used_at = storage_time(self.now)
        self.assertEqual(self.service.list_keys("ama")[0]["last_used_at"], used_at)
        self.now += timedelta(hours=1)
        self.status(422, self.service.ingest, self.authorization, b"{}", "invalid")
        self.assertEqual(self.service.list_keys("ama")[0]["last_used_at"], used_at)
        self.service.revoke_key("ama", self.key["id"])
        self.status(401, self.service.authenticate, self.authorization)
        self.status(401, self.send)
        self.assertEqual(self.service.list_keys("ama")[0]["last_used_at"], used_at)

    def test_same_idempotency_value_is_scoped_to_the_key(self):
        other = self.service.create_key("ama", "Second assistant")
        first = self.send()
        second = self.send(authorization="Bearer " + other["key"])
        self.assertNotEqual(first["report_id"], second["report_id"])
        self.assertEqual(self.count("reports"), 2)

    def test_concurrent_identical_requests_commit_one_report_and_receipt(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.send(), range(6)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.count("reports"), 1)
        self.assertEqual(self.count("report_receipts"), 1)

    def test_receipt_failure_rolls_back_report_key_usage_and_schedule(self):
        self.service.update_settings("ama", paused=False, cadence_minutes=1440)
        initial = self.service.settings("ama")
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON report_receipts BEGIN SELECT RAISE(ABORT, 'fictional receipt failure'); END")
        self.now += timedelta(minutes=20)
        with self.assertRaises(sqlite3.IntegrityError):
            self.send()
        self.assertEqual(self.count("reports"), 0)
        self.assertEqual(self.count("report_receipts"), 0)
        self.assertIsNone(self.service.list_keys("ama")[0]["last_used_at"])
        self.assertEqual(self.service.settings("ama"), initial)

    def test_revoked_suspended_and_deleted_authority_cannot_replay(self):
        self.send()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at = ? WHERE id = ?", (storage_time(self.now), "ama"))
        self.status(401, self.send)
        self.status(403, self.service.save_paste, "ama", sample(), "paste-one")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at = NULL WHERE id = ?", ("ama",))
        self.service.revoke_key("ama", self.key["id"])
        self.status(401, self.send)
        self.assertEqual(self.count("reports"), 1)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id = ?", ("ama",))
        self.status(401, self.send)
        for table in ("report_keys", "reports", "report_receipts"):
            self.assertEqual(self.count(table), 0)

    def test_invalid_payload_nonce_and_overlarge_body_never_write(self):
        for nonce in (None, "", "contains space", "x" * 129, "\u03bb"):
            self.status(400, self.service.ingest, self.authorization, self.body, nonce)
        for body, status in ((b"{}", 422), (self.body.replace(b'"posts_read": 4', b'"posts_read": true'), 422), (b" " * 32769, 413)):
            self.status(status, self.service.ingest, self.authorization, body, "bad")
        self.assertEqual(self.count("reports"), 0)
        self.assertEqual(self.count("report_receipts"), 0)

    def test_minute_quota_is_durable_exact_boundary_and_replay_does_not_count(self):
        first = self.send("n0")
        for number in range(1, 10):
            self.send("n" + str(number))
        self.assertEqual(self.send("n0"), first)
        restarted = ReportService(self.db, lambda: self.now)
        failure = self.status(429, restarted.ingest, self.authorization, self.body, "n10")
        self.assertEqual(failure.retry_after, 60)
        self.now += timedelta(seconds=59, microseconds=1)
        self.assertEqual(self.status(429, self.send, "n10").retry_after, 1)
        self.now += timedelta(microseconds=999999)
        self.send("n10")
        self.assertEqual(self.count("reports"), 11)

    def test_sixty_report_day_is_rolling_not_calendar_reset(self):
        origin = self.now
        for batch in range(6):
            self.now = origin + timedelta(minutes=batch)
            for number in range(10):
                self.send(f"day-{batch}-{number}")
        self.now = origin + timedelta(hours=12, seconds=1)  # Next UTC date.
        error = self.status(429, self.send, "day-extra")
        self.assertEqual(error.retry_after, 12 * 3600 - 1)
        self.now = origin + timedelta(days=1)
        self.send("day-extra")
        self.assertEqual(self.count("reports"), 61)

    def test_concurrent_requests_cannot_exceed_minute_quota(self):
        def attempt(number):
            try:
                return self.send("parallel-" + str(number))
            except ReportError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(results.count(429), 2)
        self.assertEqual(self.count("reports"), 10)

    def test_paste_preview_has_no_storage_and_confirmation_is_idempotent(self):
        payload = parse_paste(PASTE_CONNECTION)
        self.assertEqual(self.count("reports"), 0)
        first = self.service.save_paste("ama", payload, "preview-one")
        self.assertEqual(self.service.save_paste("ama", payload, "preview-one"), first)
        self.status(409, self.service.save_paste, "ama", sample(), "preview-one")
        other = self.service.save_paste("joon", payload, "preview-one")
        self.assertNotEqual(first["report_id"], other["report_id"])
        self.assertEqual(self.service.list_reports("ama")[0]["source"], "paste")
        self.assertIsNone(self.service.list_reports("ama")[0]["key_id"])

    def test_paste_limits_are_per_owner_and_do_not_consume_key_allowance(self):
        for number in range(10):
            self.service.save_paste("ama", sample(), "paste-" + str(number))
        self.status(429, self.service.save_paste, "ama", sample(), "paste-next")
        self.service.save_paste("joon", sample(), "paste-next")
        self.send()
        self.assertEqual(self.count("reports"), 12)

    def test_paused_intake_blocks_new_writes_but_preserves_authenticated_receipts(self):
        original = self.send()
        self.service.update_settings("ama", paused=True, cadence_minutes=None)
        self.assertEqual(self.send(), original)
        self.status(409, self.send, "another")
        self.status(409, self.service.save_paste, "ama", sample(), "paste")
        self.service.update_settings("ama", paused=False, cadence_minutes=None)
        self.send("another")

    def test_expected_checkin_exists_only_after_explicit_choice_and_uses_receipt_time(self):
        self.assertEqual(self.service.settings("ama"), {"paused": False, "cadence_minutes": None, "next_due_at": None})
        self.send()
        self.assertIsNone(self.service.settings("ama")["next_due_at"])
        self.service.update_settings("ama", paused=False, cadence_minutes=1440)
        due = storage_time(self.now + timedelta(days=1))
        self.assertEqual(self.service.settings("ama")["next_due_at"], due)
        self.now += timedelta(hours=2)
        self.service.update_settings("ama", paused=False, cadence_minutes=1440)
        self.assertEqual(self.service.settings("ama")["next_due_at"], due)
        payload = sample()
        payload["checked_at"] = "2020-01-01T00:00:00Z"
        self.send("next", json.dumps(payload).encode())
        self.assertEqual(self.service.settings("ama")["next_due_at"], storage_time(self.now + timedelta(days=1)))
        self.assertIsNone(self.service.settings("joon")["next_due_at"])
        self.service.update_settings("ama", paused=False, cadence_minutes=None)
        self.assertIsNone(self.service.settings("ama")["next_due_at"])

    def test_pending_rows_survive_history_pagination_and_handled_is_owner_bound(self):
        question = self.send("question", json.dumps(sample("question")).encode())
        self.now += timedelta(minutes=1)
        self.send("latest")
        self.assertEqual(self.service.list_reports("ama", limit=1)[0]["payload"]["kind"], "nothing")
        pending = self.service.pending_reports("ama", limit=1)
        self.assertEqual(pending["total"], 1)
        self.assertEqual(pending["reports"][0]["id"], question["report_id"])
        self.status(404, self.service.mark_handled, "joon", question["report_id"])
        handled = self.service.mark_handled("ama", question["report_id"])
        self.now += timedelta(hours=1)
        self.assertEqual(self.service.mark_handled("ama", question["report_id"])["handled_at"], handled["handled_at"])
        self.assertEqual(self.service.pending_reports("ama")["total"], 0)


if __name__ == "__main__":
    unittest.main()
