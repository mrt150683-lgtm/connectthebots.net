"""M2 joining rules and pseudonymous membership with fictional owners."""
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3
import unittest

from connectthebots_server import agent_texts
from connectthebots_server.agent_common import AgentError
from connectthebots_server.db import Database, load_migrations
from connectthebots_server.goal_members import GoalMemberService, display_author, export_memberships
from connectthebots_server.goal_sessions import GoalSessionService
from connectthebots_server.recovery_authority import local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_goals import GoalFixture, fictional_charter


class GoalMemberTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.members = GoalMemberService(self.db, lambda: self.now, self.goals)
        self.sessions = GoalSessionService(self.db, lambda: self.now, self.goals, self.members)
        self.members.sessions = self.sessions

    def goal(self, *, joining=None, membership="open", title="Fictional: tidal clock puzzle"):
        charter = fictional_charter(title)
        charter["joining"] = joining or {}
        charter["membership"] = membership
        return self.open_goal(charter=charter)

    def join(self, slug, owner="other", **extra):
        return self.members.join(owner, slug, {"capabilities": ["mathematics"], "approved_by_owner": True, **extra})["member"]

    def answer(self, owner, topic, items, tier="share", skipped=False):
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO taste_answers(user_id,topic,items_json,tier,skipped,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(user_id,topic) DO UPDATE SET items_json=excluded.items_json,tier=excluded.tier,skipped=excluded.skipped",
                (owner, topic, json.dumps(items), tier, int(skipped), storage_time(self.now)))

    def lead_session(self, slug):
        self.join(slug, "lead")
        goal = self.goals.detail("lead", slug)["goal"]
        self.sessions.set_allocation("lead", slug, {
            "sessions_per_week": 3, "minutes_per_session": 20, "until": "2026-11-30", "show_first_name": False,
            "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True,
            "charter_revision": goal["charter_revision"], "lead_notice_version": agent_texts.GOAL_LEAD_VERSION,
            "lead_approved_by_owner": True,
        })
        return self.sessions.start("lead", slug, {})

    def state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    def test_minimum_account_age_cannot_be_lowered_by_charter_and_exact_boundary(self):
        goal = self.goal(joining={"account_age_days": 0})
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='other'", (storage_time(self.now - timedelta(days=7) + timedelta(microseconds=1)),))
        result = self.members.eligibility("other", goal["slug"], ["mathematics"])
        self.assertFalse(result["eligible"])
        self.assertIn("7 days", result["missing"][0])
        self.now += timedelta(microseconds=1)
        self.assertTrue(self.members.eligibility("other", goal["slug"])["eligible"])

    def test_charter_may_require_older_account_and_completed_interview(self):
        goal = self.goal(joining={"account_age_days": 40, "interview_complete": True})
        result = self.members.eligibility("other", goal["slug"])
        self.assertEqual(len(result["missing"]), 2)
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='other'", (storage_time(self.now - timedelta(days=40)),))
            connection.execute("INSERT INTO interview_state(user_id,started_at,completed_at) VALUES(?,?,?)", ("other", storage_time(self.now), storage_time(self.now)))
        self.assertTrue(self.members.eligibility("other", goal["slug"])["eligible"])

    def test_declared_capability_any_and_all_are_deterministic(self):
        goal = self.goal(joining={"capabilities": {"all_of": ["mathematics", "reviewing"]}})
        self.assertFalse(self.members.eligibility("other", goal["slug"], ["mathematics"])["eligible"])
        self.assertTrue(self.members.eligibility("other", goal["slug"], ["reviewing", "mathematics"])["eligible"])
        revision = fictional_charter()
        revision["joining"] = {"capabilities": {"any_of": ["mathematics", "reviewing"]}}
        self.goals.revise_charter("lead", goal["slug"], revision, True)
        self.assertTrue(self.members.eligibility("other", goal["slug"], ["reviewing"])["eligible"])
        for value in (["mind_reading"], ["mathematics", "mathematics"], "mathematics", [1]):
            self.error(422, self.members.eligibility, "other", goal["slug"], value)

    def test_eligibility_queries_only_share_and_unskipped_rows(self):
        goal = self.goal(joining={"interests": [{"topic": "hobbies", "item": "astronomy"}]})
        for tier, skipped in (("ask", False), ("never", False), ("share", True)):
            with self.subTest(tier=tier, skipped=skipped):
                self.answer("other", "hobbies", ["astronomy"], tier, skipped)
                self.assertFalse(self.members.eligibility("other", goal["slug"])["eligible"])
        # Private rows contain deliberately invalid JSON: eligibility must not
        # even load/parse them before applying the sharing-tier restriction.
        self.answer("other", "hobbies", ["astronomy"], "ask")
        with self.db.transaction() as connection:
            connection.execute("UPDATE taste_answers SET items_json='private-not-json' WHERE user_id='other' AND topic='hobbies'")
            traced = []
            connection.set_trace_callback(traced.append)
            result = self.members.eligibility("other", goal["slug"], connection=connection)
        self.assertFalse(result["eligible"])
        taste_reads = [query for query in traced if "FROM taste_answers" in query]
        self.assertEqual(len(taste_reads), 1)
        self.assertIn("tier='share' AND skipped=0", taste_reads[0])
        self.assertIn("topic IN", taste_reads[0])
        self.answer("other", "hobbies", ["  Astronomy  "], "share")
        self.assertTrue(self.members.eligibility("other", goal["slug"])["eligible"])

    def test_interest_any_of_and_track_record_fail_closed_until_findings_exist(self):
        goal = self.goal(joining={"interests": [{"topic": "hobbies", "item": "astronomy"}, {"topic": "books", "item": "fictional geometry"}]})
        self.answer("other", "books", ["fictional geometry"], "share")
        self.assertTrue(self.members.eligibility("other", goal["slug"])["eligible"])
        revision = fictional_charter()
        revision["joining"] = {"track_record": 1}
        self.goals.revise_charter("lead", goal["slug"], revision, True)
        result = self.members.eligibility("other", goal["slug"])
        self.assertFalse(result["eligible"])
        self.assertIn("not available yet", result["missing"][0])
        self.assertEqual(set(result), {"eligible", "missing"})

    def test_join_requires_exact_approval_and_validated_approved_note(self):
        goal = self.goal()
        for approval in (None, False, 1, "true"):
            body = {"capabilities": ["mathematics"]}
            if approval is not None:
                body["approved_by_owner"] = approval
            self.error(409, self.members.join, "other", goal["slug"], body)
        for change in ({"capabilities": []}, {"capabilities": ["unknown"]}, {"note": "x" * 301},
                       {"note": "Contact fictional.person@example.test"}, {"unexpected": "field"}):
            body = {"capabilities": ["mathematics"], "approved_by_owner": True, **change}
            self.error(422, self.members.join, "other", goal["slug"], body)
        note = "I would like to check the fictional arithmetic."
        self.assertEqual(self.join(goal["slug"], note=note)["note"], note)

    def test_open_join_is_active_and_ordinary_insert_has_no_recovery_marker(self):
        goal = self.goal()
        before = self.state()
        result = self.join(goal["slug"])
        after = self.state()
        self.assertEqual(result["state"], "active")
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(before["growth_generation"], after["growth_generation"])
        again = self.join(goal["slug"])
        self.assertEqual(again, result)
        self.assertEqual(self.state()["revision"], after["revision"])

    def test_lead_membership_preserves_goal_handle_and_grants_no_work_permission(self):
        goal = self.goal()
        joined = self.join(goal["slug"], "lead")
        self.assertEqual(joined["handle"], goal["lead"]["handle"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_allocations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_sessions").fetchone()[0], 0)

    def test_per_goal_handles_are_random_and_unrelated_to_public_member_id(self):
        self.setting("goals_led_per_member", 2)
        first = self.goal()
        second = self.goal(title="Fictional: prism arithmetic puzzle")
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO member_profiles(user_id,public_id,updated_at) VALUES('other','m_0123456789abcdef',?)", (storage_time(self.now),))
        handles = [self.join(goal["slug"])["handle"] for goal in (first, second)]
        self.assertNotEqual(*handles)
        for handle in handles:
            self.assertRegex(handle, r"^c_[0-9a-f]{8}$")
            self.assertNotIn(handle[2:], "0123456789abcdef")

    def test_lead_approves_pending_view_contains_only_declared_caps_and_approved_note(self):
        goal = self.goal(membership="lead_approves")
        self.answer("other", "hobbies", ["private-interview-canary"], "never")
        note = "I would like to check the fictional proof."
        joined = self.join(goal["slug"], note=note)
        self.assertEqual(joined["state"], "pending")
        pending = self.members.pending("lead", goal["slug"])["members"]
        self.assertEqual(pending, [{"handle": joined["handle"], "capabilities": ["mathematics"], "note": note}])
        self.assertNotIn("private-interview-canary", json.dumps(pending))
        self.assertNotIn("user_id", json.dumps(pending))
        self.error(404, self.members.pending, "other", goal["slug"])

    def test_membership_decision_needs_live_lead_session_and_current_separate_permission(self):
        goal = self.goal(membership="lead_approves")
        joined = self.join(goal["slug"])
        self.error(409, self.members.decide, "lead", goal["slug"], joined["handle"], {"decision": "approve"})
        self.lead_session(goal["slug"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_allocations SET lead_notice_version='old-lead-version' WHERE user_id='lead'")
        self.error(409, self.members.decide, "lead", goal["slug"], joined["handle"], {"decision": "approve"})
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_allocations SET lead_notice_version=? WHERE user_id='lead'", (agent_texts.GOAL_LEAD_VERSION,))
        before = self.state()
        decided = self.members.decide("lead", goal["slug"], joined["handle"], {"decision": "approve"})
        self.assertEqual(decided["member"], {"handle": joined["handle"], "state": "active"})
        self.assertGreater(self.state()["revision"], before["revision"])

    def test_rejected_member_rejoin_requires_fresh_lead_decision_even_for_open_goal(self):
        goal = self.goal(membership="lead_approves")
        joined = self.join(goal["slug"])
        self.lead_session(goal["slug"])
        self.members.decide("lead", goal["slug"], joined["handle"], {"decision": "decline"})
        revised = fictional_charter()
        self.goals.revise_charter("lead", goal["slug"], revised, True)
        reapplied = self.join(goal["slug"])
        self.assertEqual(reapplied["state"], "pending")
        self.assertEqual(reapplied["handle"], joined["handle"])

    def test_approval_rechecks_candidate_criteria_without_disclosing_private_answers(self):
        goal = self.goal(joining={"interests": [{"topic": "hobbies", "item": "astronomy"}]}, membership="lead_approves")
        self.answer("lead", "hobbies", ["astronomy"])
        self.answer("other", "hobbies", ["astronomy"])
        joined = self.join(goal["slug"])
        self.lead_session(goal["slug"])
        self.answer("other", "hobbies", ["private-canary"], "never")
        error = self.error(409, self.members.decide, "lead", goal["slug"], joined["handle"], {"decision": "approve"})
        self.assertNotIn("private-canary", error.detail)
        self.assertNotIn("interests", error.detail)

    def test_member_limit_and_blocked_pair_contributions_follow_goal_rules(self):
        goal = self.goal()
        self.setting("goal_members_max", 2)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO user_blocks(blocker_id,blocked_id,created_at) VALUES('lead','other',?)", (storage_time(self.now),))
        self.assertEqual(self.join(goal["slug"])["state"], "active")
        self.error(409, self.members.join, "third", goal["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})

    def test_membership_record_events_are_counts_without_handles_or_notes(self):
        goal = self.goal(membership="lead_approves")
        joined = self.join(goal["slug"], note="Approved fictional membership note canary.")
        entries = [row for row in self.goals.record(None, goal["slug"])["entries"] if row["kind"] == "membership"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["author"], "system")
        self.assertEqual(entries[0]["content"], {"action": "requested", "active": 1, "pending": 1})
        self.assertNotIn(joined["handle"], json.dumps(entries))
        self.assertNotIn("note canary", json.dumps(entries))

    def test_exports_include_only_owners_membership_and_declaration_updates_are_protected(self):
        goal = self.goal()
        own = self.join(goal["slug"], note="Own fictional approved note.")
        self.join(goal["slug"], "third", note="Peer fictional approved note.")
        before = self.state()
        changed = self.members.join("other", goal["slug"], {"capabilities": ["reviewing"], "note": "Changed own approved note.", "approved_by_owner": True})
        self.assertEqual(changed["member"]["handle"], own["handle"])
        self.assertGreater(self.state()["revision"], before["revision"])
        with self.db.connect() as connection:
            exported = export_memberships(connection, "other")
        self.assertEqual(len(exported), 1)
        self.assertIn("Changed own approved note.", json.dumps(exported))
        self.assertNotIn("Peer fictional approved note.", json.dumps(exported))

    def test_names_require_live_current_permission_and_are_never_in_record_content(self):
        goal = self.goal()
        self.join(goal["slug"])
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO member_profiles(user_id,public_id,first_name,updated_at) VALUES('other','m_0123456789abcdef','Fictional',?)", (storage_time(self.now),))
            connection.execute("UPDATE goal_members SET show_first_name=1 WHERE user_id='other'")
            raw = connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone()
            self.assertNotIn("first_name", display_author(connection, raw, "other", self.now))
        body = {"sessions_per_week": 1, "minutes_per_session": 20, "until": "2026-11-02", "show_first_name": True,
                "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True, "charter_revision": goal["charter_revision"]}
        self.sessions.set_allocation("other", goal["slug"], body)
        with self.db.connect() as connection:
            raw = connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone()
            self.assertEqual(display_author(connection, raw, "other", self.now)["first_name"], "Fictional")
            self.assertNotIn("first_name", display_author(connection, raw, "other", self.now + timedelta(days=2)))
        self.sessions.stop("other", goal["slug"])
        with self.db.connect() as connection:
            raw = connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone()
            self.assertNotIn("first_name", display_author(connection, raw, "other", self.now))
        entries = [row for row in self.goals.record(None, goal["slug"])["entries"] if row["kind"] == "membership"]
        self.assertNotIn('"first_name"', json.dumps(entries))

    def test_feature_off_and_suspended_accounts_cannot_join(self):
        goal = self.goal()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='other'", (storage_time(self.now),))
        self.error(403, self.members.join, "other", goal["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
        self.setting("feature.goals", "off")
        self.error(404, self.members.eligibility, "lead", goal["slug"])
        self.error(404, self.members.join, "lead", goal["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})

    def test_migration_backfills_existing_leads_without_allocations_or_permissions(self):
        old_path = self.db.path.with_name("schema18.db")
        old = Database(old_path, lambda: self.now, migrations=load_migrations()[:18])
        old.migrate()
        from tests_server.test_goals import add_fictional_users
        from connectthebots_server import growth_config
        from connectthebots_server.goals import GoalsService
        add_fictional_users(old, self.now)
        with old.transaction() as connection:
            growth_config.store(connection, "feature.goals", "everyone", self.now)
            # M1 services in the current source create memberships; insert an
            # independent prior-schema goal to test the actual migration data.
            charter = fictional_charter()
            connection.execute("INSERT INTO goals(slug,topic_key,lead_user_id,lead_handle,state,tier,category,membership,charter_json,last_lead_step_at,created_at) "
                "VALUES('fictional-old-goal','fictional old','lead','c_01234567','proposed','standard','mathematics','open',?,?,?)",
                (json.dumps(charter), storage_time(self.now), storage_time(self.now)))
        Database(old_path, lambda: self.now).migrate()
        with old.connect() as connection:
            member = connection.execute("SELECT * FROM goal_members").fetchone()
            self.assertEqual((member["handle"], member["state"], member["capabilities_json"]), ("c_01234567", "active", "[]"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_allocations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_sessions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
