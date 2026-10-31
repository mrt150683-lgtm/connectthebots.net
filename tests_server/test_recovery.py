"""Deletion-aware restore, quarantine, authenticated files and process fencing."""
import asyncio
import base64
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import importlib.util
from http.cookies import SimpleCookie
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from connectthebots_server.board import BoardService
from connectthebots_server.db import Database
from connectthebots_server.interactions import InteractionService
from connectthebots_server.lifecycle import AccountLifecycle
from connectthebots_server.moderation import ModerationService
from connectthebots_server.notifications import NotificationService
from connectthebots_server.operator import OperatorService
from connectthebots_server.operator_alerts import OperatorAlerts
from connectthebots_server.recovery import RecoveryError, RecoveryService, advance_erasure, recovery_held, runtime_lock
from connectthebots_server.sessions import Sessions, random_token, storage_time, token_hash
from tests_server.test_operator import OperatorFixture

CRYPTO = importlib.util.find_spec("cryptography") is not None
AUTH_EXTRA = all(importlib.util.find_spec(name) is not None for name in ("starlette", "httpx", "uvicorn", "jwt", "jinja2", "cryptography"))


class RecoveryAuthorityTests(OperatorFixture):
    def setUp(self):
        super().setUp()
        self.lifecycle = AccountLifecycle(self.db, self.clock, self.board, self.interactions, self.notifications, self.moderation)
        self.recovery = RecoveryService(self.db, self.clock, self.settings)

    def state(self):
        with self.db.connect() as connection:
            return dict(connection.execute("SELECT * FROM recovery_state").fetchone())

    def raw_snapshot(self):
        """A separate pre-intervention database for platform-independent tests."""
        path = self.db.path.parent / (random_token() + ".db")
        with self.db.connect() as source, closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
        return path

    def released_services(self, path):
        settings = replace(self.settings, database_path=path, signups="open",
                           session_secret="rotated-" + "fictional-secret-" + "r" * 32)
        database = Database(path, self.clock)
        RecoveryService(database, self.clock, settings).release_hold()
        board = BoardService(database, self.clock)
        alerts = OperatorAlerts(database, self.clock, settings)
        notifications = NotificationService(database, self.clock, settings, operator_alerts=alerts)
        interactions = InteractionService(database, self.clock, board, notifications=notifications)
        moderation = ModerationService(database, self.clock, board, interactions, alerts)
        operator = OperatorService(database, self.clock, board, interactions, notifications, moderation)
        return settings, database, board, interactions, operator

    def later_suspended_account(self):
        self.now += timedelta(minutes=1)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                               ("later", "private-sub-later", "later@private.example.test", "Fictional later account", storage_time(self.now), storage_time(self.now)))
            connection.execute("UPDATE users SET email=?,display_name=?,last_login_at=? WHERE id='one'",
                               ("current@private.example.test", "Current fictional profile", storage_time(self.now)))
        self.operator.user_suspend("later")

    def test_restore_preserves_later_suspended_identity_and_current_profile(self):
        snapshot = self.raw_snapshot()
        self.later_suspended_account()
        self.recovery._quarantine(snapshot)
        _, database, _, _, _ = self.released_services(snapshot)
        with database.connect() as connection:
            later = connection.execute("SELECT * FROM users WHERE id='later'").fetchone()
            current = connection.execute("SELECT * FROM users WHERE id='one'").fetchone()
            self.assertEqual(later["google_sub"], "private-sub-later")
            self.assertEqual(later["suspended_at"], storage_time(self.now))
            self.assertEqual(current["email"], "current@private.example.test")
            self.assertEqual(current["display_name"], "Current fictional profile")
            self.assertEqual(current["last_login_at"], storage_time(self.now))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0], 4)

    @unittest.skipUnless(AUTH_EXTRA, "Install the server extra for the restored-account callback regression")
    def test_later_suspended_identity_cannot_signup_after_recovery_release(self):
        from starlette.requests import Request
        from connectthebots_server.app import create_app
        from connectthebots_server.auth import Identity, OAUTH_COOKIE

        class Provider:
            authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"

            async def exchange(self, code, verifier, nonce_hash):
                return Identity("private-sub-later", "later@private.example.test", "Fictional later account")

        def request(path, query=b"", cookie=""):
            return Request({"type": "http", "method": "GET", "scheme": "https", "path": path, "query_string": query,
                            "headers": [(b"host", b"fictional.example.test"), (b"cookie", cookie.encode())],
                            "server": ("fictional.example.test", 443), "client": ("127.0.0.1", 1234)})

        snapshot = self.raw_snapshot()
        self.later_suspended_account()
        self.recovery._quarantine(snapshot)
        settings, database, _, _, _ = self.released_services(snapshot)
        app = create_app(settings, self.clock, google_provider=Provider())
        start = asyncio.run(app.state.auth.start(request("/auth/google/start")))
        self.assertEqual(start.status_code, 303)
        parameters = parse_qs(urlsplit(start.headers["location"]).query)
        cookie = SimpleCookie()
        cookie.load(start.headers["set-cookie"])
        response = asyncio.run(app.state.auth.callback(request("/auth/google/callback",
                            ("state=" + parameters["state"][0] + "&code=fictional-code").encode(),
                            OAUTH_COOKIE + "=" + cookie[OAUTH_COOKIE].value)))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"This account is not available", response.body)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE google_sub='private-sub-later'").fetchone()[0], 1)
            self.assertIsNotNone(connection.execute("SELECT suspended_at FROM users WHERE id='later'").fetchone()[0])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_later_block_survives_both_accounts_reviewed_and_fresh_publication(self):
        snapshot = self.raw_snapshot()
        self.interactions.block_posting("one", self.postings["two"]["slug"])
        self.recovery._quarantine(snapshot)
        _, database, board, interactions, operator = self.released_services(snapshot)
        for owner in ("one", "two"):
            operator.user_unsuspend(owner)
            operator.posting_unhide(self.postings[owner]["slug"])
            draft = board.draft(owner)
            board.publish(owner, draft["revision"], draft["payload_hash"])
        with database.connect() as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT blocker_id,blocked_id FROM user_blocks")], [("one", "two")])
        # Public visibility is restored, but neither participant regains contact.
        self.assertEqual(board.public_list()["total"], 2)
        for owner, peer in (("one", "two"), ("two", "one")):
            self.error(404, interactions.prepare_request, owner, self.postings[peer]["slug"], "Fictional note", "Fictional reason")
            self.assertNotIn(self.postings[peer]["slug"], [item["slug"] for item in board.public_list(viewer_id=owner)["items"]])

    def test_snapshot_identity_missing_or_conflicting_in_live_authority_refused_atomically(self):
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                snapshot = self.raw_snapshot()
                database = Database(snapshot, self.clock)
                with database.transaction() as connection:
                    if conflict:
                        connection.execute("UPDATE users SET google_sub='conflicting-sub' WHERE id='one'")
                    else:
                        connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                           ("absent", "absent-sub", "absent@example.test", "Fictional absent account", storage_time(self.now), storage_time(self.now)))
                with self.assertRaisesRegex(RecoveryError, "identity is absent or conflicts"):
                    self.recovery._quarantine(snapshot)
                with database.connect() as connection:
                    self.assertEqual(connection.execute("SELECT recovery_hold FROM recovery_state").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM board_publication_grants WHERE revoked_at IS NULL").fetchone()[0], 3)

    def test_account_erasure_invalidates_older_backup_authority_atomically(self):
        conversation = self.conversation()
        self.interactions.send_message("one", conversation["id"], "Private fictional message", random_token())
        self.submit()
        before = self.state()
        self.assertTrue(self.lifecycle.delete_account("one"))
        after = self.state()
        self.assertEqual(after["lineage_id"], before["lineage_id"])
        self.assertEqual(after["erasure_generation"], before["erasure_generation"] + 1)
        self.assertEqual(set(after), {"singleton", "lineage_id", "erasure_generation", "recovery_hold", "restore_secret_hash"})
        self.assertFalse(self.lifecycle.delete_account("one"))
        self.assertEqual(self.state(), after)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM abuse_reports").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM operator_alerts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT body FROM messages").fetchone()[0], "[deleted]")
            self.assertEqual(connection.execute("SELECT deleted_on FROM deletion_receipts").fetchone()[0], "2026-10-30")

    def test_failed_watermark_rolls_back_every_part_of_erasure(self):
        self.submit()
        with self.db.transaction() as connection:
            connection.execute("CREATE TRIGGER fail_erasure BEFORE UPDATE ON recovery_state BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.lifecycle.delete_account("one")
        with self.db.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT id FROM users WHERE id='one'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM abuse_reports").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM deletion_receipts").fetchone()[0], 0)

    def test_old_process_secret_stays_held_after_rotation_and_release(self):
        from hashlib import sha256
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1,restore_secret_hash=?", (sha256(self.settings.session_secret.encode()).hexdigest(),))
        with self.assertRaisesRegex(RecoveryError, "Rotate SESSION_SECRET"):
            self.recovery.release_hold()
        rotated = replace(self.settings, session_secret="rotated-fictional-secret-" + "y" * 32)
        RecoveryService(self.db, self.clock, rotated).release_hold()
        self.assertTrue(recovery_held(self.db, self.settings))
        self.assertFalse(recovery_held(self.db, rotated))
        self.assertEqual(self.notifications.run_due()["accepted"], 0)

    def test_missing_authority_fails_closed(self):
        # Ordinary mutations cannot delete their own authority even when
        # offsite is disabled. Inject corruption outside that guard to verify
        # the independent read/recovery refusal as well.
        with self.assertRaises(RecoveryError), self.db.transaction() as connection:
            connection.execute("DELETE FROM recovery_state")
        with self.db.transaction(_guard_recovery=False) as connection:
            connection.execute("DELETE FROM recovery_state")
        self.assertTrue(recovery_held(self.db, self.settings))
        with self.assertRaises(RecoveryError), self.db.transaction() as connection:
            advance_erasure(connection)


