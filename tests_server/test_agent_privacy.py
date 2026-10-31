"""Current-read retries and explicit political consent, using fictional owners."""
import json
import unittest
from unittest.mock import patch

from tests_server import test_agent_growth as growth_tests


@unittest.skipUnless(growth_tests.AVAILABLE, "optional server extra not installed")
class AgentPrivacyTests(unittest.TestCase):
    PEOPLE = growth_tests.AgentApiTests.PEOPLE
    setUp = growth_tests.AgentApiTests.setUp
    call = growth_tests.AgentApiTests.call
    ok = growth_tests.AgentApiTests.ok
    interview = growth_tests.AgentApiTests.interview
    horror_fan = growth_tests.AgentApiTests.horror_fan

    def consent(self, person="ama", choice=True):
        notice = self.ok(person, "GET", "/api/v1/agent/politics-consent")
        return self.ok(person, "POST", "/api/v1/agent/politics-consent",
                       {"consent": choice, "notice_version": notice["notice_version"], "approved_by_owner": True})

    def revision(self, person, topic):
        return self.ok(person, "GET", "/api/v1/agent/interview")["answers"][topic]["revision"]

    def member(self, person):
        return self.ok(person, "GET", "/api/v1/agent/me")["member"]["public_id"]

    def disclose(self, owner, other, topic, revision=None):
        return self.call(owner, "POST", "/api/v1/agent/matches/" + self.member(other) + "/disclose",
                         {"topics": [topic], "approved_by_owner": True,
                          "revisions": {topic: revision or self.revision(owner, topic)}})

    def test_retry_never_replays_a_peer_after_block_or_deletion(self):
        self.horror_fan("ama")
        self.horror_fan("joon")
        headers = {"Idempotency-Key": "fictional-checkin"}
        first = self.call("ama", "POST", "/api/v1/agent/checkin", {}, **headers)
        self.assertEqual(first.status_code, 200)
        member = first.json()["matches"]["new"][0]["member"]["public_id"]
        self.ok("ama", "POST", "/api/v1/agent/block", {"member": member})
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/matches")["matches"], [])
        for erase in (False, True):
            if erase:
                self.app.state.lifecycle.delete_account("joon")
            repeated = self.call("ama", "POST", "/api/v1/agent/checkin", {}, **headers)
            self.assertEqual(repeated.status_code, 200)
            self.assertIs(repeated.json()["already_applied"], True)
            self.assertNotIn(member, repeated.text)
            self.assertNotIn("horror", repeated.text)
        with self.db.connect() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT response_json FROM agent_receipts")], ["{}"])
            self.assertEqual(connection.execute("SELECT checkin_count FROM interview_state WHERE user_id='ama'").fetchone()[0], 1)

    def test_nonce_is_bound_to_method_path_and_exact_body(self):
        body = {"body": "Fictional notes for a sensor sketch.", "approved_by_owner": True}
        headers = {"Idempotency-Key": "fictional-post"}
        self.assertEqual(self.call("ama", "POST", "/api/v1/boards/hobbies/posts", body, **headers).status_code, 201)
        for method, path, value in (("POST", "/api/v1/boards/music/posts", body),
                                    ("PATCH", "/api/v1/agent/profile", body),
                                    ("POST", "/api/v1/boards/hobbies/posts", body | {"body": "A different fictional sketch."})):
            self.assertEqual(self.call("ama", method, path, value, **headers).status_code, 409)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM board_posts").fetchone()[0], 1)

    def test_lost_receipt_after_commit_is_uncertain_and_never_reexecutes(self):
        body = {"body": "Fictional one-time board submission.", "approved_by_owner": True}
        headers = {"Idempotency-Key": "fictional-interruption"}
        with patch.object(self.app.state.agents, "remember", side_effect=RuntimeError("fictional interruption")):
            response = self.call("ama", "POST", "/api/v1/boards/hobbies/posts", body, **headers)
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("fictional interruption", response.text)
        repeated = self.call("ama", "POST", "/api/v1/boards/hobbies/posts", body, **headers)
        self.assertEqual(repeated.status_code, 409)
        self.assertIn("unconfirmed", repeated.json()["error"])
        self.assertNotIn("already_applied", repeated.json())
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM board_posts").fetchone()[0], 1)
            self.assertEqual(tuple(connection.execute("SELECT response_json,status FROM agent_receipts").fetchone()), ("{}", 0))

    def test_changed_ask_answer_revokes_grant_and_stale_approval_refuses(self):
        self.horror_fan("ama")
        self.horror_fan("joon")
        original = self.revision("joon", "books")
        self.assertEqual(self.disclose("joon", "ama", "books", original).status_code, 200)
        # An identical save keeps the exact approval; a changed value does not.
        self.ok("joon", "PUT", "/api/v1/agent/interview/answers", {"answers": [
            {"topic": "books", "items": ["Terry Pratchett"], "tier": "ask"}]})
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_disclosures").fetchone()[0], 1)
        changed = "Fictional newly private book"
        for person, tier in (("ama", "share"), ("joon", "ask")):
            self.ok(person, "PUT", "/api/v1/agent/interview/answers", {"answers": [{"topic": "books", "items": [changed], "tier": tier}]})
        self.assertNotIn(changed, json.dumps(self.ok("ama", "GET", "/api/v1/agent/matches")))
        self.assertEqual(self.disclose("joon", "ama", "books", original).status_code, 409)
        self.assertEqual(self.disclose("joon", "ama", "books").status_code, 200)
        self.assertIn(changed, json.dumps(self.ok("ama", "GET", "/api/v1/agent/matches")))

    def test_every_non_skipped_political_tier_needs_separate_current_consent(self):
        notice = self.ok("ama", "GET", "/api/v1/agent/politics-consent")
        self.assertFalse(notice["active"])
        self.assertIn("stored privately", notice["storage_note"])
        for tier in ("ask", "never", "share"):
            response = self.call("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [
                {"topic": "politics", "items": ["centre"], "tier": tier, "owner_confirmed_share": True}]})
            self.assertEqual(response.status_code, 409)
        self.ok("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [{"topic": "politics", "skipped": True, "tier": "share"}]})
        for fields in ({"consent": True}, {"consent": True, "notice_version": notice["notice_version"]},
                       {"consent": True, "notice_version": "old", "approved_by_owner": True},
                       {"consent": True, "notice_version": notice["notice_version"], "approved_by_owner": 1}):
            self.assertEqual(self.call("ama", "POST", "/api/v1/agent/politics-consent", fields).status_code, 409)
        self.assertTrue(self.consent()["active"])
        for tier in ("ask", "never", "share"):
            self.ok("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [
                {"topic": "politics", "items": ["centre"], "tier": tier, "owner_confirmed_share": True}]})

    def test_reverting_or_reinserting_answer_does_not_reuse_approval_revision(self):
        self.horror_fan("ama")
        self.horror_fan("joon")
        original = self.revision("joon", "books")
        self.assertEqual(self.disclose("joon", "ama", "books", original).status_code, 200)
        for tier in ("never", "ask"):
            self.ok("joon", "PUT", "/api/v1/agent/interview/answers", {"answers": [
                {"topic": "books", "items": ["Terry Pratchett"], "tier": tier}]})
        reverted = self.revision("joon", "books")
        self.assertNotEqual(original, reverted)
        self.assertEqual(self.disclose("joon", "ama", "books", original).status_code, 409)
        self.assertEqual(self.disclose("joon", "ama", "books", reverted).status_code, 200)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM taste_answers WHERE user_id='joon' AND topic='books'")
        self.ok("joon", "PUT", "/api/v1/agent/interview/answers", {"answers": [
            {"topic": "books", "items": ["Terry Pratchett"], "tier": "ask"}]})
        self.assertNotIn(self.revision("joon", "books"), (original, reverted))
        self.assertEqual(self.disclose("joon", "ama", "books", reverted).status_code, 409)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_disclosures WHERE user_id='joon'").fetchone()[0], 0)
        self.assertEqual(self.disclose("joon", "ama", "books").status_code, 200)

    def test_processing_consent_does_not_approve_sharing_and_withdrawal_erases(self):
        self.consent("ama")
        self.consent("joon")
        for person in ("ama", "joon"):
            self.horror_fan(person)
            self.ok(person, "PUT", "/api/v1/agent/interview/answers", {"answers": [
                {"topic": "politics", "items": ["centre"], "tier": "ask"}]})
        self.assertNotIn("politics", json.dumps(self.ok("ama", "GET", "/api/v1/agent/matches")))
        self.assertEqual(self.disclose("ama", "joon", "politics").status_code, 200)
        self.assertEqual(self.disclose("joon", "ama", "politics").status_code, 200)
        self.assertIn("politics", json.dumps(self.ok("ama", "GET", "/api/v1/agent/matches")))
        withdrawn = self.ok("ama", "POST", "/api/v1/agent/politics-consent", {"consent": False})
        self.assertFalse(withdrawn["active"])
        self.assertEqual(withdrawn["decision"]["choice"], "withdrawn")
        self.assertNotIn("politics", self.ok("ama", "GET", "/api/v1/agent/interview")["answers"])
        self.assertNotIn("politics", json.dumps(self.ok("joon", "GET", "/api/v1/agent/matches")))
        self.assertEqual(self.call("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [
            {"topic": "politics", "items": ["centre"], "tier": "never"}]}).status_code, 409)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM taste_disclosures WHERE user_id='ama' AND topic='politics'").fetchone()[0], 0)
            history = self.app.state.agents.taste.export_owner("ama", connection)["politics_consent_decisions"]
        self.assertEqual([item["choice"] for item in history], ["granted", "withdrawn"])
        self.assertTrue(all(item["source"] == "agent_attestation" and item["recorded_at"] for item in history))
        self.app.state.lifecycle.delete_account("ama")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM politics_consent_decisions WHERE user_id='ama'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM politics_consent_decisions WHERE user_id='joon'").fetchone()[0], 1)

    def test_old_version_or_existing_answer_is_never_current_consent(self):
        for person in ("ama", "joon"):
            self.horror_fan(person)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO politics_consent_decisions(user_id,notice_version,choice,recorded_at,source) VALUES('ama','old','granted','fictional','agent_attestation')")
            for person in ("ama", "joon"):
                connection.execute("UPDATE taste_answers SET items_json='[\"centre\"]',tier='share',skipped=0 WHERE user_id=? AND topic='politics'", (person,))
        self.assertFalse(self.ok("ama", "GET", "/api/v1/agent/politics-consent")["active"])
        self.assertFalse(self.ok("joon", "GET", "/api/v1/agent/politics-consent")["active"])
        self.assertNotIn("politics", json.dumps(self.ok("ama", "GET", "/api/v1/agent/matches")))

    def test_same_clock_withdrawal_and_stale_grant_retry_do_not_reenable(self):
        notice = self.ok("ama", "GET", "/api/v1/agent/politics-consent")
        grant = {"consent": True, "notice_version": notice["notice_version"], "approved_by_owner": True}
        headers = {"Idempotency-Key": "fictional-political-grant"}
        self.assertTrue(self.call("ama", "POST", "/api/v1/agent/politics-consent", grant, **headers).json()["active"])
        self.ok("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [
            {"topic": "politics", "items": ["centre"], "tier": "never"}]})
        self.ok("ama", "POST", "/api/v1/agent/politics-consent", {"consent": False})
        replay = self.call("ama", "POST", "/api/v1/agent/politics-consent", grant, **headers)
        self.assertIs(replay.json()["already_applied"], True)
        self.assertNotIn("active", replay.json())
        self.assertFalse(self.ok("ama", "GET", "/api/v1/agent/politics-consent")["active"])
        self.assertNotIn("politics", self.ok("ama", "GET", "/api/v1/agent/interview")["answers"])
        with self.db.connect() as connection:
            rows = connection.execute("SELECT choice,recorded_at FROM politics_consent_decisions WHERE user_id='ama' ORDER BY id").fetchall()
        self.assertEqual([row["choice"] for row in rows], ["granted", "withdrawn"])
        self.assertEqual(rows[0]["recorded_at"], rows[1]["recorded_at"])
