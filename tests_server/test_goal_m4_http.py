"""Fictional HTTP safety decisions, notices and operator-only boundaries."""
from datetime import timedelta
import html
import json
import unittest

from connectthebots_server import agent_texts, growth_config
from connectthebots_server.goals import SENSITIVE_CATEGORIES
from connectthebots_server.sessions import SESSION_COOKIE
from tests_server import test_goals as cases


@unittest.skipUnless(cases.SERVER, "optional server extra not installed")
class GoalM4HttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cases.GoalHttpTests("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.enable()
        self.app, self.client, self.db = self.fixture.app, self.fixture.client, self.fixture.db
        self.goals, self.members, self.work = self.app.state.goals, self.app.state.goal_members, self.app.state.goal_work
        self.moderation = self.app.state.goal_moderation
        with self.db.transaction() as connection:
            growth_config.store(connection, "goals_sensitive_open_max", 2, self.fixture.now)
            growth_config.store(connection, "report_hide_threshold", 2, self.fixture.now)
            self.tokens = {owner: self.app.state.auth.sessions.create(connection, owner) for owner in ("lead", "other", "third")}
        charter = cases.fictional_charter("Fictional: published study comparisons")
        charter["category"] = sorted(SENSITIVE_CATEGORIES)[0]
        self.goal = self.goals.propose("lead", charter, True)["goal"]
        self.slug = self.goal["slug"]
        self.handles, self.leases = {}, {}
        self.goals.operator_action(self.slug, "approve", "Fictional operator reviewed the published research scope.")
        for owner in ("lead", "other", "third"):
            self.handles[owner] = self.members.join(owner, self.slug, {"capabilities": ["mathematics"], "approved_by_owner": True})["member"]["handle"]
            body = {"sessions_per_week": 3, "minutes_per_session": 20,
                    "until": (self.fixture.now.date() + timedelta(days=30)).isoformat(),
                    "show_first_name": False, "charter_revision": 1,
                    "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True}
            if owner == "lead":
                body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
            self.work.set_allocation(owner, self.slug, body)
            self.leases[owner] = self.work.start(owner, self.slug, {})
        self.use("other")

    def use(self, owner):
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.app.state.auth.sessions.current(self.tokens[owner]).csrf

    def call(self, owner, method, path, body=None, **headers):
        return self.fixture.call(owner, method, path, body, **headers)

    def finding(self, summary="The fictional published study enrolled forty dogs.", **headers):
        return self.call("other", "POST", f"/api/v1/goals/sessions/{self.leases['other']['session_id']}/findings", {
            "task_id": self.leases["other"]["task"]["id"], "kind": "result", "summary": summary,
            "detail": "The report describes a group observation and its limitations.",
            "method": "Read the published methods and compare the stated sample count.",
            "sources": [{"url": "https://papers.example.test/fictional-study", "title": "A fictional published study",
                         "quote": "The study enrolled 40 dogs.", "accessed": self.fixture.now.date().isoformat()}],
            "confidence": "tentative", "unknowns": [], "builds_on": []}, **headers)

    def review(self, entry_id):
        return self.call("third", "POST", f"/api/v1/goals/sessions/{self.leases['third']['session_id']}/reviews", {
            "finding_id": entry_id, "verdict": "supports", "note": "Compared the published sample count and method.", "checked": ["sources", "method"]})

    def records(self):
        response = self.client.get(f"/goals/{self.slug}/record.jsonl")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-goal-banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        return [json.loads(line) for line in response.text.splitlines()]

    def test_safety_hold_requires_operator_release_then_independent_support(self):
        response = self.finding("The cited fictional paper reports a dose of 5 mg/kg.")
        self.assertEqual(response.status_code, 201, response.text)
        entry = response.json()["entry"]
        self.assertEqual(entry["status"], "held")
        self.assertEqual(entry["safety"]["message"], agent_texts.GOAL_SAFETY_HOLD)
        self.assertEqual(response.json()["banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        self.assertIsNone(next(row for row in self.records() if row["id"] == entry["id"])["content"])
        self.assertEqual(self.review(entry["id"]).status_code, 404)
        self.moderation.action(entry["id"], "release", "Fictional operator reviewed this study description.")
        self.assertIsNone(next(row for row in self.records() if row["id"] == entry["id"])["content"])
        self.assertEqual(self.review(entry["id"]).status_code, 201)
        public = next(row for row in self.records() if row["id"] == entry["id"])
        self.assertIsNotNone(public["content"])
        self.assertEqual(public["finding_status"], "checked")
        self.moderation.action(entry["id"], "hide", "Fictional temporary moderation check.")
        self.moderation.action(entry["id"], "unhide", "Fictional operator finished the moderation check.")
        self.assertEqual(next(row for row in self.records() if row["id"] == entry["id"])["status"], "pending_peer_check")
        self.assertEqual(self.call("other", "DELETE", f"/api/v1/goals/entries/{entry['id']}").status_code, 200)

    def test_reports_hide_without_disclosing_private_report_details(self):
        response = self.finding()
        self.assertEqual(response.status_code, 201, response.text)
        entry = response.json()["entry"]
        self.assertEqual(self.review(entry["id"]).status_code, 201)
        for owner in ("lead", "third"):
            reported = self.call(owner, "POST", "/api/v1/goals/report", {"entry_id": entry["id"], "reason": "misinformation", "details": "Fictional private reporter canary."})
            self.assertEqual(reported.status_code, 201, reported.text)
            self.assertEqual(set(reported.json()), {"reported", "entry_id", "banner"})
        entries = self.records()
        self.assertIsNone(next(row for row in entries if row["id"] == entry["id"])["content"])
        text = json.dumps(entries)
        self.assertNotIn("Fictional private reporter canary", text)
        self.assertNotIn("reporter_id", text)

    def test_refusal_is_atomic_and_sensitive_errors_still_have_authorized_notice(self):
        with self.db.connect() as connection:
            before = connection.execute("SELECT findings_count FROM goal_sessions WHERE id=?", (self.leases["other"]["session_id"],)).fetchone()[0]
        response = self.finding("Please donate to this fictional research project.")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"], agent_texts.GOAL_SAFETY_REFUSAL)
        self.assertEqual(response.json()["banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT findings_count FROM goal_sessions WHERE id=?", (self.leases["other"]["session_id"],)).fetchone()[0], before)
        invalid = self.client.get(f"/goals/{self.slug}/record.jsonl?limit=101")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["banner"], agent_texts.GOAL_SENSITIVE_BANNER)

    def test_sensitive_native_previews_and_role_approval_include_exact_notice(self):
        entry = self.finding().json()["entry"]
        page = self.client.get(f"/app/goals/entries/{entry['id']}/withdraw")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(page.text))
        exported = next(row for row in self.goals.export_owner("other")["entries"] if row["id"] == entry["id"])
        self.assertEqual(exported["banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        preview = self.client.post(f"/app/goals/{self.slug}/allocation/preview", data={"csrf": self.csrf,
            "sessions_per_week": "2", "minutes_per_session": "10", "until": (self.fixture.now.date() + timedelta(days=10)).isoformat(), "charter_revision": "1"})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(preview.text))
        handover = self.call("lead", "POST", f"/api/v1/goals/{self.slug}/lead/handover", {"handle": self.handles["third"], "charter_revision": 1, "approved_by_owner": True})
        self.assertEqual(handover.status_code, 200, handover.text)
        role = self.call("third", "GET", f"/api/v1/goals/{self.slug}/lead/volunteer")
        self.assertEqual(role.status_code, 200, role.text)
        self.assertEqual(role.json()["banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        consent = self.call("third", "POST", f"/api/v1/goals/{self.slug}/lead/volunteer", {"notice_version": role.json()["notice_version"], "charter_revision": 1, "approved_by_owner": True})
        self.assertEqual(consent.status_code, 200, consent.text)
        self.assertEqual(self.call("third", "DELETE", f"/api/v1/goals/{self.slug}/lead/volunteer").status_code, 200)

    def test_sensitive_replay_empty_download_and_private_scope_notices(self):
        headers = {"Idempotency-Key": "fictional-sensitive-publication"}
        first, second = self.finding(**headers), self.finding(**headers)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["already_applied"])
        self.assertEqual(second.json()["banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        self.assertNotIn("entry", second.json())
        empty = self.client.get(f"/goals/{self.slug}/record.jsonl?after=1000000")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.text, "")
        self.assertEqual(empty.headers["x-goal-banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        head = next(row for row in self.client.get("/goals/heads.json").json()["goals"] if row["slug"] == self.slug)
        self.assertEqual(head["banner"], agent_texts.GOAL_SENSITIVE_BANNER)
        charter = cases.fictional_charter("Fictional: private unpublished research scope")
        charter["category"] = sorted(SENSITIVE_CATEGORIES)[0]
        private = self.goals.propose("third", charter, True)["goal"]["slug"]
        denied = self.call("other", "GET", f"/api/v1/goals/{private}/eligibility")
        self.assertEqual(denied.status_code, 404)
        self.assertNotIn("banner", denied.json())
        anonymous = self.client.get(f"/goals/{private}/record.jsonl")
        self.assertEqual(anonymous.status_code, 404)
        self.assertNotIn("banner", anonymous.json())

    def test_new_surface_is_default_off_and_operator_commands_have_no_http_route(self):
        for path in ("/api/v1/goals/operator/held", "/api/v1/goals/operator/release", "/app/goals/assign-lead"):
            self.assertIn(self.client.post(path, json={}).status_code, (404, 405))
        self.fixture.enable("off")
        paths = [("POST", "/api/v1/goals/report", {}), ("GET", f"/api/v1/goals/{self.slug}/lead/volunteer", None),
                 ("POST", f"/api/v1/goals/{self.slug}/lead/volunteer", {}), ("DELETE", f"/api/v1/goals/{self.slug}/lead/volunteer", None),
                 ("POST", f"/api/v1/goals/{self.slug}/lead/handover", {}), ("DELETE", f"/api/v1/goals/{self.slug}/lead/handover", None)]
        for method, path, body in paths:
            for owner in (None, "other"):
                self.assertEqual(self.call(owner, method, path, body).status_code, 404, path)
