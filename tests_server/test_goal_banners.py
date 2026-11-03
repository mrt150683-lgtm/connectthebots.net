"""Approved sensitive notices and moderated-charter service boundaries."""
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import patch

from connectthebots_server import agent_texts
from connectthebots_server.agent_common import AgentError
from connectthebots_server.goal_members import GoalMemberService
from connectthebots_server.goal_leadership import GoalLeadershipService
from connectthebots_server.goal_sessions import GoalSessionService
from tests_server.test_goals import GoalFixture, fictional_charter
from tests_server.test_goal_sessions import SessionFixture


class SensitiveServiceFixture(GoalFixture):
    def setUp(self):
        super().setUp()
        self.setting("goals_sensitive_open_max", 1)
        self.members = GoalMemberService(self.db, lambda: self.now, self.goals)
        self.work = GoalSessionService(self.db, lambda: self.now, self.goals, self.members)
        self.members.sessions = self.work
        charter = fictional_charter("Fictional: sensitive banner clock", "animal_health")
        charter["membership"] = "lead_approves"
        self.goal = self.open_goal(charter=charter)
        self.slug = self.goal["slug"]
        snapshot = (Path(__file__).resolve().parents[1] / "tests_server/fixtures/goal_approved_notices.md").read_text(encoding="utf-8")
        self.approved_banner = snapshot.split("### T4. Sensitive-goal banner", 1)[1].split("> ", 1)[1].splitlines()[0]
        self.assertEqual(agent_texts.GOAL_SENSITIVE_BANNER, self.approved_banner)

    def banner(self, result):
        self.assertEqual(result.get("banner"), self.approved_banner, result)
        return result

    def join(self, user):
        return self.members.join(user, self.slug, {"capabilities": ["mathematics"], "approved_by_owner": True})

    def body(self, user="other"):
        body = {"sessions_per_week": 3, "minutes_per_session": 20,
            "until": (self.now.date() + timedelta(days=30)).isoformat(), "show_first_name": False,
            "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True,
            "charter_revision": self.goal["charter_revision"]}
        if user == "lead":
            body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
        return body

    def activate(self):
        self.join("lead")
        pending = self.join("other")
        self.work.set_allocation("lead", self.slug, self.body("lead"))
        self.lead_session = self.work.start("lead", self.slug, {})
        self.members.decide("lead", self.slug, pending["member"]["handle"], {"decision": "approve"})


