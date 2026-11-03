"""Exact fictional lead-role choices, assignment, revocation and recovery."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from connectthebots_server.db import Database
from connectthebots_server.goal_leadership import GoalLeadershipService, export_owner
from connectthebots_server.goal_record import verify_record
from connectthebots_server.recovery import RecoveryService
from connectthebots_server.recovery_authority import AuthorityError, RecoveryAuthority, local_state
from connectthebots_server.sessions import storage_time
from connectthebots_server.taste import TasteService
from tests_server.test_goal_sessions import SessionFixture
from tests_server.test_goals import fictional_charter
from tests_server.test_offsite_recovery import MemoryStorage


class LeadershipTests(SessionFixture):
    def setUp(self):
        super().setUp()
        self.leadership = GoalLeadershipService(self.db, lambda: self.now, self.goals, self.members, self.sessions)

    def handle(self, user):
        with self.db.connect() as connection:
            return connection.execute("SELECT handle FROM goal_members WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND user_id=?", (self.slug, user)).fetchone()[0]

    def pause(self):
        self.goals.operator_action(self.slug, "pause", "Fictional operator pause to request a new lead.")

    def offer(self, user="other", **changes):
        preview = self.leadership.preview(user, self.slug)
        return self.leadership.volunteer(user, self.slug, {"charter_revision": preview["charter_revision"],
            "notice_version": preview["notice_version"], "approved_by_owner": True, **changes})

    def request(self, target="other", user="lead", **changes):
        return self.leadership.handover(user, self.slug, {"handle": self.handle(target),
            "charter_revision": self.goals.detail(user, self.slug)["goal"]["charter_revision"], "approved_by_owner": True, **changes})

    def assign(self, user="other"):
        return self.leadership.assign(self.slug, self.handle(user), "Fictional reviewed leadership assignment.")

    def state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    def enroll(self):
        self.storage = MemoryStorage()
        self.authority = RecoveryAuthority(self.storage, b"l" * 32, "ctb-" + "a" * 32 + "/")
        self.authority.enroll(self.db)

    def test_preview_renders_exact_role_and_stores_no_consent_or_charter_copy(self):
        self.pause()
        before = self.state()
        preview = self.leadership.preview("other", self.slug)
        self.assertEqual(preview["notice_version"], "goal-lead-role-v1")
        self.assertIn(self.goal["charter"]["title"], preview["statement"])
        self.assertIn(self.handle("other"), preview["statement"])
        self.assertIn("role only", preview["statement"])
        self.assertEqual(self.state(), before)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_lead_approvals").fetchone()[0], 0)
        self.offer()
        with self.db.connect() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM goal_lead_approvals")]
        self.assertNotIn(self.goal["charter"]["title"], json.dumps(rows))
        self.assertNotIn("statement", rows[0])
        self.assertNotIn("charter_json", rows[0])

    def test_exact_approval_version_revision_and_fields_are_required(self):
        self.pause()
        for changes in ({"approved_by_owner": False}, {"approved_by_owner": 1}, {"notice_version": "old"},
                        {"charter_revision": 0}, {"charter_revision": True}):
            self.error(409, self.offer, **changes)
        self.error(422, self.offer, user_id="third")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_lead_approvals").fetchone()[0], 0)

    def test_paused_assignment_consumes_role_choice_and_preserves_fixed_handles_and_chain(self):
        old_handle, new_handle = self.handle("lead"), self.handle("other")
        before = self.goals.public_record(self.slug)["entries"]
        self.pause()
        self.offer()
        result = self.assign()["goal"]
        self.assertEqual(result["state"], "open")
        self.assertEqual(result["lead"]["handle"], new_handle)
        self.assertEqual(self.handle("lead"), old_handle)
        self.assertEqual(self.handle("other"), new_handle)
        record = self.goals.public_record(self.slug)["entries"]
        self.assertEqual(record[:len(before)], before)
        self.assertTrue(verify_record(record)["valid"])
        self.assertEqual(record[-1]["content"], {"action": "assign_lead", "handle": new_handle, "reason": "Fictional reviewed leadership assignment."})
        self.assertNotIn("user_id", json.dumps(record))
        self.assertNotIn("notice_version", json.dumps(record[-1]))
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT state FROM goal_lead_approvals").fetchone()[0], "consumed")
        self.pause()
        self.error(409, self.assign)

    def test_open_handover_requires_exact_current_lead_and_target_and_changes_nothing_until_operator(self):
        self.error(409, self.offer)
        self.error(409, self.request, approved_by_owner=False)
        self.error(403, self.request, user="third")
        self.error(422, self.request, target="lead")
        self.request()
        self.offer()
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["lead"]["handle"], self.handle("lead"))
        # A new exact handover identity cannot reuse the target's old yes.
        self.request()
        self.error(409, self.assign)
        self.offer()
        self.request(target="third")
        self.error(409, self.assign)
        self.offer("third")
        self.assign("third")
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["lead"]["handle"], self.handle("third"))

    def test_charter_change_makes_role_and_handover_snapshots_stale(self):
        self.request()
        self.offer()
        charter = fictional_charter()
        charter["aim"] = "A different fictional scope requiring a fresh lead role approval."
        self.goals.revise_charter("lead", self.slug, charter, True)
        self.error(409, self.assign)
        self.error(409, self.offer)
        self.request()
        self.offer()
        self.assign()

    def test_closed_hidden_proposed_reviewing_or_erased_goals_cannot_assign(self):
        self.pause()
        self.offer()
        with self.db.connect() as connection:
            original = dict(self.goals._goal(connection, self.slug))
        for state, pending, charter in (("closed", 0, original["charter_json"]), ("hidden", 0, original["charter_json"]),
                ("proposed", 0, original["charter_json"]), ("paused", 1, original["charter_json"]), ("paused", 0, None)):
            with self.db.transaction() as connection:
                connection.execute("UPDATE goals SET state=?,review_pending=?,charter_json=? WHERE slug=?", (state, pending, charter, self.slug))
            self.error(409, self.assign)
        with self.db.transaction() as connection:
            connection.execute("UPDATE goals SET state='paused',review_pending=0,charter_json=? WHERE slug=?", (original["charter_json"], self.slug))
        self.assign()

    def test_eligibility_access_membership_and_suspension_are_rechecked_at_assignment(self):
        self.pause()
        self.offer()
        for sql, reset, status in (
            ("UPDATE users SET suspended_at='fictional' WHERE id='other'", "UPDATE users SET suspended_at=NULL WHERE id='other'", 403),
            ("UPDATE goal_members SET state='removed' WHERE user_id='other'", "UPDATE goal_members SET state='active' WHERE user_id='other'", 403),
            ("UPDATE users SET created_at='2026-11-01T12:00:00.000000Z' WHERE id='other'", "UPDATE users SET created_at='2026-09-01T12:00:00.000000Z' WHERE id='other'", 409),
            ("UPDATE early_access_grants SET expires_at='2026-10-01T00:00:00.000000Z' WHERE user_id='other'", "UPDATE early_access_grants SET expires_at='2026-12-01T00:00:00.000000Z' WHERE user_id='other'", 403)):
            with self.db.transaction() as connection:
                connection.execute(sql)
            self.error(status, self.assign)
            with self.db.transaction() as connection:
                connection.execute(reset)
        self.assign()

    def test_per_owner_and_global_caps_and_topic_are_rechecked(self):
        self.pause()
        self.offer()
        peer = self.open_goal("other", fictional_charter("Fictional: separate cap puzzle"))
        self.error(409, self.assign)
        self.setting("goals_led_per_member", 2)
        self.setting("goals_open_max", 1)
        self.error(409, self.assign)
        self.setting("goals_open_max", 10)
        with self.db.transaction() as connection:
            key = self.goals._goal(connection, self.slug)["topic_key"]
            connection.execute("UPDATE goals SET topic_key=? WHERE slug=?", (key, peer["slug"]))
        self.error(409, self.assign)
        with self.db.transaction() as connection:
            connection.execute("UPDATE goals SET topic_key='fictional separate cap puzzle' WHERE slug=?", (peer["slug"],))
        self.assign()

    def test_sensitive_capacity_is_rechecked(self):
        self.pause()
        self.offer()
        with self.db.transaction() as connection:
            connection.execute("UPDATE goals SET tier='sensitive' WHERE slug=?", (self.slug,))
        self.error(409, self.assign)
        self.setting("goals_sensitive_open_max", 1)
        self.assign()

    def test_role_change_revokes_both_leads_work_names_and_old_preview_identity(self):
        for user in ("lead", "other"):
            TasteService(self.db, lambda: self.now).update_member(user, {"first_name": "Fictional" + user.title()})
            self.approve(user, show_first_name=True)
        old_session, new_session = self.begin("lead"), self.begin("other")
        with self.db.connect() as connection:
            before = {row["user_id"]: dict(row) for row in connection.execute("SELECT * FROM goal_allocations")}
            generations = {row["user_id"]: row["work_generation"] for row in connection.execute("SELECT * FROM goal_members")}
        self.request()
        self.offer()
        self.assign()
        self.assertNotIn("first_name", self.goals.detail(None, self.slug)["goal"]["lead"])
        with self.db.connect() as connection:
            for row in connection.execute("SELECT * FROM goal_allocations"):
                self.assertIsNotNone(row["stopped_at"])
                self.assertIsNone(row["lead_notice_version"])
                self.assertIsNone(row["lead_approved_at"])
                self.assertNotEqual(row["approval_id"], before[row["user_id"]]["approval_id"])
                self.assertFalse(row["show_first_name"])
            for user in ("lead", "other"):
                self.assertEqual(connection.execute("SELECT work_generation FROM goal_members WHERE user_id=?", (user,)).fetchone()[0], generations[user] + 1)
                self.assertFalse(connection.execute("SELECT show_first_name FROM goal_members WHERE user_id=?", (user,)).fetchone()[0])
            for session in (old_session, new_session):
                row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (session["session_id"],)).fetchone()
                self.assertEqual(row["end_reason"], "lead_changed")
                self.assertEqual(row["reserved_minutes"], 20)
                self.assertLessEqual(row["grace_ends_at"], storage_time(self.now))
        self.error(409, self.begin, "other")
        self.approve("other")
        self.begin("other")
        # Reassign the old lead: their original T3L still cannot return.
        self.request(target="lead", user="other")
        self.offer("lead")
        self.assign("lead")
        self.error(409, self.begin, "lead")
        self.approve("lead")
        self.begin("lead")

    def test_role_assignment_does_not_revoke_unrelated_owner_goal_or_peer_work(self):
        self.setting("goals_led_per_member", 2)
        other_goal = self.open_goal("other", fictional_charter("Fictional: untouched role puzzle"))
        self.members.join("other", other_goal["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
        self.approve("other", other_goal["slug"])
        unrelated = self.begin("other", other_goal["slug"])
        self.approve("third")
        peer = self.begin("third")
        self.request()
        self.offer()
        self.assign()
        for session in (unrelated, peer):
            with self.db.connect() as connection:
                self.assertIsNone(connection.execute("SELECT end_reason FROM goal_sessions WHERE id=?", (session["session_id"],)).fetchone()[0])
        self.gate(unrelated, user="other")
        self.gate(peer, user="third")

    def test_work_stop_does_not_infer_withdrawal_of_a_separate_role_choice(self):
        self.pause()
        self.offer()
        self.sessions.stop("other", self.slug)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT state FROM goal_lead_approvals").fetchone()[0], "pending")
        self.assign()
        self.error(409, self.begin, "other")

    def test_owner_can_revoke_pending_choices_without_a_session_or_enabled_feature(self):
        self.request()
        self.offer()
        self.setting("feature.goals", "off")
        self.leadership.withdraw("other", self.slug)
        self.error(409, self.assign)
        self.leadership.cancel_handover("lead", self.slug)
        self.setting("feature.goals", "everyone")
        self.error(409, self.offer)
        self.request()
        self.offer()
        self.leadership.cancel_handover("lead", self.slug)
        self.error(409, self.assign)
        self.error(403, self.leadership.cancel_handover, "third", self.slug)

    def test_export_and_deletion_scope_private_role_snapshots_to_their_owners(self):
        self.request()
        self.offer()
        with self.db.connect() as connection:
            own = export_owner(connection, "other")
            lead = export_owner(connection, "lead")
            unrelated = export_owner(connection, "third")
        self.assertEqual(len(own["lead_role_approvals"]), 1)
        self.assertEqual(own["lead_handovers"], [])
        self.assertEqual(lead["lead_role_approvals"], [])
        self.assertEqual(len(lead["lead_handovers"]), 1)
        self.assertEqual(unrelated, {"lead_role_approvals": [], "lead_handovers": []})
        for exported in (own, lead):
            self.assertNotIn("user_id", json.dumps(exported))
            self.assertNotIn(self.goal["charter"]["title"], json.dumps(exported))
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id='other'")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_lead_approvals").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_lead_handovers").fetchone()[0], 0)
            self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())

    def test_approval_and_assignment_are_protected_once_each_and_outage_rolls_back(self):
        self.pause()
        self.enroll()
        before = self.state()
        self.offer()
        approved = self.state()
        self.assertEqual(approved["generation"], before["generation"] + 1)
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.assign()
        self.storage.unavailable = False
        self.assertEqual(self.state(), approved)
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["state"], "paused")
        self.assign()
        self.assertEqual(self.state()["generation"], approved["generation"] + 1)

    def test_recovery_quarantine_revokes_pending_role_choices_without_authority_advance(self):
        self.request()
        self.offer()
        self.enroll()
        before = self.state()
        snapshot = Path(self.temp.name) / "fictional-leadership-quarantine.db"
        with self.db.connect() as source, closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination)
        RecoveryService(self.db, lambda: self.now, SimpleNamespace(session_secret="fictional-restore-" + "s" * 32))._quarantine(snapshot)
        restored = Database(snapshot, lambda: self.now, recovery_authority=self.authority)
        with restored.connect() as connection:
            after = local_state(connection)
            for field in ("generation", "growth_generation", "marker"):
                self.assertEqual(after[field], before[field])
            self.authority.require(after)
            for table in ("goal_lead_approvals", "goal_lead_handovers"):
                self.assertEqual(connection.execute(f"SELECT state FROM {table}").fetchone()[0], "revoked")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT state FROM goal_lead_approvals").fetchone()[0], "pending")

    def test_hidden_current_charter_cannot_leak_in_role_preview_or_resume_by_assignment(self):
        self.pause()
        self.offer()
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='hidden' WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND kind='charter'", (self.slug,))
        self.error(409, self.leadership.preview, "other", self.slug)
        self.error(409, self.assign)

    def test_recovery_hold_and_suspended_owners_cannot_change_role_choices(self):
        self.pause()
        self.offer()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='other'", (storage_time(self.now),))
        self.error(403, self.leadership.withdraw, "other", self.slug)
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=NULL WHERE id='other'")
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        self.error(503, self.assign)
        self.error(503, self.leadership.withdraw, "other", self.slug)