@unittest.skipUnless(os.name == "posix" and CRYPTO, "Run on Ubuntu with the server extra for backup file permissions and crypto")
class EncryptedRecoveryTests(RecoveryAuthorityTests):
    def setUp(self):
        super().setUp()
        self.settings = replace(self.settings, backup_encryption_key=base64.urlsafe_b64encode(b"f" * 32).decode())
        self.recovery = RecoveryService(self.db, self.clock, self.settings)

    def snapshot(self):
        return Path(self.recovery.backup()["path"])

    def test_backup_encrypts_private_content_and_restore_quarantines_authority(self):
        conversation = self.conversation()
        self.interactions.send_message("one", conversation["id"], "Fictional private recovery marker", random_token())
        self.request("three", "two")
        self.submit()
        self.reports.create_key("one", "Fictional assistant")
        with self.db.transaction() as connection:
            Sessions(self.db, self.clock, self.settings.session_secret).create(connection, "one")
        self.operator.invite_create()
        backup = self.snapshot()
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(b"Fictional private recovery marker", backup.read_bytes())
        self.assertNotIn(b"SQLite format", backup.read_bytes())
        # A later block and draft change must not be undone into active contact.
        self.interactions.block_conversation("one", conversation["id"])
        result = self.recovery.restore(backup)
        self.assertTrue(result["restored"])
        self.assertTrue(Path(result["checkpoint"]).exists())
        self.assertTrue(recovery_held(self.db, self.settings))
        self.assertEqual(self.board.public_list()["total"], 0)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_keys WHERE revoked_at IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invites WHERE revoked_at IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM board_publication_grants WHERE revoked_at IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversations WHERE closed_at IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM introductions WHERE status='requested'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM email_outbox WHERE state IN ('pending','sending','outbox')").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_settings WHERE paused=1").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT body FROM messages").fetchone()[0], "Fictional private recovery marker")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        rotated = replace(self.settings, session_secret="rotated-fictional-secret-" + "z" * 32)
        RecoveryService(self.db, self.clock, rotated).release_hold()
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE suspended_at IS NOT NULL").fetchone()[0], 3)

    def test_account_and_waitlist_erasure_refuse_older_backup_without_mutation(self):
        for kind in ("waitlist", "account"):
            with self.subTest(kind=kind):
                backup = self.snapshot()
                if kind == "account":
                    self.lifecycle.delete_account("one")
                else:
                    with self.db.transaction() as connection:
                        connection.execute("INSERT INTO waitlist VALUES(?,?,?,?,?)", ("fictional", "waitlist@example.test", "Consent", storage_time(self.now), token_hash(random_token())))
                        connection.execute("DELETE FROM waitlist WHERE id='fictional'")
                        advance_erasure(connection)
                state = self.state()
                with self.assertRaisesRegex(RecoveryError, "erasure"):
                    self.recovery.restore(backup)
                self.assertEqual(self.state(), state)
                self.assertFalse(recovery_held(self.db, self.settings))
        # A new post-erasure snapshot is eligible and never restores the person.
        self.recovery.restore(self.snapshot())
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT id FROM users WHERE id='one'").fetchone())

    def test_corruption_wrong_key_and_foreign_schema_leave_live_data_untouched(self):
        backup = self.snapshot()
        original = backup.read_bytes()
        for offset in (len(original) // 2, len(original) - 1, 20):
            changed = bytearray(original)
            changed[offset] ^= 1
            bad = backup.parent / "fictional-corrupt.ctb"
            bad.write_bytes(changed)
            with self.assertRaises(RecoveryError):
                self.recovery.restore(bad)
            self.assertFalse(recovery_held(self.db, self.settings))
        wrong = replace(self.settings, backup_encryption_key=base64.urlsafe_b64encode(b"0" * 32).decode())
        with self.assertRaises(RecoveryError):
            RecoveryService(self.db, self.clock, wrong).restore(backup)
        with self.db.transaction() as connection:
            connection.execute("CREATE TABLE foreign_data(value TEXT)")
        with self.assertRaisesRegex(RecoveryError, "schema objects"):
            self.recovery.restore(backup)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 3)

    def test_fresh_host_foreign_lineage_and_missing_target_cannot_claim_authority(self):
        backup = self.snapshot()
        from connectthebots_server.db import Database
        fresh = replace(self.settings, database_path=self.db.path.parent / "fresh.db")
        database = Database(fresh.database_path, self.clock)
        service = RecoveryService(database, self.clock, fresh)
        with self.assertRaises(RecoveryError):
            service.restore(backup)
        self.assertFalse(fresh.database_path.exists())
        database.migrate()
        with self.assertRaisesRegex(RecoveryError, "another database"):
            service.restore(backup)

    def test_active_runtime_lock_refuses_restore_but_allows_backup(self):
        with runtime_lock(self.db.path):
            backup = self.snapshot()
            with self.assertRaisesRegex(RecoveryError, "database is in use"):
                self.recovery.restore(backup)
        self.recovery.restore(backup)

    def test_retention_only_prunes_owned_regular_files_after_success(self):
        first = self.snapshot()
        unrelated = first.parent / "operator-note.txt"
        unrelated.write_text("Keep this file", encoding="utf-8")
        link = first.parent / "ctb-backup-20200101T000000000000Z-0123456789abcdef.ctb"
        link.symlink_to(unrelated)
        self.now += timedelta(days=15)
        self.snapshot()
        self.assertFalse(first.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(unrelated.read_text(), "Keep this file")

    def test_symlink_or_public_backup_directory_refused(self):
        public = self.db.path.parent / "public"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(RecoveryError, "mode0700"):
            RecoveryService(self.db, self.clock, replace(self.settings, backup_directory=public)).backup()
        link = self.db.path.parent / "link"
        link.symlink_to(public, target_is_directory=True)
        with self.assertRaises(RecoveryError):
            RecoveryService(self.db, self.clock, replace(self.settings, backup_directory=link)).backup()

    def test_racing_erasure_aborts_snapshot_publication(self):
        original = self.recovery._authority_matches
        def changed(snapshot, live):
            original(snapshot, {**live, "erasure_generation": live["erasure_generation"] + 1})
        with patch.object(self.recovery, "_authority_matches", side_effect=changed), self.assertRaises(RecoveryError):
            self.recovery.backup()
        self.assertEqual(list((self.db.path.parent / "backups").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
