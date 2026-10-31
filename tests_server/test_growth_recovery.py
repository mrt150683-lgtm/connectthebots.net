"""Growth privacy survives offsite recovery, migration and account release."""
import base64
import importlib.util
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from connectthebots_server.agent_common import AgentError
from connectthebots_server.db import Database, load_migrations
from connectthebots_server.recovery import RecoveryError, RecoveryService
from connectthebots_server.recovery_authority import AuthorityError, RecoveryAuthority, local_state
from connectthebots_server.report_store import ReportService
from connectthebots_server.sessions import storage_time
from connectthebots_server.taste import TasteService
from connectthebots_server.topic_boards import TopicBoardService
from tests_server.test_offsite_recovery import MemoryStorage, OffsiteFixture


class GrowthFixture(OffsiteFixture):
    def setUp(self):
        super().setUp()
        self.taste = TasteService(self.db, self.clock)
        self.topics = TopicBoardService(self.db, self.clock)
        self.answer = {"topic": "music", "items": ["Fictional garden songs"], "tier": "ask"}
        for owner in ("one", "two"):
            self.taste.save_answers(owner, [self.answer])
        revision = self.taste.interview("one")["answers"]["music"]["revision"]
        self.taste.disclose("one", self.taste.member("two")["public_id"], ["music"], True, {"music": revision})
        self.taste.update_member("one", {"first_name": "Fictional", "show_name_on_leaderboards": True,
                                         "signature_opt_in": True, "signature_includes_code": True,
                                         "approved_by_owner": True})
        self.topic_post = self.topics.post("one", "projects", "Fictional project for testing restored privacy.", [], True)["post"]["id"]
        self.topic_reply = self.topics.reply("two", self.topic_post, "Fictional reply for recovery.", True)["id"]


class GrowthAuthorityTests(GrowthFixture):
    def test_privacy_service_changes_advance_independent_authority_once(self):
        changes = (
            lambda: self.topics.withdraw("one", "post", self.topic_post),
            lambda: self.topics.withdraw("two", "reply", self.topic_reply),
            lambda: self.taste.save_answers("one", [{**self.answer, "tier": "never"}]),
            lambda: self.taste.update_member("one", {"first_name": None, "signature_opt_in": False}),
        )
        for change in changes:
            before = self.generation()
            change()
            self.assertEqual(self.generation(), before + 1)
            self.authority.require(self.authority_state())

    def test_storage_failure_rolls_back_privacy_changes(self):
        before = self.generation()
        self.storage.unavailable = True
        for change in (
            lambda: self.topics.withdraw("one", "post", self.topic_post),
            lambda: self.taste.save_answers("one", [{**self.answer, "tier": "never"}]),
            lambda: self.taste.update_member("one", {"first_name": None}),
        ):
            with self.assertRaises(AuthorityError):
                change()
        self.storage.unavailable = False
        self.assertEqual(self.generation(), before)
        self.assertEqual(self.taste.member("one")["first_name"], "Fictional")
        self.assertEqual(len(self.topics.posts("two", "projects")["posts"]), 1)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT tier FROM taste_answers WHERE user_id='one'").fetchone()[0], "ask")

    def test_noop_profile_answer_and_routine_timestamps_do_not_write_markers(self):
        before = self.generation()
        self.now += timedelta(minutes=1)
        self.taste.update_member("one", {"first_name": "Fictional"})
        self.taste.save_answers("two", [self.answer])
        with self.db.transaction() as connection:
            connection.execute("UPDATE interview_state SET last_checkin_at=?,checkin_count=checkin_count+1", (storage_time(self.now),))
            connection.execute("UPDATE member_profiles SET updated_at=?", (storage_time(self.now),))
            connection.execute("UPDATE users SET last_login_at=?", (storage_time(self.now),))
        self.assertEqual(self.generation(), before)

    def test_removal_and_moderation_of_growth_records_advance_authority(self):
        now = storage_time(self.now)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO topic_signals VALUES('one','fictional','Fictional',?)", (now,))
            connection.execute("INSERT INTO share_approvals(id,user_id,kind,gap,created_at) VALUES('share','one','missing_person','Fictional gap',?)", (now,))
            connection.execute("INSERT INTO share_permissions VALUES('one','[\"https://forum.example.test/c/makers\"]',2,?)", (now,))
            connection.execute("INSERT INTO board_content_reports(id,reporter_id,target_kind,target_id,reason,details,created_at) VALUES('report','two','post',?,'privacy','Fictional details',?)", (self.topic_post, now))
            connection.execute("INSERT INTO referral_codes VALUES('one','fictional-code',?)", (now,))
            connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at) VALUES('referral','one','two','fictional-code','agent',?)", (now,))
        changes = (
            ("UPDATE board_posts SET hidden_at=? WHERE id=?", (now, self.topic_post)),
            ("UPDATE board_replies SET hidden_at=? WHERE id=?", (now, self.topic_reply)),
            ("UPDATE topic_boards SET archived_at=? WHERE id='seed-projects'", (now,)),
            ("DELETE FROM taste_disclosures WHERE user_id='one'", ()),
            ("DELETE FROM taste_answers WHERE user_id='one'", ()),
            ("DELETE FROM share_approvals WHERE id='share'", ()),
            ("UPDATE share_permissions SET weekly_limit=3 WHERE user_id='one'", ()),
            ("DELETE FROM share_permissions WHERE user_id='one'", ()),
            ("DELETE FROM topic_signals WHERE user_id='one'", ()),
            ("DELETE FROM board_content_reports WHERE id='report'", ()),
            ("UPDATE referrals SET flagged_at=?,flag_reason='operator' WHERE id='referral'", (now,)),
            ("DELETE FROM referral_codes WHERE user_id='one'", ()),
            ("INSERT INTO growth_config VALUES('fictional','false',?)", (now,)),
        )
        for statement, parameters in changes:
            with self.subTest(statement=statement):
                before = self.generation()
                with self.db.transaction() as connection:
                    connection.execute(statement, parameters)
                self.assertEqual(self.generation(), before + 1)


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("cryptography"),
                     "Encrypted recovery requires Linux file ownership, locking and crypto extra")
