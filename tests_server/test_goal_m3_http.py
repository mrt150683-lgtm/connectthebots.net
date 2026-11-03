"""Fictional M3 HTTP, public verification and native withdrawal boundaries."""
import json
import unittest

from connectthebots_server.goal_record import verify_record
from tests_server import test_goals as cases
from tests_server import test_goal_controls as controls


@unittest.skipUnless(cases.SERVER, "optional server extra not installed")
class GoalM3HttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = controls.GoalControlTests("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.app = self.fixture.client, self.fixture.app
        self.db, self.work = self.fixture.db, self.fixture.work
        self.slug = self.fixture.slugs[0]
        self.fixture.grant()
        self.lease = self.work.start("other", self.slug, {})

    def call(self, owner, method, path, body=None, **headers):
        return self.fixture.fixture.call(owner, method, path, body, **headers)

    def body(self):
        return {"task_id": self.lease["task"]["id"], "kind": "proof_step",
                "summary": "Fictional proof: two successive odd counts differ by two.",
                "detail": "Write the counts as 2n+1 and 2(n+1)+1, then subtract.",
                "method": "Expand the second expression and cancel the matching terms.",
                "sources": [], "confidence": "tentative", "unknowns": [], "builds_on": []}

    def publish(self, **headers):
        return self.call("other", "POST", f"/api/v1/goals/sessions/{self.lease['session_id']}/findings", self.body(), **headers)

    def test_finding_route_replay_recovers_without_duplicate_submission(self):
        first = self.publish(**{"Idempotency-Key": "fictional-m3-proof"})
        self.assertEqual(first.status_code, 201, first.text)
        before = self.work.allocation("other", self.slug)["current_session"]
        retry = self.publish(**{"Idempotency-Key": "fictional-m3-proof"})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["already_applied"])
        after = self.work.allocation("other", self.slug)["current_session"]
        self.assertEqual(before, after)
        entries = self.app.state.goals.record("other", self.slug)["entries"]
        self.assertEqual(sum(row["kind"] == "finding" for row in entries), 1)
        path = f"/api/v1/goals/sessions/{self.lease['session_id']}"
        self.assertEqual(self.call("other", "GET", path).status_code, 200)
        self.assertEqual(self.call("lead", "GET", path).status_code, 404)

    def test_public_pagination_heads_and_complete_verification(self):
        self.assertEqual(self.publish().status_code, 201)
        entries, after = [], 0
        for _ in range(30):
            response = self.client.get(f"/goals/{self.slug}/record.jsonl?after={after}&limit=1")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("application/x-ndjson", response.headers["content-type"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            page = [json.loads(line) for line in response.text.splitlines()]
            if not page:
                break
            self.assertIn('rel="next"', response.headers["link"])
            entries.extend(page)
            after = int(response.headers["x-record-next-after"])
        else:
            self.fail("public record cursor did not finish")
        self.assertTrue(verify_record(entries, self.slug)["valid"])
        self.assertTrue(any(row["kind"] == "finding" for row in entries))
        for row in entries:
            self.assertFalse({"user_id", "author_user_id", "session_id", "reviewer_user_id"} & set(row))
        heads = self.client.get("/goals/heads.json").json()["goals"]
        self.assertIn({"slug": self.slug, "seq": entries[-1]["seq"], "entry_sha256": entries[-1]["entry_sha256"]}, heads)
        for query in ("limit=101", "after=-1", "after=9223372036854775808", "after=0&after=1", "owner=other"):
            self.assertEqual(self.client.get(f"/goals/{self.slug}/record.jsonl?{query}").status_code, 422, query)

    def test_native_withdrawal_is_owner_bound_csrf_checked_and_available_when_off(self):
        response = self.publish()
        self.assertEqual(response.status_code, 201, response.text)
        entry = response.json()["entry"]
        path = f"/app/goals/entries/{entry['id']}/withdraw"
        page = self.client.get(path)
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("two successive odd counts", page.text)
        self.fixture.use("lead")
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.fixture.post(path, confirm="withdraw").status_code, 404)
        self.fixture.use("other")
        bad = self.client.post(path, data={"confirm": "withdraw"}, follow_redirects=False)
        self.assertNotIn(bad.status_code, (200, 303))
        self.assertEqual(self.fixture.post(path).status_code, 409)
        self.fixture.fixture.enable("off")
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.fixture.post(path, confirm="withdraw").status_code, 303)
        self.assertEqual(self.client.get(path).status_code, 200)
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry["id"],)).fetchone()
            self.assertIsNone(row["content_json"])
            self.assertEqual(row["status"], "withdrawn")
            self.assertEqual(row["entry_sha256"], entry["entry_sha256"])

    def test_feature_off_hides_new_agent_and_public_routes_before_parsing(self):
        self.fixture.fixture.enable("off")
        sid = self.lease["session_id"]
        routes = [("GET", f"/api/v1/goals/sessions/{sid}", None),
                  *(('POST', f"/api/v1/goals/sessions/{sid}/{suffix}", {}) for suffix in ('findings', 'reviews', 'notes')),
                  ("POST", f"/api/v1/goals/{self.slug}/tasks", {}),
                  ("PATCH", f"/api/v1/goals/{self.slug}/tasks/t_fictional", {}),
                  ("POST", f"/api/v1/goals/{self.slug}/summary", {}),
                  ("DELETE", "/api/v1/goals/entries/e_fictional", None)]
        for method, path, body in routes:
            for owner in (None, "other"):
                self.assertEqual(self.call(owner, method, path, body).status_code, 404, path)
        self.assertEqual(self.client.get(f"/goals/{self.slug}/record.jsonl?after=broken").status_code, 404)
        self.assertEqual(self.client.get("/goals/heads.json?broken=1").status_code, 404)
