"""Owner-bound HTTP permissions and native goal controls, fictional owners only."""
from datetime import timedelta
import json
import re
import unittest

from connectthebots_server import agent_texts, growth_config
from connectthebots_server.sessions import SESSION_COOKIE
from tests_server import test_goals as cases


@unittest.skipUnless(cases.SERVER, "optional server extra not installed")
class GoalControlTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cases.GoalHttpTests("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.enable()
        self.app, self.client, self.db = self.fixture.app, self.fixture.client, self.fixture.db
        self.goals, self.members, self.work = self.app.state.goals, self.app.state.goal_members, self.app.state.goal_work
        with self.db.transaction() as connection:
            growth_config.store(connection, "goals_led_per_member", 2, self.fixture.now)
            self.tokens = {user: self.app.state.auth.sessions.create(connection, user) for user in ("lead", "other", "third")}
        self.slugs = []
        for title in ("Fictional: tidal clock puzzle", "Fictional: paper prism puzzle"):
            goal = self.goals.propose("lead", cases.fictional_charter(title), True)["goal"]
            self.goals.operator_action(goal["slug"], "approve", "Fictional operator reviewed the charter.")
            for user in ("lead", "other"):
                self.members.join(user, goal["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
            self.slugs.append(goal["slug"])
        self.use("other")

    def use(self, user):
        self.user = user
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[user])
        self.csrf = self.app.state.auth.sessions.current(self.tokens[user]).csrf

    def body(self, slug=None, user="other", **changes):
        body = {"sessions_per_week": 3, "minutes_per_session": 20,
                "until": (self.fixture.now.date() + timedelta(days=30)).isoformat(),
                "show_first_name": False, "charter_revision": 1,
                "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True}
        if user == "lead":
            body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
        return {**body, **changes}

    def grant(self, slug=None, user="other"):
        return self.work.set_allocation(user, slug or self.slugs[0], self.body(user=user))

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=False)

    def preview(self, slug=None, **changes):
        slug = slug or self.slugs[0]
        fields = {"sessions_per_week": "2", "minutes_per_session": "10", "until": self.body()["until"], "charter_revision": "1"}
        response = self.post(f"/app/goals/{slug}/allocation/preview", **{**fields, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        token = re.search(r'name="token" value="([^"]+)"', response.text).group(1)
        return token, response

    def confirm(self, token, slug=None, **fields):
        return self.post(f"/app/goals/{slug or self.slugs[0]}/allocation/confirm", token=token, approve="yes", **fields)

    def snapshot(self):
        with self.db.connect() as connection:
            return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("goal_members", "goal_allocations", "goal_sessions")}

    def test_preview_and_exact_confirmation_lower_time_and_end_existing_lease(self):
        self.grant()
        lease = self.work.start("other", self.slugs[0], {})
        token, page = self.preview()
        self.assertIn("up to 2 times a week for up to 10 minutes each", page.text)
        self.assertIn("My first name will not be displayed.", page.text)
        self.assertIn("nothing it writes is advice", page.text)
        self.assertEqual(self.work.allocation("other", self.slugs[0])["allocation"]["minutes_per_session"], 20)
        response = self.confirm(token)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/app#common-goals")
        current = self.work.allocation("other", self.slugs[0])
        self.assertEqual(current["allocation"]["minutes_per_session"], 10)
        self.assertIsNone(current["current_session"])
        with self.db.connect() as connection:
            row = connection.execute("SELECT end_reason,reserved_minutes FROM goal_sessions WHERE id=?", (lease["session_id"],)).fetchone()
        self.assertEqual(row["end_reason"], "allocation_replaced")
        self.assertEqual(row["reserved_minutes"], 20)

    def test_lead_requires_separate_native_confirmation(self):
        self.use("lead")
        token, page = self.preview()
        self.assertIn("Separate lead permission", page.text)
        before = self.snapshot()
        self.assertEqual(self.confirm(token).status_code, 409)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.confirm(token, approve_lead="yes").status_code, 303)
        self.assertEqual(self.work.allocation("lead", self.slugs[0])["allocation"]["lead_notice_version"], agent_texts.GOAL_LEAD_VERSION)

    def test_token_is_owner_goal_content_and_expiry_bound(self):
        token, _ = self.preview()
        before = self.snapshot()
        self.use("lead")
        self.assertEqual(self.confirm(token, approve_lead="yes").status_code, 400)
        self.use("other")
        self.assertEqual(self.confirm(token, self.slugs[1]).status_code, 400)
        changed = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertEqual(self.confirm(changed).status_code, 400)
        self.fixture.now += timedelta(seconds=601)
        self.assertEqual(self.confirm(token).status_code, 400)
        self.assertEqual(before, self.snapshot())

    def test_confirm_is_one_use_and_cannot_reactivate_after_stop(self):
        self.grant()
        token, _ = self.preview()
        self.assertEqual(self.confirm(token).status_code, 303)
        after = self.snapshot()
        self.assertEqual(self.confirm(token).status_code, 409)
        self.assertEqual(after, self.snapshot())
        token, _ = self.preview()
        self.assertEqual(self.post(f"/app/goals/{self.slugs[0]}/stop").status_code, 303)
        stopped = self.snapshot()
        self.assertEqual(self.confirm(token).status_code, 409)
        self.assertEqual(stopped, self.snapshot())

    def test_first_grant_preview_is_invalidated_by_stop_even_without_allocation(self):
        for stop in (f"/app/goals/{self.slugs[0]}/stop", "/app/goals/stop-all"):
            with self.subTest(stop=stop):
                token, _ = self.preview()
                self.assertEqual(self.post(stop).status_code, 303)
                before = self.snapshot()
                self.assertEqual(self.confirm(token).status_code, 409)
                self.assertEqual(before, self.snapshot())

    def test_unsafe_forms_neither_change_permission_nor_select_another_owner(self):
        self.grant()
        before = self.snapshot()
        path = f"/app/goals/{self.slugs[0]}/stop"
        self.assertEqual(self.post(path, csrf="invalid").status_code, 403)
        self.assertEqual(self.post(path, user_id="lead").status_code, 400)
        response = self.client.post(path, data={"csrf": self.csrf}, headers={"Origin": "https://foreign.example.test"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(before, self.snapshot())
        self.use("third")
        response = self.post(path)
        self.assertIn(response.status_code, (303, 404))
        with self.db.connect() as connection:
            row = connection.execute("SELECT stopped_at FROM goal_allocations WHERE user_id='other'").fetchone()
        self.assertIsNone(row["stopped_at"])

    def test_owner_can_stop_all_while_agent_goal_api_is_disabled(self):
        for slug in self.slugs:
            self.grant(slug)
        self.work.start("other", self.slugs[0], {})
        self.fixture.enable("off")
        page = self.client.get("/app")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Stop all goal work", page.text)
        self.assertNotIn("data-goal-change", page.text)
        self.assertEqual(self.fixture.call("other", "GET", f"/api/v1/goals/{self.slugs[0]}/allocation").status_code, 404)
        self.assertEqual(self.post("/app/goals/stop-all").status_code, 303)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_allocations WHERE user_id='other' AND stopped_at IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_sessions WHERE user_id='other' AND end_reason IS NULL").fetchone()[0], 0)

    def test_agent_idempotency_recovery_returns_current_state_without_regrant(self):
        slug = self.slugs[0]
        url = f"/api/v1/goals/{slug}/allocation"
        body = self.body()
        self.assertEqual(self.fixture.call("other", "PUT", url, body, **{"Idempotency-Key": "fictional-allocation"}).status_code, 200)
        start_url = f"/api/v1/goals/{slug}/sessions"
        lease = self.fixture.call("other", "POST", start_url, {}, **{"Idempotency-Key": "fictional-session"})
        self.assertEqual(lease.status_code, 201, lease.text)
        replay = self.fixture.call("other", "POST", start_url, {}, **{"Idempotency-Key": "fictional-session"})
        self.assertEqual(replay.json()["already_applied"], True)
        current = self.fixture.call("other", "GET", url).json()
        self.assertEqual(current["current_session"]["id"], lease.json()["session_id"])
        self.assertEqual(self.fixture.call("other", "DELETE", url).status_code, 200)
        self.assertEqual(self.fixture.call("other", "PUT", url, body, **{"Idempotency-Key": "fictional-allocation"}).json()["already_applied"], True)
        self.assertIsNotNone(self.fixture.call("other", "GET", url).json()["allocation"]["stopped_at"])

    def test_join_and_pending_private_state_recover_after_response_loss(self):
        slug = self.slugs[0]
        url = f"/api/v1/goals/{slug}/join"
        body = {"capabilities": ["mathematics"], "note": "Fictional private note approved for the lead.", "approved_by_owner": True}
        joined = self.fixture.call("third", "POST", url, body, **{"Idempotency-Key": "fictional-join"})
        self.assertEqual(joined.status_code, 201, joined.text)
        self.assertEqual(self.fixture.call("third", "POST", url, body, **{"Idempotency-Key": "fictional-join"}).json()["already_applied"], True)
        detail = self.fixture.call("third", "GET", f"/api/v1/goals/{slug}").json()["goal"]
        self.assertEqual(detail["your_membership"]["state"], "active")
        peer = self.fixture.call("other", "GET", f"/api/v1/goals/{slug}").json()
        self.assertNotIn(body["note"], json.dumps(peer))

    def test_changed_display_name_invalidates_the_exact_statement_shown(self):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO member_profiles(user_id,public_id,first_name,updated_at) VALUES('other',?,?,?)",
                               ("m_" + "0" * 16, "FictionalBefore", self.fixture.now.isoformat()))
        token, page = self.preview(show_first_name="yes")
        self.assertIn("FictionalBefore", page.text)
        with self.db.transaction() as connection:
            connection.execute("UPDATE member_profiles SET first_name='FictionalAfter' WHERE user_id='other'")
        before = self.snapshot()
        self.assertEqual(self.confirm(token).status_code, 409)
        self.assertEqual(before, self.snapshot())
