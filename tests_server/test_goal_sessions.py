"""Hand-timed goal allocations, reservation budgets, grace and revocation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
import threading
from unittest.mock import patch

from connectthebots_server import agent_texts
from connectthebots_server.agent_common import AgentError
from connectthebots_server.goal_members import GoalMemberService
from connectthebots_server.goal_sessions import GoalSessionService, export_owner
from connectthebots_server.recovery_authority import local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_goals import GoalFixture, fictional_charter


class SessionFixture(GoalFixture):
    def setUp(self):
        super().setUp()
        self.members = GoalMemberService(self.db, lambda: self.now, self.goals)
        self.sessions = GoalSessionService(self.db, lambda: self.now, self.goals, self.members)
        self.members.sessions = self.sessions
        self.goal = self.open_goal()
        self.slug = self.goal["slug"]
        for user in ("lead", "other", "third"):
            self.members.join(user, self.slug, {"capabilities": ["mathematics"], "approved_by_owner": True})

    def body(self, user="other", slug=None, **changes):
        slug = slug or self.slug
        goal = self.goals.detail(user, slug)["goal"]
        body = {"sessions_per_week": 3, "minutes_per_session": 20,
                "until": (self.now.date() + timedelta(days=30)).isoformat(), "show_first_name": False,
                "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True,
                "charter_revision": goal["charter_revision"]}
        with self.db.connect() as connection:
            lead = connection.execute("SELECT lead_user_id FROM goals WHERE slug=?", (slug,)).fetchone()[0]
        if lead == user:
            body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
        return body | changes

    def approve(self, user="other", slug=None, **changes):
        return self.sessions.set_allocation(user, slug or self.slug, self.body(user, slug, **changes))

    def begin(self, user="other", slug=None):
        return self.sessions.start(user, slug or self.slug, {})

    def finish(self, session, user="other", minutes=0):
        return self.sessions.end(user, session["session_id"], {"minutes_used": minutes, "did": "Fictional exact arithmetic was checked."})

    def gate(self, session, kind="work", consume=False, user="other"):
        with self.db.transaction() as connection:
            return self.sessions.require_session(connection, user, session["session_id"], kind, consume)


class AllocationTests(SessionFixture):
    def test_allocation_requires_exact_current_notice_approval_and_scope(self):
        for changes in ({"approved_by_owner": False}, {"approved_by_owner": 1},
                        {"notice_version": "old-goal-notice"}, {"charter_revision": 0}, {"charter_revision": True}):
            with self.subTest(changes=changes):
                self.error(409, self.sessions.set_allocation, "other", self.slug, self.body(**changes))
        body = self.body()
        del body["charter_revision"]
        self.error(409, self.sessions.set_allocation, "other", self.slug, body)
        self.assertIsNone(self.sessions.allocation("other", self.slug)["allocation"])

    def test_lead_permission_is_separate_and_cannot_be_inferred_from_membership(self):
        body = self.body("lead")
        del body["lead_approved_by_owner"]
        self.error(409, self.sessions.set_allocation, "lead", self.slug, body)
        self.error(409, self.sessions.set_allocation, "lead", self.slug,
                   self.body("lead", lead_notice_version="old-lead-notice"))
        self.error(422, self.sessions.set_allocation, "other", self.slug,
                   self.body(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True))
        self.approve("lead")
        started = self.begin("lead")
        with self.db.transaction() as connection:
            lease = self.sessions.require_lead_session(connection, "lead", self.goal_id())
            self.assertEqual(lease["id"], started["session_id"])
        self.sessions.stop("lead", self.slug)
        with self.db.transaction() as connection, self.assertRaises(AgentError):
            self.sessions.require_lead_session(connection, "lead", self.goal_id())

    def goal_id(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT id FROM goals WHERE slug=?", (self.slug,)).fetchone()[0]

    def test_ranges_dates_and_unknown_fields_are_rejected(self):
        for changes in ({"sessions_per_week": 0}, {"sessions_per_week": 8}, {"sessions_per_week": True},
                        {"minutes_per_session": 4}, {"minutes_per_session": 61}, {"minutes_per_session": 5.0},
                        {"until": (self.now.date() - timedelta(days=1)).isoformat()},
                        {"until": (self.now.date() + timedelta(days=91)).isoformat()},
                        {"until": "2026-11-01T12:00:00Z"}, {"until": "2026-02-30"},
                        {"show_first_name": 1}, {"user_id": "third"}):
            with self.subTest(changes=changes):
                self.error(422, self.sessions.set_allocation, "other", self.slug, self.body(**changes))
        result = self.approve(until=(self.now.date() + timedelta(days=90)).isoformat())
        self.assertEqual(result["allocation"]["until"], (self.now.date() + timedelta(days=90)).isoformat())

    def test_aggregate_allocation_minutes_across_goals_and_replacement_exclusion(self):
        self.setting("goals_led_per_member", 2)
        second = self.open_goal(charter=fictional_charter("Fictional: prism arithmetic puzzle"))
        self.members.join("other", second["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
        self.setting("goal_weekly_minutes_total_max", 100)
        self.approve(sessions_per_week=3, minutes_per_session=20)
        self.error(422, self.sessions.set_allocation, "other", second["slug"], self.body(slug=second["slug"], sessions_per_week=3))
        self.approve(sessions_per_week=2)
        self.approve(slug=second["slug"], sessions_per_week=3)
        self.approve(sessions_per_week=2)

    def test_preview_renders_exact_consent_and_never_creates_permission(self):
        preview = self.sessions.preview_allocation("other", self.slug, self.body())
        self.assertIn('up to 3 times a week for up to 20 minutes each', preview["statement"])
        self.assertIn("My first name will not be displayed.", preview["statement"])
        self.assertNotIn("{", preview["statement"])
        self.assertIsNone(preview["lead_statement"])
        self.assertIsNone(preview["expected_approval_id"])
        self.assertIsNone(self.sessions.allocation("other", self.slug)["allocation"])
        self.assertIsNotNone(self.sessions.preview_allocation("lead", self.slug, self.body("lead"))["lead_statement"])

    def test_first_name_is_separate_opt_in_and_stop_clears_display(self):
        self.error(409, self.sessions.set_allocation, "other", self.slug, self.body(show_first_name=True))
        from connectthebots_server.agent_common import public_id
        with self.db.transaction() as connection:
            public_id(connection, "other", self.now)
            connection.execute("UPDATE member_profiles SET first_name='Fictional' WHERE user_id='other'")
        result = self.approve(show_first_name=True)
        self.assertIn("I also choose to display my first name, Fictional,", result["statement"])
        preview = self.sessions.preview_allocation("other", self.slug, self.body(show_first_name=True))
        with self.db.transaction() as connection:
            connection.execute("UPDATE member_profiles SET first_name='Revised' WHERE user_id='other'")
        self.error(409, self.sessions.set_allocation, "other", self.slug, self.body(show_first_name=True),
                   expected_approval_id=preview["expected_approval_id"], expected_statement=preview["statement"])
        self.sessions.stop("other", self.slug)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT show_first_name FROM goal_members WHERE user_id='other'").fetchone()[0], 0)

    def test_signed_preview_state_cannot_resurrect_stopped_or_replaced_permission(self):
        first = self.sessions.preview_allocation("other", self.slug, self.body())
        self.sessions.set_allocation("other", self.slug, self.body(), expected_approval_id=first["expected_approval_id"])
        self.error(409, self.sessions.set_allocation, "other", self.slug, self.body(), expected_approval_id=None)
        preview = self.sessions.preview_allocation("other", self.slug, self.body())
        self.sessions.stop("other", self.slug)
        self.error(409, self.sessions.set_allocation, "other", self.slug, self.body(), expected_approval_id=preview["expected_approval_id"])

    def test_stop_fences_first_grant_preview_even_without_an_allocation_row(self):
        for operation in ("stop", "stop_all"):
            with self.subTest(operation=operation):
                preview = self.sessions.preview_allocation("other", self.slug, self.body())
                self.assertIsNone(preview["expected_approval_id"])
                with self.db.connect() as connection:
                    before = local_state(connection)
                if operation == "stop":
                    self.sessions.stop("other", self.slug)
                else:
                    self.sessions.stop_all("other")
                self.error(409, self.sessions.set_allocation, "other", self.slug, self.body(),
                    expected_approval_id=preview["expected_approval_id"],
                    expected_work_generation=preview["expected_work_generation"],
                    expected_statement=preview["statement"])
                with self.db.connect() as connection:
                    self.assertGreater(local_state(connection)["revision"], before["revision"])
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_allocations WHERE user_id='other'").fetchone()[0], 0)
        fresh = self.sessions.preview_allocation("other", self.slug, self.body())
        self.sessions.set_allocation("other", self.slug, self.body(), expected_approval_id=None,
            expected_work_generation=fresh["expected_work_generation"], expected_statement=fresh["statement"])

    def test_private_allocation_recovery_survives_lost_eligibility_but_new_work_does_not(self):
        self.approve()
        started = self.begin()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='other'", (storage_time(self.now),))
        recovered = self.sessions.allocation("other", self.slug)
        self.assertEqual(recovered["current_session"]["id"], started["session_id"])
        self.assertEqual(recovered["allocation"]["minutes_per_session"], 20)
        self.assertIsNone(self.sessions.allocation("third", self.slug)["allocation"])
        self.error(409, self.sessions.set_allocation, "other", self.slug, self.body())
        self.error(409, self.gate, started, "finding")

    def test_replacing_allowance_ends_lease_and_grace_and_rotates_approval(self):
        self.approve()
        started = self.begin()
        self.now += timedelta(minutes=20)
        self.approve(minutes_per_session=10)
        self.error(409, self.gate, started, "finding")
        self.assertIsNone(self.sessions.allocation("other", self.slug)["current_session"])


class SessionWindowTests(SessionFixture):
    def test_start_needs_allocation_and_returns_real_matching_task_not_fake_reviews(self):
        self.error(409, self.begin)
        self.approve()
        started = self.begin()
        self.assertEqual(started["task"]["capabilities"], ["mathematics"])
        self.assertEqual(started["reviews"], [])
        self.assertIsNone(started["summary"])
        self.assertEqual(_time(started["ends_at"]) - self.now, timedelta(minutes=20))
        self.assertEqual(self.sessions.allocation("other", self.slug)["current_session"]["id"], started["session_id"])
        self.error(404, self.gate, started, user="third")

    def test_expiry_work_grace_submission_and_exact_boundary(self):
        self.approve()
        started = self.begin()
        self.now += timedelta(minutes=20)
        self.error(409, self.gate, started, "work")
        self.gate(started, "finding", True)
        state = self.sessions.allocation("other", self.slug)["current_session"]
        self.assertEqual(state["ended_at"], started["ends_at"])
        self.assertIsNone(state["minutes_used"])
        self.assertEqual(state["end_reason"], "expired")
        self.error(409, self.begin)
        self.now += timedelta(minutes=5)
        self.error(409, self.gate, started, "review")
        self.assertIsNone(self.sessions.allocation("other", self.slug)["current_session"])
        self.begin()

    def test_permission_midnight_shortens_lease_and_cancels_grace(self):
        self.now = self.now.replace(hour=23, minute=58)
        self.approve(until=self.now.date().isoformat())
        started = self.begin()
        self.assertEqual(_time(started["ends_at"]) - self.now, timedelta(minutes=2))
        self.now += timedelta(minutes=2)
        self.error(409, self.gate, started, "finding")
        self.error(409, self.begin)
        self.assertEqual(self.sessions.dashboard("other")["goals"][0]["usage"]["reserved_minutes"], 20)

    def test_rolling_week_count_boundary_and_no_refund_for_zero_reported_time(self):
        self.approve(sessions_per_week=1)
        started = self.begin()
        self.finish(started)
        self.now += timedelta(days=7, microseconds=-1)
        error = self.error(429, self.begin)
        self.assertEqual(error.retry_after, 1)
        self.now += timedelta(microseconds=1)
        self.begin()

    def test_aggregate_reserved_minutes_survive_allowance_replacement_and_stop(self):
        self.setting("goal_weekly_minutes_total_max", 40)
        self.approve(sessions_per_week=2, minutes_per_session=20)
        first = self.begin()
        self.finish(first)
        self.now += timedelta(minutes=1)
        second = self.begin()
        self.finish(second)
        self.sessions.stop("other", self.slug)
        self.approve(sessions_per_week=3, minutes_per_session=10)
        error = self.error(429, self.begin)
        self.assertEqual(error.retry_after, 7 * 24 * 60 * 60 - 60)
        self.assertEqual(self.sessions.dashboard("other")["goals"][0]["usage"]["reserved_minutes"], 40)

    def test_one_owner_slot_is_atomic_across_concurrent_starts(self):
        self.approve()
        barrier = threading.Barrier(2)
        def begin():
            barrier.wait(timeout=5)
            try:
                return self.begin()["session_id"]
            except AgentError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: begin(), range(2)))
        self.assertEqual(sum(isinstance(item, str) for item in results), 1)
        self.assertEqual(results.count(409), 1)

    def test_findings_reviews_caps_are_separate_and_transactional(self):
        self.approve()
        started = self.begin()
        try:
            with self.db.transaction() as connection:
                self.sessions.require_session(connection, "other", started["session_id"], "finding", True)
                raise RuntimeError("Fictional later submission failure")
        except RuntimeError:
            pass
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT findings_count FROM goal_sessions WHERE id=?", (started["session_id"],)).fetchone()[0], 0)
        for _ in range(3):
            self.gate(started, "finding", True)
        self.error(429, self.gate, started, "finding", True)
        for _ in range(5):
            self.gate(started, "review", True)
        self.error(429, self.gate, started, "review", True)
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (started["session_id"],)).fetchone()
            self.assertIsNone(row["did"])
            self.assertEqual((row["findings_count"], row["reviews_count"]), (3, 5))

    def test_grace_slot_applies_across_different_goals(self):
        self.setting("goals_led_per_member", 2)
        second = self.open_goal(charter=fictional_charter("Fictional: prism arithmetic puzzle"))
        self.members.join("other", second["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
        self.approve()
        self.approve(slug=second["slug"])
        started = self.begin()
        self.now += timedelta(minutes=21)
        self.error(409, self.begin, slug=second["slug"])
        self.sessions.stop("other", self.slug)
        self.begin(slug=second["slug"])

    def test_current_account_membership_eligibility_and_site_limits_are_rechecked(self):
        self.approve()
        started = self.begin()
        self.setting("goal_session_minutes_max", 10)
        self.error(409, self.gate, started, "finding")
        self.setting("goal_session_minutes_max", 60)
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='other'", (storage_time(self.now),))
        self.error(409, self.gate, started, "finding")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='other'", (storage_time(self.now - timedelta(days=30)),))
            connection.execute("UPDATE goal_members SET state='removed' WHERE user_id='other'")
        self.error(403, self.gate, started, "finding")
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_members SET state='active' WHERE user_id='other'")
            connection.execute("UPDATE users SET suspended_at=? WHERE id='other'", (storage_time(self.now),))
        self.error(403, self.gate, started, "finding")

    def test_dashboard_first_read_pauses_inactive_goal_and_cancels_work_at_boundary(self):
        self.approve()
        self.now += timedelta(days=22)
        dashboard = self.sessions.dashboard("other")
        self.assertEqual(dashboard["goals"][0]["state"], "paused")
        # Put the fictional lead ten minutes before the inactivity boundary;
        # an existing contributor's lease crosses it while the dashboard is
        # the first goal read after the boundary.
        with self.db.transaction() as connection:
            connection.execute("UPDATE goals SET state='open',last_lead_step_at=? WHERE slug=?",
                (storage_time(self.now - timedelta(days=20, hours=23, minutes=50)), self.slug))
        started = self.begin()
        self.now += timedelta(minutes=11)
        dashboard = self.sessions.dashboard("other")
        self.assertEqual(dashboard["goals"][0]["state"], "paused")
        self.assertIsNone(dashboard["goals"][0]["current_session"])
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (started["session_id"],)).fetchone()
            self.assertIsNotNone(row["ended_at"])
            self.assertLessEqual(row["grace_ends_at"], storage_time(self.now))
    def test_end_bounds_no_peer_end_and_explicit_end_cancels_grace(self):
        self.approve()
        started = self.begin()
        self.error(404, self.finish, started, user="third")
        for minutes in (-1, 21, True, 5.0):
            self.error(422, self.finish, started, minutes=minutes)
        result = self.finish(started, minutes=5)
        self.assertEqual(result["session"]["minutes_used"], 5)
        self.error(409, self.gate, started, "finding")
        self.assertEqual(self.finish(started, minutes=5), result)
        self.error(409, self.finish, started, minutes=6)

    def test_stop_and_stop_all_work_when_flag_off_and_cancel_grace(self):
        self.approve()
        started = self.begin()
        self.now += timedelta(minutes=21)
        self.setting("feature.goals", "off")
        dashboard = self.sessions.dashboard("other")
        self.assertFalse(dashboard["enabled"])
        self.assertEqual(dashboard["active_count"], 1)
        self.sessions.stop_all("other")
        self.setting("feature.goals", "everyone")
        self.error(409, self.gate, started, "finding")
        self.error(409, self.begin)
        self.assertFalse(self.sessions.dashboard("other")["goals"][0]["allocation"]["active"])

    def test_paused_goal_expired_permission_and_changed_notice_or_scope_refuse_work(self):
        self.approve()
        started = self.begin()
        with patch.object(agent_texts, "GOAL_CONTRIBUTION_VERSION", "fictional-new-version"):
            self.error(409, self.gate, started)
        revised = fictional_charter()
        revised["aim"] = "A wholly revised fictional scope needs a fresh allocation approval."
        self.goals.revise_charter("lead", self.slug, revised, True)
        self.error(409, self.gate, started)
        self.error(409, self.begin)

    def test_full_ordinary_session_does_not_advance_recovery_authority_but_stop_does(self):
        self.approve()
        with self.db.connect() as connection:
            before = local_state(connection)
        started = self.begin()
        self.gate(started, "finding", True)
        self.now += timedelta(minutes=20)
        self.gate(started, "review", True)
        self.finish(started, minutes=20)
        with self.db.connect() as connection:
            self.assertEqual(local_state(connection), before)
        self.sessions.stop("other", self.slug)
        with self.db.connect() as connection:
            self.assertGreater(local_state(connection)["revision"], before["revision"])
            self.assertFalse(export_owner(connection, "other", self.now)["allocations"][0]["active"])


def _time(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