class GrowthEncryptedRecoveryTests(GrowthFixture):
    def test_old_backup_refused_after_withdrawal_or_private_data_removal(self):
        for change in (
            lambda: self.topics.withdraw("one", "post", self.topic_post),
            lambda: self.taste.save_answers("one", [{**self.answer, "tier": "never", "items": ["Replacement private item"]}]),
            lambda: self.taste.update_member("one", {"first_name": None}),
        ):
            snapshot = Path(self.recovery.backup()["path"])
            change()
            generation = self.generation()
            with self.assertRaises((RecoveryError, AuthorityError)):
                self.recovery.restore(snapshot)
            self.assertEqual(self.generation(), generation)
            self.assertFalse(self.recovery._validate(self.db.path)["recovery_hold"])

    def test_quarantine_keeps_growth_private_after_secret_and_account_release(self):
        now = storage_time(self.now)
        self.reports.create_key("one", "Fictional agent")
        with self.db.transaction() as connection:
            key_id = connection.execute("SELECT id FROM report_keys WHERE user_id='one'").fetchone()[0]
            connection.execute("INSERT INTO agent_receipts VALUES(?,?,?,?,?,?)", (key_id, "nonce", "body", '{"private":"fictional peer"}', 200, now))
            connection.execute("INSERT INTO taste_matches_seen VALUES('one','two',?)", (now,))
            connection.execute("INSERT INTO topic_signals VALUES('one','fictional','Fictional',?)", (now,))
            connection.execute("INSERT INTO early_access_grants VALUES('one','leaderboard','fictional',?,?)", (now, storage_time(self.now + timedelta(days=1))))
            connection.execute("INSERT INTO politics_consent_decisions(user_id,notice_version,choice,recorded_at,source) VALUES('one','fictional-notice','granted',?,'agent_attestation')", (now,))
            connection.execute("INSERT INTO taste_answers(user_id,topic,items_json,tier,skipped,updated_at) VALUES('one','politics','[\"centre\"]','share',0,?)", (now,))
        snapshot = Path(self.recovery.backup()["path"])
        local_generation = self.authority_state()["growth_generation"]
        result = self.recovery.restore(snapshot)
        self.assertTrue(result["recovery_hold"])
        self.assertEqual(self.authority_state()["growth_generation"], local_generation)
        rotated = replace(self.settings, session_secret="fictional-rotated-secret-" + "r" * 32)
        RecoveryService(self.db, self.clock, rotated).release_hold()
        self.operator.settings = rotated
        for owner in ("one", "two"):
            self.operator.user_unsuspend(owner)
        self.assertEqual(self.topics.posts("two", "projects")["posts"], [])
        with self.assertRaises(AgentError):
            self.topics.thread("two", self.topic_post)
        profile = self.taste.member("one")
        self.assertEqual(profile["first_name"], "Fictional")
        self.assertFalse(any(profile[key] for key in ("show_name_on_leaderboards", "signature_opt_in", "signature_includes_code")))
        with self.db.connect() as connection:
            for table in ("taste_disclosures", "agent_receipts", "taste_matches_seen", "topic_signals", "early_access_grants"):
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0, table)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_answers WHERE topic='politics'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT choice,source FROM politics_consent_decisions ORDER BY id DESC LIMIT 1").fetchone()[:], ("withdrawn", "recovery"))
            self.assertEqual(connection.execute("SELECT tier,items_json FROM taste_answers WHERE user_id='one'").fetchone()[:], ("never", '["Fictional garden songs"]'))
            self.assertIsNotNone(connection.execute("SELECT hidden_at FROM board_replies WHERE id=?", (self.topic_reply,)).fetchone()[0])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM report_keys WHERE revoked_at IS NULL").fetchone()[0], 0)

    def test_unenrolled_intact_restore_also_refuses_removed_growth_data(self):
        from tests_server.test_recovery import EncryptedRecoveryTests
        # A separate fictional local-only host must not depend on B2 enrollment
        # to reject a snapshot predating a personal-data or permission change.
        fixture = EncryptedRecoveryTests('test_backup_encrypts_private_content_and_restore_quarantines_authority')
        fixture.setUp()
        try:
            taste = TasteService(fixture.db, fixture.clock)
            taste.save_answers("one", [self.answer])
            snapshot = fixture.snapshot()
            with fixture.db.connect() as connection:
                before = local_state(connection)
            taste.save_answers("one", [{**self.answer, "items": ["Replacement private value"], "tier": "never"}])
            with fixture.db.connect() as connection:
                after = local_state(connection)
                self.assertFalse(after["enrolled"])
                self.assertEqual(after["generation"], before["generation"])
                self.assertEqual(after["growth_generation"], before["growth_generation"] + 1)
            with self.assertRaises(RecoveryError):
                fixture.recovery.restore(snapshot)
            fresh = fixture.snapshot()
            generation = after["growth_generation"]
            self.assertTrue(fixture.recovery.restore(fresh)["restored"])
            with fixture.db.connect() as connection:
                self.assertEqual(local_state(connection)["growth_generation"], generation)
        finally:
            fixture.doCleanups()