class SensitiveServiceBannerTests(SensitiveServiceFixture):
    def test_membership_successes_carry_the_exact_approved_banner(self):
        self.banner(self.members.eligibility("other", self.slug))
        self.banner(self.join("lead"))
        other, third = self.banner(self.join("other")), self.banner(self.join("third"))
        self.banner(self.members.pending("lead", self.slug))
        self.work.set_allocation("lead", self.slug, self.body("lead"))
        self.work.start("lead", self.slug, {})
        self.banner(self.members.decide("lead", self.slug, other["member"]["handle"], {"decision": "approve"}))
        self.banner(self.members.decide("lead", self.slug, third["member"]["handle"], {"decision": "decline"}))

    def test_allocation_session_and_stop_successes_carry_the_exact_approved_banner(self):
        self.activate()
        self.banner(self.work.allocation("other", self.slug))
        self.banner(self.work.preview_allocation("other", self.slug, self.body()))
        self.banner(self.work.set_allocation("other", self.slug, self.body()))
        self.banner(self.work.allocation("other", self.slug))
        session = self.banner(self.work.start("other", self.slug, {}))
        self.banner(self.work.context("other", session["session_id"]))
        report = {"minutes_used": 0, "did": "Fictional exact arithmetic was inspected."}
        self.banner(self.work.end("other", session["session_id"], report))
        self.banner(self.work.end("other", session["session_id"], report))
        self.banner(self.work.stop("other", self.slug))
        self.banner(self.work.stop_all("lead"))
        self.banner(self.work.dashboard("other")["goals"][0])

    def test_held_or_hidden_charter_cannot_expose_criteria_title_or_authorize_work(self):
        self.activate()
        pending = self.join("third")
        self.work.set_allocation("other", self.slug, self.body())
        session = self.work.start("other", self.slug, {})
        for visibility in ("held", "hidden"):
            with self.db.transaction() as connection:
                connection.execute("UPDATE goal_entries SET status=? WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND kind='charter'", (visibility, self.slug))
            # Even a raw visibility transition before its lifecycle cancellation
            # cannot expose criteria or authorize work through a stale lease.
            with patch.object(self.members, "_eligibility", side_effect=AssertionError("Private criteria must not be read")):
                operations = (
                    (self.members.eligibility, ("other", self.slug)),
                    (self.join, ("third",)),
                    (self.members.pending, ("lead", self.slug)),
                    (self.members.decide, ("lead", self.slug, pending["member"]["handle"], {"decision": "approve"})),
                    (self.work.allocation, ("other", self.slug)),
                    (self.work.preview_allocation, ("other", self.slug, self.body())),
                    (self.work.set_allocation, ("other", self.slug, self.body())),
                    (self.work.start, ("other", self.slug, {})),
                    (self.work.context, ("other", session["session_id"])),
                )
                for operation, args in operations:
                    with self.subTest(visibility=visibility, operation=operation.__name__):
                        error = self.error(409, operation, *args)
                        self.assertNotIn(self.goal["charter"]["title"], str(error))
            dashboard = self.work.dashboard("other")
            item = dashboard["goals"][0]
            self.assertEqual(item["title"], "Charter unavailable")
            self.assertFalse(item["charter_available"])
            self.assertNotIn(self.goal["charter"]["title"], json.dumps(dashboard))
            self.assertEqual(item["allocation"]["minutes_per_session"], 20)
            self.assertEqual(item["current_session"]["id"], session["session_id"])
            self.assertEqual(self.work.dashboard("lead")["goals"][0]["title"], self.goal["charter"]["title"])

    def test_feature_off_and_hidden_scope_still_allow_owner_stop_controls(self):
        self.activate()
        self.work.set_allocation("other", self.slug, self.body())
        session = self.work.start("other", self.slug, {})
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='hidden' WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND kind='charter'", (self.slug,))
            connection.execute("UPDATE goals SET state='hidden' WHERE slug=?", (self.slug,))
        self.setting("feature.goals", "off")
        dashboard = self.work.dashboard("other")
        self.assertFalse(dashboard["enabled"])
        self.assertEqual(dashboard["goals"][0]["title"], "Charter unavailable")
        self.banner(self.work.stop("other", self.slug))
        self.banner(self.work.stop_all("lead"))
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (session["session_id"],)).fetchone()
            self.assertEqual(row["end_reason"], "owner_stop")
            self.assertEqual(row["reserved_minutes"], 20)
        self.assertFalse(self.work.dashboard("other")["goals"][0]["allocation"]["active"])

    def test_unlisted_proposal_keeps_existing_private_author_read_policy(self):
        # A proposal's author can inspect their own eligibility. It remains
        # unavailable to another owner and cannot grant work before approval.
        charter = fictional_charter("Fictional: private sensitive role", "animal_health")
        proposal = self.propose("third", charter)
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND kind='charter'", (proposal["slug"],))
        self.banner(self.members.eligibility("third", proposal["slug"]))
        self.error(404, self.members.eligibility, "other", proposal["slug"])

    def test_reassigned_lead_cannot_read_original_authors_hidden_charter_title(self):
        self.activate()
        leadership = GoalLeadershipService(self.db, lambda: self.now, self.goals, self.members, self.work)
        with self.db.connect() as connection:
            handle = self.members.member(connection, self.goals._goal(connection, self.slug), "other")["handle"]
        leadership.handover("lead", self.slug, {"handle": handle, "charter_revision": self.goal["charter_revision"], "approved_by_owner": True})
        proposal = leadership.preview("other", self.slug)
        leadership.volunteer("other", self.slug, {"charter_revision": proposal["charter_revision"],
            "notice_version": proposal["notice_version"], "approved_by_owner": True})
        leadership.assign(self.slug, handle, "Fictional assignment for private charter author testing.")
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='hidden' WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND kind='charter'", (self.slug,))
        current = self.work.dashboard("other")["goals"][0]
        self.assertTrue(current["is_lead"])
        self.assertEqual(current["title"], "Charter unavailable")
        self.assertFalse(current["charter_available"])
        original = self.work.dashboard("lead")["goals"][0]
        self.assertFalse(original["is_lead"])
        self.assertEqual(original["title"], self.goal["charter"]["title"])
        for user, readable in (("other", False), ("lead", True)):
            charter = next(entry for entry in self.goals.record(user, self.slug)["entries"] if entry["kind"] == "charter")
            self.assertEqual(charter["content"] is not None, readable)


class StandardServiceBannerTests(SessionFixture):
    def test_standard_membership_and_work_do_not_gain_a_sensitive_label(self):
        self.assertNotIn("banner", self.members.eligibility("other", self.slug))
        self.assertNotIn("banner", self.members.join("other", self.slug, {"capabilities": ["mathematics"], "approved_by_owner": True}))
        self.assertNotIn("banner", self.sessions.preview_allocation("other", self.slug, self.body()))
        self.assertNotIn("banner", self.approve())
        session = self.begin()
        self.assertNotIn("banner", session)
        self.assertNotIn("banner", self.sessions.context("other", session["session_id"]))
        self.assertNotIn("banner", self.finish(session))
        self.assertNotIn("banner", self.sessions.stop("other", self.slug))
        self.assertNotIn("banner", self.sessions.stop_all("other"))
