"""M3 evidence and review acceptance, using fictional owners and sources."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3
from unittest.mock import patch

from connectthebots_server import agent_texts
from connectthebots_server.goal_findings import (FINDING_KINDS, GoalFindingService, entry_readable,
    finding_states, public_entry_content, public_finding_content, review_offers, validate_finding)
from connectthebots_server.goal_members import GoalMemberService
from connectthebots_server.goal_record import append_entry, verify_record
from connectthebots_server.goal_sessions import GoalSessionService
from connectthebots_server.recovery_authority import local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_goals import GoalFixture, fictional_charter


def source(**changes):
    return {"url": "https://fictional.example.test/puzzle", "title": "A fictional clock construction",
            "quote": "The example period is exactly twelve steps.", "accessed": "2026-11-01", **changes}


def payload(task_id="t_fictional", **changes):
    return {"task_id": task_id, "kind": "proof_step", "summary": "A fictional clock repeats in twelve steps.",
            "detail": "List every symbolic state and compare the twelfth state with the first.",
            "method": "Enumerate the twelve states with exact integer arithmetic.", "sources": [],
            "confidence": "tentative", "unknowns": ["Whether a shorter construction exists."], "builds_on": [], **changes}


class GoalFindingTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.members = GoalMemberService(self.db, lambda: self.now, self.goals)
        self.sessions = GoalSessionService(self.db, lambda: self.now, self.goals, self.members)
        self.members.sessions = self.sessions
        self.findings = GoalFindingService(self.db, lambda: self.now, self.goals, self.sessions)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                ("fourth", "fictional-fourth", "fourth@fictional.example.test", "Private fictional fourth",
                 storage_time(self.now - timedelta(days=30)), storage_time(self.now)))

    def work(self, goal, user="lead", *, start=True):
        self.members.join(user, goal["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
        body = {"sessions_per_week": 3, "minutes_per_session": 20, "until": "2026-11-30", "show_first_name": False,
                "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True,
                "charter_revision": goal["charter_revision"]}
        if user == "lead":
            body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
        self.sessions.set_allocation(user, goal["slug"], body)
        return self.sessions.start(user, goal["slug"], {}) if start else None

    def publish(self, work, user="lead", **changes):
        return self.findings.finding(user, work["session_id"], payload(**{"task_id": work["task"]["id"], **changes}))["entry"]

    def check(self, work, entry, user="other", verdict="supports", **changes):
        return self.findings.review(user, work["session_id"], {"finding_id": entry["id"], "verdict": verdict,
            "note": "Repeated the fictional integer construction and compared every step.", "checked": ["method"], **changes})

    def raw(self, entry_id):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry_id,)).fetchone()

    def state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    def counter(self, work, kind="findings_count"):
        with self.db.connect() as connection:
            return connection.execute("SELECT " + kind + " FROM goal_sessions WHERE id=?", (work["session_id"],)).fetchone()[0]

    def offers(self, goal, user="other"):
        with self.db.transaction() as connection:
            row = self.goals._goal(connection, goal["slug"])
            return review_offers(connection, user, row, now=self.now)

    def test_exact_kind_tier_evidence_matrix_without_invented_sources(self):
        external = {"literature_summary", "result", "negative_result"}
        for kind in FINDING_KINDS:
            for tier in ("standard", "sensitive"):
                with self.subTest(kind=kind, tier=tier):
                    needs_source = tier == "sensitive" or kind in external
                    if needs_source:
                        self.error(422, validate_finding, payload(kind=kind), tier)
                        self.error(422, validate_finding, payload(kind=kind, sources=[source(quote="")]), tier)
                    else:
                        self.assertEqual(validate_finding(payload(kind=kind), tier)["sources"], [])
                    self.assertEqual(validate_finding(payload(kind=kind, method="", sources=[source()]), tier)["kind"], kind)
        for kind in ("proof_step", "code", "design", "method"):
            self.error(422, validate_finding, payload(kind=kind, method=""), "standard")
        question = validate_finding(payload(kind="question", method="", unknowns=["Which symbolic state has not been checked?"]), "standard")
        self.assertEqual(question["sources"], [])

    def test_text_and_list_boundaries_and_unknown_keys_are_strict(self):
        for key, length in (("summary", 600), ("detail", 4000), ("method", 1500)):
            self.assertEqual(len(validate_finding(payload(**{key: "x" * length}), "standard")[key]), length)
            self.error(422, validate_finding, payload(**{key: "x" * (length + 1)}), "standard")
        for change in ({"summary": ""}, {"detail": ""}, {"summary": "invisible\u200b text"}, {"unknowns": ["x"] * 6},
                       {"unknowns": ["x" * 301]}, {"unknowns": "unknown"}, {"confidence": "certain"},
                       {"kind": "execute"}, {"unexpected": True}, {"sources": [source()] * 11},
                       {"builds_on": ["e_" + "a" * 64] * 51}, {"builds_on": ["f_old_alias"]}):
            with self.subTest(change=change):
                self.error(422, validate_finding, payload(**change), "standard")
        for key in payload():
            body = payload()
            del body[key]
            self.error(422, validate_finding, body, "standard")
        self.assertEqual(len(validate_finding(payload(unknowns=["x" * 300] * 5), "standard")["unknowns"]), 5)

    def test_sources_https_dates_quotes_and_artifact_hashes(self):
        self.assertEqual(len(validate_finding(payload(sources=[source(quote="q" * 300)] * 10), "standard")["sources"]), 10)
        for change in ({"url": "http://fictional.example.test"}, {"url": "file:///fictional"},
                       {"url": "https://"}, {"url": "https://user:secret@fictional.example.test"},
                       {"url": "https://fictional.example.test:wrong"}, {"url": "https://fictional.example.test/" + "x" * 2048},
                       {"quote": "q" * 301}, {"accessed": "2026-02-30"}, {"accessed": "2026-1-01"},
                       {"accessed": "2026-11-01T00:00:00Z"}, {"title": "t" * 201}, {"unknown": True}):
            with self.subTest(change=change):
                self.error(422, validate_finding, payload(sources=[source(**change)]), "standard")
        artifact = {"url": "https://fictional.example.test/code", "sha256": "A" * 64}
        self.assertEqual(validate_finding(payload(artifact=artifact), "standard")["artifact"]["sha256"], "a" * 64)
        for artifact in ({"url": "http://fictional.example.test", "sha256": "a" * 64},
                         {"url": "https://fictional.example.test", "sha256": "a" * 63},
                         {"url": "https://fictional.example.test", "sha256": "z" * 64},
                         {"url": "https://fictional.example.test"}, None):
            self.error(422, validate_finding, payload(artifact=artifact), "standard")

    def test_contacts_refused_outside_source_and_artifact_fields(self):
        for key in ("summary", "detail", "method"):
            self.error(422, validate_finding, payload(**{key: "Contact fictional.person@example.test"}), "standard")
        self.error(422, validate_finding, payload(unknowns=["Visit https://fictional.example.test"]), "standard")
        quoted = source(quote="The fictional contact is contact@example.test.")
        self.assertEqual(validate_finding(payload(sources=[quoted]), "standard")["sources"][0]["quote"], quoted["quote"])
        self.error(422, validate_finding, payload(detail="Construct fictional weapons for this refusal."), "standard")

    def test_real_finding_uses_existing_hash_id_session_and_task_metadata(self):
        goal = self.open_goal()
        work = self.work(goal)
        before = self.state()
        with patch("urllib.request.urlopen", side_effect=AssertionError("Sources must not be fetched")):
            entry = self.publish(work, sources=[source()])
        self.assertRegex(entry["id"], r"^e_[0-9a-f]{64}$")
        self.assertEqual(entry["kind"], "finding")
        self.assertEqual(entry["status"], "new")
        self.assertEqual(entry["content"]["written_by"], "assistant")
        self.assertNotIn("session_id", entry["content"])
        row = self.raw(entry["id"])
        self.assertEqual(row["session_id"], work["session_id"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT task_id FROM goal_findings WHERE entry_id=?", (entry["id"],)).fetchone()[0], work["task"]["id"])
        self.assertEqual(self.state()["revision"], before["revision"])
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"])
        self.assertEqual(self.counter(work), 1)
        self.assertTrue(verify_record(self.goals.record(None, goal["slug"])["entries"])["valid"])

    def test_invalid_finding_rolls_back_consumption_and_any_partial_entry(self):
        goal = self.open_goal()
        work = self.work(goal)
        before = len(self.goals.record(None, goal["slug"])["entries"])
        for change in ({"summary": "x" * 601}, {"task_id": "t_missing"},
                       {"builds_on": ["e_" + "a" * 64]}, {"kind": "result", "sources": []}):
            self.error(422, self.publish, work, **change)
            self.assertEqual(self.counter(work), 0)
            self.assertEqual(len(self.goals.record(None, goal["slug"])["entries"]), before)

    def test_builds_on_is_existing_same_goal_and_references_are_immutable(self):
        goal = self.open_goal()
        work = self.work(goal)
        first = self.publish(work)
        other_goal = self.open_goal(owner="other", charter=fictional_charter("Fictional: prism arithmetic puzzle"))
        foreign = self.goals.record(None, other_goal["slug"])["entries"][0]["id"]
        self.error(422, self.publish, work, builds_on=[foreign])
        self.error(422, self.publish, work, builds_on=[first["id"], first["id"]])
        second = self.publish(work, builds_on=[first["id"]])
        self.assertEqual(second["refs"], [first["id"]])
        self.assertEqual(second["content"]["builds_on"], [first["id"]])
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as connection:
            connection.execute("UPDATE goal_findings SET task_id='other-task' WHERE entry_id=?", (first["id"],))

    def test_requires_own_current_session_and_grace_has_exact_end(self):
        goal = self.open_goal()
        work = self.work(goal)
        self.error(404, self.findings.finding, "other", work["session_id"], payload(work["task"]["id"]))
        self.now += timedelta(minutes=20)
        self.publish(work)
        self.now += timedelta(minutes=5)
        self.error(409, self.publish, work)
        self.assertEqual(self.counter(work), 1)

    def test_explicit_end_and_owner_stop_cancel_submission_grace(self):
        for action in ("end", "stop"):
            with self.subTest(action=action):
                goal = self.open_goal(owner="lead" if action == "end" else "other",
                    charter=fictional_charter("Fictional: " + action + " clock proof"))
                work = self.work(goal, "third")
                if action == "end":
                    self.sessions.end("third", work["session_id"], {"minutes_used": 0, "did": "Completed fictional comparison."})
                else:
                    self.sessions.stop("third", goal["slug"])
                self.error(409, self.publish, work, "third")

    def test_finding_session_and_goal_weekly_caps_rollback_cleanly(self):
        goal = self.open_goal()
        work = self.work(goal)
        for _ in range(3):
            self.publish(work)
        self.error(429, self.publish, work)
        self.assertEqual(self.counter(work), 3)
        self.setting("goal_findings_per_session", 4)
        with self.db.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM goal_entries").fetchone()[0]
        self.setting("goal_entries_per_goal_per_week", max(10, count))
        while self.counter(work) < 4:
            self.publish(work)
        # A different owner still cannot bypass the shared goal cap.
        other = self.work(goal, "other")
        with self.db.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM goal_entries").fetchone()[0]
        self.setting("goal_entries_per_goal_per_week", 10)
        self.setting("goal_findings_per_session", 10)
        while count < 10:
            self.publish(other, "other")
            count += 1
        old = self.counter(other)
        self.error(429, self.publish, other, "other")
        self.assertEqual(self.counter(other), old)

    def test_review_not_own_not_twice_and_no_account_or_independence_leak(self):
        goal = self.open_goal()
        lead = self.work(goal)
        entry = self.publish(lead)
        self.error(409, self.check, lead, entry, "lead")
        self.assertEqual(self.counter(lead, "reviews_count"), 0)
        other = self.work(goal, "other")
        before = self.state()
        result = self.check(other, entry)
        self.assertEqual(result["finding_status"], "checked")
        self.assertEqual(set(result["entry"]["content"]), {"finding_id", "verdict", "note", "checked", "written_by"})
        self.assertEqual(result["entry"]["refs"], [entry["id"]])
        self.error(409, self.check, other, entry)
        self.assertEqual(self.counter(other, "reviews_count"), 1)
        self.assertEqual(self.state()["revision"], before["revision"])
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"])

    def test_review_validation_rollback_and_caps(self):
        goal = self.open_goal()
        self.setting("goal_findings_per_session", 10)
        lead = self.work(goal)
        entries = [self.publish(lead) for _ in range(6)]
        other = self.work(goal, "other")
        for change in ({"verdict": "certain"}, {"note": "x" * 1501}, {"note": "mail fictional@example.test"},
                       {"checked": []}, {"checked": ["method"] * 2}, {"checked": ["x"] * 6},
                       {"checked": ["x" * 81]}, {"unexpected": True}, {"finding_id": "f_alias"}):
            self.error(422, self.check, other, entries[0], **change)
            self.assertEqual(self.counter(other, "reviews_count"), 0)
        for entry in entries[:5]:
            self.check(other, entry, verdict="could_not_check", checked=[])
        self.error(429, self.check, other, entries[5])
        self.assertEqual(self.counter(other, "reviews_count"), 5)

    def test_complete_ordinary_sessions_findings_reviews_and_end_advance_no_authority(self):
        goal = self.open_goal()
        self.work(goal, start=False)
        self.work(goal, "other", start=False)
        before = self.state()
        lead = self.sessions.start("lead", goal["slug"], {})
        other = self.sessions.start("other", goal["slug"], {})
        entry = self.publish(lead)
        self.check(other, entry)
        for user, work in (("lead", lead), ("other", other)):
            self.sessions.end(user, work["session_id"], {"minutes_used": 0, "did": "Completed the fictional construction check."})
        self.assertEqual(self.state()["revision"], before["revision"])
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"])

    def test_concurrent_duplicate_review_consumes_exactly_one_slot(self):
        goal = self.open_goal()
        entry = self.publish(self.work(goal))
        other = self.work(goal, "other")

        def attempt(_):
            try:
                self.check(other, entry)
                return 200
            except ValueError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, range(2))), [200, 409])
        self.assertEqual(self.counter(other, "reviews_count"), 1)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_review_checks").fetchone()[0], 1)

    def test_referral_reviews_are_recorded_but_support_does_not_count_and_dissent_does(self):
        goal = self.open_goal()
        lead = self.work(goal)
        entry = self.publish(lead)
        other = self.work(goal, "other")
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at) VALUES(?,?,?,?,?,?)",
                ("fictional-link", "lead", "other", "fictional-code", "agent", storage_time(self.now)))
        self.assertEqual(self.offers(goal), [])
        self.assertEqual(self.check(other, entry)["finding_status"], "new")
        second = self.publish(lead)
        self.assertEqual(self.check(other, second, verdict="does_not_support")["finding_status"], "disputed")
        with self.db.connect() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT independent FROM goal_review_checks")], [0, 0])

    def test_blocked_pairs_can_contribute_but_are_not_offered_or_allowed_to_review(self):
        goal = self.open_goal()
        lead = self.work(goal)
        entry = self.publish(lead)
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO user_blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)", ("other", "lead", storage_time(self.now)))
        other = self.work(goal, "other")
        self.assertEqual(self.offers(goal), [])
        self.error(404, self.check, other, entry)
        self.assertEqual(self.counter(other, "reviews_count"), 0)
        self.publish(other, "other")

    def test_sensitive_first_check_is_private_until_independent_support_without_status_mutation(self):
        self.setting("goals_sensitive_open_max", 1)
        goal = self.open_goal(charter=fictional_charter(category="animal_health"))
        lead = self.work(goal)
        entry = self.publish(lead, sources=[source()])
        other = self.work(goal, "other")
        self.assertEqual(entry["status"], "pending_peer_check")
        self.assertEqual([item["id"] for item in self.offers(goal)], [entry["id"]])
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry["id"],)).fetchone()
            self.assertFalse(entry_readable(connection, row, goal, "other"))
            self.assertTrue(entry_readable(connection, row, goal, "lead"))
            self.assertTrue(entry_readable(connection, row, goal, operator=True))
            self.assertFalse(public_finding_content(connection, row))
        before = self.state()
        self.assertEqual(self.check(other, entry)["finding_status"], "checked")
        self.assertEqual(self.raw(entry["id"])["status"], "pending_peer_check")
        with self.db.connect() as connection:
            self.assertTrue(public_finding_content(connection, self.raw(entry["id"])))
        self.assertEqual(self.state()["revision"], before["revision"])
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"])
        third = self.work(goal, "third")
        self.assertEqual(self.check(third, entry, "third", "does_not_support")["finding_status"], "disputed")
        with self.db.connect() as connection:
            self.assertTrue(public_finding_content(connection, self.raw(entry["id"])))

    def test_sensitive_linked_review_cannot_read_pending_then_visible_review_still_not_independent(self):
        self.setting("goals_sensitive_open_max", 1)
        goal = self.open_goal(charter=fictional_charter(category="animal_health"))
        entry = self.publish(self.work(goal), sources=[source()])
        other = self.work(goal, "other")
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at) VALUES(?,?,?,?,?,?)",
                ("fictional-link", "other", "lead", "fictional-code", "agent", storage_time(self.now)))
        self.error(404, self.check, other, entry)
        self.assertEqual(self.offers(goal), [])
        self.assertEqual(self.counter(other, "reviews_count"), 0)
        third = self.work(goal, "third")
        self.check(third, entry, "third")
        self.assertEqual(self.check(other, entry)["finding_status"], "checked")

    def test_pending_sensitive_dissent_review_does_not_expose_private_finding_through_its_note(self):
        self.setting("goals_sensitive_open_max", 1)
        goal = self.open_goal(charter=fictional_charter(category="animal_health"))
        entry = self.publish(self.work(goal), sources=[source()])
        other = self.work(goal, "other")
        dissent = self.check(other, entry, verdict="does_not_support", note="A fictional private canary about the pending claim.")["entry"]
        with self.db.connect() as connection:
            row = self.raw(dissent["id"])
            self.assertFalse(entry_readable(connection, row, goal))
            self.assertTrue(entry_readable(connection, row, goal, "other"))
            self.assertTrue(entry_readable(connection, row, goal, operator=True))
        public = self.goals.record(None, goal["slug"])["entries"]
        self.assertIsNone(next(row for row in public if row["id"] == dissent["id"])["content"])
        third = self.work(goal, "third")
        self.check(third, entry, "third")
        with self.db.connect() as connection:
            self.assertTrue(entry_readable(connection, self.raw(dissent["id"]), goal))
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE id=?", (entry["id"],))
        public = self.goals.record(None, goal["slug"])["entries"]
        self.assertIsNone(next(row for row in public if row["id"] == dissent["id"])["content"])
        with self.db.connect() as connection:
            self.assertFalse(entry_readable(connection, self.raw(dissent["id"]), goal))

    def test_safety_hold_hidden_withdrawn_override_all_checks_and_offers(self):
        goal = self.open_goal()
        lead = self.work(goal)
        other = self.work(goal, "other")
        for status in ("held", "hidden", "withdrawn"):
            entry = self.publish(lead)
            self.check(other, entry)
            with self.db.transaction() as connection:
                connection.execute("UPDATE goal_entries SET status=? WHERE id=?", (status, entry["id"]))
            with self.db.connect() as connection:
                row = self.raw(entry["id"])
                self.assertEqual(finding_states(connection, row["goal_id"])[entry["id"]], status)
                self.assertFalse(public_finding_content(connection, row))
                self.assertFalse(entry_readable(connection, row, goal, "other"))
                self.assertEqual(entry_readable(connection, row, goal, "lead"), status != "withdrawn")
            self.assertEqual(self.offers(goal, "third"), [])
            self.error(404, self.check, other, entry)

    def test_held_ancestor_redacts_transitive_findings_reviews_and_offers_but_keeps_authored_work(self):
        goal = self.open_goal()
        lead = self.work(goal)
        first = self.publish(lead, summary="A fictional ancestor claim.")
        second = self.publish(lead, builds_on=[first["id"]], summary="A fictional descendant carrying the ancestor claim.")
        third = self.publish(lead, builds_on=[second["id"]], summary="A fictional transitive descendant.")
        other = self.work(goal, "other")
        check = self.check(other, third)["entry"]
        reviewer = self.work(goal, "third")
        self.assertEqual({row["id"] for row in self.offers(goal, "third")}, {first["id"], second["id"], third["id"]})
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE id=?", (first["id"],))
        public = {row["id"]: row for row in self.goals.record(None, goal["slug"])["entries"]}
        for entry in (first, second, third, check):
            self.assertIsNone(public[entry["id"]]["content"])
        self.assertEqual(self.offers(goal, "third"), [])
        self.error(404, self.check, reviewer, second, "third")
        # Another author's withdrawal/hold never erases this stored prose.
        self.assertIsNotNone(self.raw(second["id"])["content_json"])
        self.assertIsNotNone(self.goals.owned_entry("lead", second["id"])["content"])
        with self.db.connect() as connection:
            self.assertTrue(entry_readable(connection, self.raw(third["id"]), goal, operator=True))
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='visible' WHERE id=?", (first["id"],))
        public = {row["id"]: row for row in self.goals.record(None, goal["slug"])["entries"]}
        self.assertIsNotNone(public[third["id"]]["content"])

    def test_pending_review_offer_cannot_release_a_private_cited_ancestor(self):
        self.setting("goals_sensitive_open_max", 1)
        goal = self.open_goal(charter=fictional_charter(category="animal_health"))
        lead = self.work(goal)
        first = self.publish(lead, sources=[source()])
        second = self.publish(lead, sources=[source()], builds_on=[first["id"]])
        other = self.work(goal, "other")
        self.assertEqual([row["id"] for row in self.offers(goal)], [first["id"]])
        self.error(404, self.check, other, second)
        self.check(other, first)
        self.assertEqual([row["id"] for row in self.offers(goal)], [second["id"]])
        self.check(other, second)

    def test_public_dependency_traversal_handles_chain_longer_than_python_recursion_limit(self):
        goal = self.open_goal()
        with self.db.transaction() as connection:
            row = self.goals._goal(connection, goal["slug"])
            previous = None
            for index in range(1100):
                entry = append_entry(connection, row, "finding", row["lead_handle"], {"summary": "Fictional chain step " + str(index)},
                    self.now, refs=[previous] if previous else [], author_user_id="lead")
                previous = entry["id"]
            tail = connection.execute("SELECT * FROM goal_entries WHERE id=?", (previous,)).fetchone()
            self.assertTrue(public_entry_content(connection, tail))
            first = connection.execute("SELECT id FROM goal_entries WHERE goal_id=? AND kind='finding' ORDER BY seq LIMIT 1", (row["id"],)).fetchone()[0]
            connection.execute("UPDATE goal_entries SET status='held' WHERE id=?", (first,))
            self.assertFalse(public_entry_content(connection, tail))

    def test_offers_oldest_first_skip_prior_and_two_checks_but_not_failed_attempts(self):
        goal = self.open_goal()
        lead = self.work(goal)
        entries = [self.publish(lead, summary="Fictional construction " + str(n)) for n in range(3)]
        other, third = self.work(goal, "other"), self.work(goal, "third")
        self.work(goal, "fourth")
        self.assertEqual([row["id"] for row in self.offers(goal)], [entry["id"] for entry in entries])
        self.check(other, entries[0], verdict="could_not_check", checked=[])
        self.check(third, entries[0], "third", "could_not_check", checked=[])
        self.check(other, entries[1])
        self.check(third, entries[1], "third", "needs_change")
        self.assertEqual([row["id"] for row in self.offers(goal, "fourth")], [entries[0]["id"], entries[2]["id"]])
        self.assertEqual([row["id"] for row in self.offers(goal)], [entries[2]["id"]])
        self.assertEqual(self.offers(goal, "lead"), [])
        for offer in self.offers(goal, "fourth"):
            self.assertEqual(set(offer["display_author"]), {"handle"})
            self.assertNotIn("independent", offer)

    def test_checked_later_finding_resolves_dissent_and_later_dissent_reopens_it(self):
        goal = self.open_goal()
        lead = self.work(goal)
        original = self.publish(lead)
        other = self.work(goal, "other")
        self.check(other, original, verdict="does_not_support")
        answer = self.publish(lead, builds_on=[original["id"]])
        self.check(other, answer)
        with self.db.connect() as connection:
            states = finding_states(connection, self.raw(original["id"])["goal_id"])
        self.assertEqual(states[original["id"]], "new")
        self.assertEqual(states[answer["id"]], "checked")
        third = self.work(goal, "third")
        self.check(third, original, "third", "does_not_support")
        with self.db.connect() as connection:
            self.assertEqual(finding_states(connection, self.raw(original["id"])["goal_id"])[original["id"]], "disputed")

    def test_withdrawn_review_removes_support_and_deduplication_survives_erasure(self):
        self.setting("goals_sensitive_open_max", 1)
        goal = self.open_goal(charter=fictional_charter(category="animal_health"))
        entry = self.publish(self.work(goal), sources=[source()])
        other = self.work(goal, "other")
        review = self.check(other, entry)["entry"]
        before = self.state()
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='withdrawn',content_json=NULL WHERE id=?", (review["id"],))
        self.assertGreater(self.state()["revision"], before["revision"])
        with self.db.connect() as connection:
            self.assertFalse(public_finding_content(connection, self.raw(entry["id"])))
        self.error(409, self.check, other, entry)
        self.assertEqual(self.counter(other, "reviews_count"), 1)

    def test_account_deletion_unlinks_private_review_metadata_and_preserves_chain(self):
        goal = self.open_goal()
        entry = self.publish(self.work(goal))
        other = self.work(goal, "other")
        review = self.check(other, entry)["entry"]
        before = self.state()
        with self.db.transaction() as connection:
            self.goals.erase_owner("other", connection=connection)
            connection.execute("DELETE FROM users WHERE id='other'")
        with self.db.connect() as connection:
            meta = connection.execute("SELECT * FROM goal_review_checks WHERE entry_id=?", (review["id"],)).fetchone()
            self.assertIsNone(meta["reviewer_user_id"])
            self.assertEqual(meta["reviewer_handle"], review["author"])
            self.assertEqual(meta["independent"], 1)
            self.assertIsNone(self.raw(review["id"])["content_json"])
        self.assertGreater(self.state()["revision"], before["revision"])
        self.assertTrue(verify_record(self.goals.record(None, goal["slug"])["entries"])["valid"])

    def test_feature_off_suspended_owner_and_changed_charter_block_work(self):
        goal = self.open_goal()
        work = self.work(goal)
        self.setting("feature.goals", "off")
        self.error(404, self.publish, work)
        self.setting("feature.goals", "everyone")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='lead'", (storage_time(self.now),))
        self.error(403, self.publish, work)
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=NULL WHERE id='lead'")
        self.goals.revise_charter("lead", goal["slug"], fictional_charter(), True)
        self.error(409, self.publish, work)
        self.assertEqual(self.counter(work), 0)