class GrowthMigrationTests(OffsiteFixture):
    def legacy(self, *, politics=True):
        path = self.db.path.parent / "legacy.db"
        # Schema 11: every migration before the growth privacy correction (0012).
        old = Database(path, self.clock, migrations=tuple(m for m in load_migrations() if m.name < "0012"))
        old.migrate()
        now = storage_time(self.now)
        with old.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES('legacy','fictional','legacy@example.invalid','Fictional',?,?)", (now, now))
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES('peer','fictional-peer','peer@example.invalid','Fictional peer',?,?)", (now, now))
            if politics:
                connection.execute("INSERT INTO taste_answers VALUES('legacy','politics','[\"centre\"]','never',0,?)", (now,))
            connection.execute("INSERT INTO taste_disclosures VALUES('legacy','peer','music',?)", (now,))
        ReportService(old, self.clock).create_key("legacy", "Fictional legacy agent")
        with old.transaction() as connection:
            key_id = connection.execute("SELECT id FROM report_keys WHERE user_id='legacy'").fetchone()[0]
            connection.execute("INSERT INTO agent_receipts VALUES(?,?,?,?,?,?)", (key_id, "legacy-nonce", "legacy-body", '{"peer":"fictional-private-value"}', 200, now))
        storage = MemoryStorage()
        authority = RecoveryAuthority(storage, b"f" * 32, "ctb-" + "b" * 32 + "/")
        authority.enroll(old)
        return old, storage, authority

    def test_legacy_cleanup_requires_authority_and_removes_no_other_answers(self):
        old, storage, authority = self.legacy()
        with old.transaction() as connection:
            connection.execute("INSERT INTO taste_answers VALUES('legacy','music','[\"Fictional\"]','never',0,?)", (storage_time(self.now),))
        with self.assertRaises(AuthorityError):
            Database(old.path, self.clock).migrate()
        current = Database(old.path, self.clock, recovery_authority=authority)
        storage.unavailable = True
        with self.assertRaises(AuthorityError):
            current.migrate()
        storage.unavailable = False
        with old.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 11)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_answers WHERE topic='politics'").fetchone()[0], 1)
        current.migrate()
        with current.connect() as connection:
            self.assertEqual(connection.execute("SELECT topic FROM taste_answers").fetchone()[0], "music")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM politics_consent_decisions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_disclosures").fetchone()[0], 0)
            receipt = connection.execute("SELECT nonce_hash,body_hash,response_json FROM agent_receipts").fetchone()
            self.assertEqual(receipt[:], ("legacy-nonce", "legacy-body", '{"replayed":true,"result_retained":false}'))
            self.assertEqual(local_state(connection)["generation"], 1)
        self.assertTrue(current.is_current())

    def test_migration_timeout_after_remote_acceptance_does_not_retry_or_erase(self):
        old, storage, authority = self.legacy()
        current = Database(old.path, self.clock, recovery_authority=authority)
        storage.timeout_after_write = True
        with self.assertRaises(AuthorityError):
            current.migrate()
        storage.timeout_after_write = False
        with self.assertRaises(AuthorityError):
            current.migrate()
        with old.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 11)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_answers WHERE topic='politics'").fetchone()[0], 1)
            self.assertEqual(local_state(connection)["generation"], 0)
        self.assertEqual(authority.latest()["payload"]["generation"], 1)

    def test_legacy_peer_payload_cleanup_is_guarded_without_politics_answers(self):
        old, storage, authority = self.legacy(politics=False)
        # Isolate receipt cleanup from both political data and legacy grants.
        with old.transaction() as connection:
            connection.execute("DELETE FROM taste_disclosures")
        current = Database(old.path, self.clock, recovery_authority=authority)
        storage.unavailable = True
        with self.assertRaises(AuthorityError):
            current.migrate()
        storage.unavailable = False
        current.migrate()
        with current.connect() as connection:
            self.assertEqual(local_state(connection)["generation"], 1)
            self.assertEqual(connection.execute("SELECT response_json FROM agent_receipts").fetchone()[0], '{"replayed":true,"result_retained":false}')

    @unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2")),
                         "Application startup requires the optional server extra")
    def test_actual_application_startup_supplies_authority_to_cleanup_migration(self):
        from starlette.testclient import TestClient
        from connectthebots_server.app import create_app
        old, storage, authority = self.legacy()
        settings = replace(self.settings, database_path=old.path)
        with patch('connectthebots_server.recovery_authority.from_settings', return_value=authority) as configured:
            application = create_app(settings, self.clock)
        configured.assert_called_once_with(settings)
        with TestClient(application, base_url="https://fictional.example.test") as client:
            self.assertEqual(client.get('/healthz').status_code, 200)
        with Database(old.path, self.clock).connect() as connection:
            self.assertEqual(local_state(connection)["generation"], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_answers WHERE topic='politics'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
