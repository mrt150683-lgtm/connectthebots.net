"""Fictional M2 work permissions survive lifecycle changes and recovery safely."""
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3
import unittest

from connectthebots_server import agent_texts
from connectthebots_server.agent_common import AgentError
from connectthebots_server.board import BoardService
from connectthebots_server.config import Settings
from connectthebots_server.db import Database
from connectthebots_server.goal_members import GoalMemberService
from connectthebots_server.goal_record import append_entry, verify_record
from connectthebots_server.goal_sessions import GoalSessionService
from connectthebots_server.goals import GoalsService
from connectthebots_server.interactions import InteractionService
from connectthebots_server.lifecycle import AccountLifecycle
from connectthebots_server.moderation import ModerationService
from connectthebots_server.notifications import NotificationService
from connectthebots_server.operator import OperatorService
from connectthebots_server.operator_alerts import OperatorAlerts
from connectthebots_server.recovery import RecoveryService
from connectthebots_server.recovery_authority import AuthorityError, RecoveryAuthority, local_state
from connectthebots_server.sessions import storage_time
from connectthebots_server.taste import TasteService
from tests_server import test_goals as goal_cases
from tests_server import test_offsite_recovery as offsite_cases


class GoalWorkPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = goal_cases.GoalFixture("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.db, self.goals = self.fixture.db, self.fixture.goals
        self.clock = lambda: self.fixture.now
        self.members = GoalMemberService(self.db, self.clock, self.goals)
        self.sessions = GoalSessionService(self.db, self.clock, self.goals, self.members)
        self.members.sessions = self.sessions
        self.goal = self.fixture.open_goal()
        self.slug = self.goal["slug"]
        for user in ("lead", "other", "third"):
            self.join(user)
        self.settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://fictional.example.test",
            "DATABASE_PATH": str(self.db.path), "SESSION_SECRET": "fictional-work-privacy-" + "p" * 32})
        self.lifecycle, self.operator = self.services(self.db, self.goals, self.settings)

    def services(self, database, goals, settings):
        board = BoardService(database, self.clock)
        alerts = OperatorAlerts(database, self.clock, settings)
        notifications = NotificationService(database, self.clock, settings, operator_alerts=alerts)
        interactions = InteractionService(database, self.clock, board, notifications=notifications)
        moderation = ModerationService(database, self.clock, board, interactions, alerts)
        lifecycle = AccountLifecycle(database, self.clock, board, interactions, notifications, moderation, goals=goals)
        operator = OperatorService(database, self.clock, board, interactions, notifications, moderation, lifecycle=lifecycle)
        return lifecycle, operator

    def join(self, user, slug=None, **extra):
        return self.members.join(user, slug or self.slug,
                                 {"capabilities": ["mathematics"], "approved_by_owner": True, **extra})

    def approve(self, user="other", slug=None, **extra):
        slug = slug or self.slug
        with self.db.connect() as connection:
            goal = connection.execute("SELECT * FROM goals WHERE slug=?", (slug,)).fetchone()
        body = {"sessions_per_week": 3, "minutes_per_session": 20,
                "until": (self.clock().date() + timedelta(days=30)).isoformat(),
                "show_first_name": False, "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION,
                "approved_by_owner": True, "charter_revision": goal["charter_revision"]}
        if goal["lead_user_id"] == user:
            body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
        return self.sessions.set_allocation(user, slug, {**body, **extra})

    def begin(self, user="other", slug=None):
        return self.sessions.start(user, slug or self.slug, {})["session_id"]

    def row(self, session_id, database=None):
        with (database or self.db).connect() as connection:
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (session_id,)).fetchone()
            return dict(row) if row else None

    def assert_closed(self, session_id, reason, database=None):
        row = self.row(session_id, database)
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["end_reason"], reason)
        self.assertLessEqual(row["grace_ends_at"], storage_time(self.clock()))
        self.assertEqual(row["reserved_minutes"], 20)

    def enroll(self):
        self.storage = offsite_cases.MemoryStorage()
        self.authority = RecoveryAuthority(self.storage, b"f" * 32, "ctb-" + "e" * 32 + "/")
        self.authority.enroll(self.db)

    def authority_state(self, database=None):
        with (database or self.db).connect() as connection:
            return local_state(connection)

    def test_export_keeps_only_own_membership_work_and_private_notes(self):
        charter = goal_cases.fictional_charter()
        charter["membership"] = "lead_approves"
        self.goals.revise_charter("lead", self.slug, charter, True)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                ("fourth", "fictional-fourth", "fourth@fictional.example.test", "Fictional Fourth",
                 storage_time(self.clock() - timedelta(days=30)), storage_time(self.clock())))
        self.join("fourth", note="Fictional fourth private pending-note canary.")
        self.join("lead", note="Fictional lead own note canary.")
        self.approve("lead")
        self.approve("other")
        lead_session, peer_session = self.begin("lead"), self.begin("other")
        self.sessions.end("other", peer_session, {"minutes_used": 2, "did": "Fictional other private session report canary."})
        live = self.goals.detail("lead", self.slug)["goal"]
        self.assertEqual(live["pending_members"][0]["note"], "Fictional fourth private pending-note canary.")
        self.assertNotIn("pending_members", self.goals.detail("other", self.slug)["goal"])
        pending = self.goals.detail("fourth", self.slug)["goal"]
        self.assertEqual(set(pending["your_membership"]), {"handle", "state", "capabilities", "note"})
        self.assertEqual(pending["your_membership"]["state"], "pending")
        self.assertEqual(pending["your_membership"]["capabilities"], ["mathematics"])
        self.assertEqual(pending["your_membership"]["note"], "Fictional fourth private pending-note canary.")
        self.assertNotIn("pending_members", pending)
        self.assertNotIn("your_membership", self.goals.detail(None, self.slug)["goal"])
        self.assertNotIn("Fictional fourth private pending-note canary", json.dumps(self.goals.detail("other", self.slug)))
        exported = self.goals.export_owner("lead")
        self.assertEqual([row["id"] for row in exported["sessions"]], [lead_session])
        self.assertTrue(all(row["user_id"] == "lead" for row in exported["allocations"]))
        serialized = json.dumps(exported)
        self.assertIn("Fictional lead own note canary", serialized)
        for private in ("Fictional fourth private pending-note canary", "Fictional other private session report canary", peer_session):
            self.assertNotIn(private, serialized)
        self.assertIn("Fictional fourth private pending-note canary", json.dumps(self.goals.export_owner("fourth")))

    def test_member_deletion_removes_work_rows_without_mutating_record_session_reference(self):
        self.approve()
        session_id = self.begin()
        with self.db.transaction() as connection:
            goal = connection.execute("SELECT * FROM goals WHERE slug=?", (self.slug,)).fetchone()
            member = self.members.member(connection, goal, "other")
            entry = append_entry(connection, goal, "step", member["handle"], {"note": "Fictional authored work canary."},
                                 self.clock(), author_user_id="other", session_id=session_id)
        self.lifecycle.delete_account("other")
        with self.db.connect() as connection:
            for table in ("goal_members", "goal_allocations", "goal_sessions"):
                self.assertIsNone(connection.execute("SELECT 1 FROM " + table + " WHERE user_id='other'").fetchone())
            retained = connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry["id"],)).fetchone()
            self.assertEqual(retained["session_id"], session_id)
            self.assertIsNone(retained["content_json"])
            self.assertIsNone(retained["author_user_id"])
            self.assertEqual(retained["entry_sha256"], entry["entry_sha256"])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertTrue(verify_record(self.goals.record(None, self.slug)["entries"])["valid"])

    def test_lead_deletion_ends_every_lease_on_that_goal_and_preserves_unrelated_work(self):
        self.approve("lead")
        self.approve("other")
        lead_session, peer_session = self.begin("lead"), self.begin("other")
        unrelated = self.fixture.open_goal("other", goal_cases.fictional_charter("Fictional: prism notation puzzle"))
        self.join("other", unrelated["slug"])
        self.join("third", unrelated["slug"])
        self.approve("third", unrelated["slug"])
        unaffected = self.begin("third", unrelated["slug"])
        self.lifecycle.delete_account("lead")
        self.assertIsNone(self.row(lead_session))
        self.assert_closed(peer_session, "account_deleted")
        self.assertIsNone(self.row(unaffected)["end_reason"])
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["state"], "paused")
        self.assertTrue(verify_record(self.goals.record(None, self.slug)["entries"])["valid"])

    def test_suspend_and_unsuspend_do_not_restore_allocation_or_lead_name(self):
        TasteService(self.db, self.clock).update_member("lead", {"first_name": "FictionalLead"})
        self.approve("lead", show_first_name=True)
        session_id = self.begin("lead")
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["lead"]["first_name"], "FictionalLead")
        self.operator.user_suspend("lead")
        self.assert_closed(session_id, "suspension")
        self.operator.user_unsuspend("lead")
        self.assertNotIn("first_name", self.goals.detail(None, self.slug)["goal"]["lead"])
        with self.assertRaises(AgentError):
            self.begin("lead")
        self.assertFalse(self.goals.export_owner("lead")["allocations"][0]["active"])

    def test_operator_pause_revokes_expired_lease_submission_grace(self):
        self.approve()
        session_id = self.begin()
        self.fixture.now += timedelta(minutes=21)
        with self.db.transaction() as connection:
            self.sessions.require_session(connection, "other", session_id, kind="finding")
        self.assertEqual(self.row(session_id)["end_reason"], "expired")
        self.goals.operator_action(self.slug, "pause", "Fictional operator pause.")
        self.assert_closed(session_id, "goal_paused")

    def test_lazy_inactivity_pause_ends_current_work(self):
        self.approve()
        self.fixture.now += timedelta(days=20, hours=23, minutes=50)
        session_id = self.begin()
        self.fixture.now += timedelta(minutes=11)
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["state"], "paused")
        self.assert_closed(session_id, "goal_paused")

    def test_operator_close_ends_work_without_erasing_reservations(self):
        self.approve()
        session_id = self.begin()
        self.goals.operator_action(self.slug, "close", "Fictional final research summary.")
        self.assert_closed(session_id, "goal_closed")
        self.assertIsNone(self.row(session_id)["minutes_used"])

    def test_charter_change_ends_old_lease_and_requires_new_scope_approval(self):
        self.approve()
        session_id = self.begin()
        charter = goal_cases.fictional_charter()
        charter["aim"] = "Produce a different fictional derivation with reproducible arithmetic."
        self.goals.revise_charter("lead", self.slug, charter, True)
        self.assert_closed(session_id, "charter_changed")
        with self.assertRaises(AgentError) as caught:
            self.begin()
        self.assertEqual(caught.exception.status, 409)
        self.approve()
        self.assertIsNotNone(self.begin())

    def test_pending_sensitive_revision_ends_work_before_operator_review(self):
        self.approve()
        session_id = self.begin()
        charter = goal_cases.fictional_charter(category="animal_health")
        charter["aim"] = "A fictional animal literature catalogue awaiting operator approval."
        result = self.goals.revise_charter("lead", self.slug, charter, True)["goal"]
        self.assertEqual(result["state"], "paused")
        self.assertTrue(result["review_pending"])
        self.assert_closed(session_id, "charter_changed")
        self.assertNotIn(charter["aim"], json.dumps(self.goals.detail("other", self.slug)))
        with self.assertRaises(AgentError):
            self.begin()

    def test_ordinary_session_has_no_marker_and_lifecycle_cancellation_advances_once(self):
        self.approve()
        self.enroll()
        before = self.authority_state()
        session_id = self.begin()
        self.sessions.end("other", session_id, {"minutes_used": 1, "did": "Fictional ordinary completion."})
        self.assertEqual(self.authority_state()["generation"], before["generation"])
        active = self.begin()
        self.goals.operator_action(self.slug, "pause", "Fictional protected pause.")
        after = self.authority_state()
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertEqual(after["growth_generation"], before["growth_generation"] + 1)
        self.assert_closed(active, "goal_paused")

    def test_authority_failure_rolls_back_suspension_allocations_and_lease_together(self):
        self.approve()
        session_id = self.begin()
        self.enroll()
        before = self.authority_state()
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.operator.user_suspend("other")
        self.storage.unavailable = False
        self.assertEqual(self.authority_state(), before)
        self.assertIsNone(self.row(session_id)["end_reason"])
        self.assertTrue(self.goals.export_owner("other")["allocations"][0]["active"])
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT suspended_at FROM users WHERE id='other'").fetchone()[0])

    def test_restore_revokes_work_grace_lead_permission_and_names_without_marker(self):
        TasteService(self.db, self.clock).update_member("lead", {"first_name": "FictionalLead"})
        self.approve("lead", show_first_name=True)
        self.approve("other")
        lead_session = self.begin("lead")
        self.fixture.now += timedelta(minutes=21)
        peer_session = self.begin("other")
        self.enroll()
        before = self.authority_state()
        snapshot = self.db.path.parent / "fictional-work-restore.db"
        with self.db.connect() as source, sqlite3.connect(snapshot) as destination:
            source.backup(destination)
        recovery = RecoveryService(self.db, self.clock, self.settings)
        recovery._quarantine(snapshot)
        restored = Database(snapshot, self.clock, recovery_authority=self.authority)
        after = self.authority_state(restored)
        for field in ("generation", "growth_generation", "marker"):
            self.assertEqual(after[field], before[field])
        self.authority.require(after)
        for session_id in (lead_session, peer_session):
            self.assert_closed(session_id, "recovery", restored)
        rotated = replace(self.settings, database_path=snapshot, session_secret="fictional-rotated-" + "r" * 32)
        restored_goals = GoalsService(restored, self.clock, settings=rotated)
        with restored.connect() as connection:
            self.assertEqual(connection.execute("SELECT SUM(show_first_name) FROM goal_members").fetchone()[0], 0)
            self.assertFalse(connection.execute("SELECT 1 FROM goal_allocations WHERE stopped_at IS NULL").fetchone())
        RecoveryService(restored, self.clock, rotated).release_hold()
        _, operator = self.services(restored, restored_goals, rotated)
        operator.user_unsuspend("lead")
        restored_members = GoalMemberService(restored, self.clock, restored_goals)
        restored_sessions = GoalSessionService(restored, self.clock, restored_goals, restored_members)
        with self.assertRaises(AgentError):
            restored_sessions.start("lead", self.slug, {})
        self.assertNotIn("first_name", restored_goals.detail(None, self.slug)["goal"]["lead"])
        self.assertFalse(restored_goals.export_owner("lead")["allocations"][0]["active"])


if __name__ == "__main__":
    unittest.main()
