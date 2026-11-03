"""Native owner-only cancellation of pending role choices, including feature off."""
import json
import unittest

from connectthebots_server.agent_common import AgentError
from tests_server import test_goals as cases
from tests_server import test_goal_controls as control_cases


@unittest.skipUnless(cases.SERVER, "optional server extra not installed")
class GoalRoleControlTests(unittest.TestCase):
    def setUp(self):
        self.controls = control_cases.GoalControlTests("runTest")
        self.addCleanup(self.controls.doCleanups)
        self.controls.setUp()
        self.app, self.client, self.db = self.controls.app, self.controls.client, self.controls.db
        self.roles = self.app.state.goal_leadership
        self.work, self.slug = self.controls.work, self.controls.slugs[0]
        self.prepare(self.slug)

    def prepare(self, slug):
        with self.db.connect() as connection:
            handle = connection.execute("SELECT handle FROM goal_members WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND user_id='other'", (slug,)).fetchone()[0]
        self.roles.handover("lead", slug, {"handle": handle, "charter_revision": 1, "approved_by_owner": True})
        preview = self.roles.preview("other", slug)
        self.roles.volunteer("other", slug, {"charter_revision": preview["charter_revision"], "notice_version": preview["notice_version"], "approved_by_owner": True})
        return handle

    def flags(self, user, slug=None):
        return next(goal for goal in self.work.dashboard(user)["goals"] if goal["slug"] == (slug or self.slug))

    def snapshots(self):
        with self.db.connect() as connection:
            return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("goal_lead_approvals", "goal_lead_handovers", "goals", "goal_members", "goal_allocations", "goal_sessions")}

    def path(self, action):
        return f"/app/goals/{self.slug}/lead/" + action

    def assert_assign_refused(self):
        with self.db.connect() as connection:
            handle = connection.execute("SELECT handle FROM goal_members WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND user_id='other'", (self.slug,)).fetchone()[0]
        with self.assertRaises(AgentError) as error:
            self.roles.assign(self.slug, handle, "Fictional operator cannot reuse a cancelled role choice.")
        self.assertEqual(error.exception.status, 409)

    def test_dashboard_buttons_are_only_for_the_owners_pending_choices_with_feature_off(self):
        self.controls.fixture.enable("off")
        for user, role, handover in (("other", True, False), ("lead", False, True)):
            self.controls.use(user)
            own = self.flags(user)
            self.assertIs(own["own_pending_lead_approval"], role)
            self.assertIs(own["own_pending_handover"], handover)
            self.assertFalse(self.flags(user, self.controls.slugs[1])["own_pending_lead_approval"])
            page = self.client.get("/app")
            self.assertEqual(page.status_code, 200, page.text)
            self.assertEqual("Cancel lead role request" in page.text, role)
            self.assertEqual("Cancel handover request" in page.text, handover)
            self.assertEqual("data-goal-lead-withdraw" in page.text, role)
            self.assertEqual("data-goal-handover-cancel" in page.text, handover)
            self.assertIn("Stop working on this goal", page.text)
            self.assertNotIn("data-goal-change", page.text)
            self.assertNotIn("target_user_id", json.dumps(own))
        self.controls.use("third")
        page = self.client.get("/app")
        self.assertNotIn("Cancel lead role request", page.text)
        self.assertNotIn("Cancel handover request", page.text)

    def test_native_role_withdrawal_prevents_assignment_without_stopping_work_or_changing_role(self):
        for user in ("lead", "other"):
            self.controls.grant(user=user)
        sessions = {user: self.work.start(user, self.slug, {}) for user in ("lead", "other")}
        self.controls.fixture.enable("off")
        self.controls.use("other")
        before = self.snapshots()
        response = self.controls.post(self.path("withdraw"))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/app#common-goals")
        after = self.snapshots()
        for table in ("goals", "goal_members", "goal_allocations", "goal_sessions", "goal_lead_handovers"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(after["goal_lead_approvals"][0]["state"], "revoked")
        self.assert_assign_refused()
        self.assertNotIn("Cancel lead role request", self.client.get("/app").text)
        self.controls.fixture.enable()
        for user, session in sessions.items():
            self.assertEqual(self.work.context(user, session["session_id"])["session_id"], session["session_id"])

    def test_native_handover_cancellation_prevents_assignment_and_leaves_target_choice_separate(self):
        self.controls.grant(user="lead")
        session = self.work.start("lead", self.slug, {})
        self.controls.fixture.enable("off")
        self.controls.use("lead")
        before = self.snapshots()
        response = self.controls.post(self.path("handover/cancel"))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/app#common-goals")
        after = self.snapshots()
        for table in ("goals", "goal_members", "goal_allocations", "goal_sessions", "goal_lead_approvals"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(after["goal_lead_handovers"][0]["state"], "revoked")
        self.assert_assign_refused()
        self.assertNotIn("Cancel handover request", self.client.get("/app").text)
        self.assertTrue(self.flags("other")["own_pending_lead_approval"])
        self.controls.fixture.enable()
        self.assertEqual(self.work.context("lead", session["session_id"])["session_id"], session["session_id"])

    def test_foreign_owner_and_forged_forms_cannot_cancel_someone_elses_choice(self):
        before = self.snapshots()
        for user, action in (("other", "withdraw"), ("lead", "handover/cancel")):
            self.controls.use(user)
            path = self.path(action)
            self.assertEqual(self.controls.post(path, csrf="wrong").status_code, 403)
            self.assertEqual(self.controls.post(path, user_id="third").status_code, 400)
            response = self.client.post(path, data={"csrf": self.controls.csrf}, headers={"Origin": "https://foreign.example.test"}, follow_redirects=False)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.client.get(path).status_code, 405)
        self.controls.use("third")
        # The role cancellation names no target owner: another member's request
        # cannot be selected, even when a harmless no-op redirects successfully.
        self.assertEqual(self.controls.post(self.path("withdraw")).status_code, 303)
        self.assertEqual(self.controls.post(self.path("handover/cancel")).status_code, 403)
        self.assertEqual(self.snapshots(), before)
        self.client.cookies.clear()
        for action in ("withdraw", "handover/cancel"):
            response = self.client.post(self.path(action), data={"csrf": "fictional"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/signin")
        self.assertEqual(self.snapshots(), before)

    def test_work_stop_keeps_role_cancellation_controls_and_cancellation_is_idempotent(self):
        self.controls.fixture.enable("off")
        for user, action, flag in (("other", "withdraw", "own_pending_lead_approval"), ("lead", "handover/cancel", "own_pending_handover")):
            self.controls.use(user)
            self.assertEqual(self.controls.post(f"/app/goals/{self.slug}/stop").status_code, 303)
            self.assertTrue(self.flags(user)[flag])
            self.assertEqual(self.controls.post(self.path(action)).status_code, 303)
            self.assertFalse(self.flags(user)[flag])
            before = self.snapshots()
            self.assertEqual(self.controls.post(self.path(action)).status_code, 303)
            self.assertEqual(self.snapshots(), before)

    def test_cancellation_is_scoped_to_the_selected_goal(self):
        other_slug = self.controls.slugs[1]
        self.prepare(other_slug)
        self.controls.fixture.enable("off")
        self.controls.use("other")
        self.assertTrue(self.flags("other")["own_pending_lead_approval"])
        self.assertTrue(self.flags("other", other_slug)["own_pending_lead_approval"])
        self.assertEqual(self.controls.post(self.path("withdraw")).status_code, 303)
        self.assertFalse(self.flags("other")["own_pending_lead_approval"])
        self.assertTrue(self.flags("other", other_slug)["own_pending_lead_approval"])
