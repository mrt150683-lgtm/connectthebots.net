"""M4 report privacy, reversible visibility and protected recovery transitions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading
from unittest.mock import patch

from connectthebots_server.goal_findings import GoalFindingService, finding_states, review_offers
from connectthebots_server.goal_moderation import GoalModerationService, REASONS, THRESHOLD_REASON, remember_hold
from connectthebots_server.goal_record import verify_record
from connectthebots_server.goal_tasks import GoalTaskService
from connectthebots_server.recovery_authority import AuthorityError, RecoveryAuthority, local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_goal_findings import payload
from tests_server.test_goal_sessions import SessionFixture
from tests_server.test_goals import fictional_charter
from tests_server.test_offsite_recovery import MemoryStorage


class ModerationFixture(SessionFixture):
    def setUp(self):
        super().setUp()
        self.moderation = GoalModerationService(self.db, lambda: self.now, self.goals)
        self.findings = GoalFindingService(self.db, lambda: self.now, self.goals, self.sessions)
        self.tasks = GoalTaskService(self.db, lambda: self.now, self.goals, self.sessions)
        with self.db.transaction() as connection:
            for user in ("fourth", "fifth", "sixth", "seventh"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (user, "fictional-sub-" + user, user + "@fictional.example.test", "Private fictional " + user,
                     storage_time(self.now - timedelta(days=30)), storage_time(self.now)))
                connection.execute("INSERT INTO early_access_grants(user_id,source,reference,granted_at,expires_at) VALUES(?,?,?,?,?)",
                    (user, "referral_perk", "fictional-moderation", storage_time(self.now), storage_time(self.now + timedelta(days=30))))

    def append(self, kind="note", *, user="lead", status="visible", content=None, slug=None, reasons=None, refs=()):
        slug = slug or self.slug
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection, slug)
            member = self.members.member(connection, goal, user)
            if content is None:
                content = {"text": "A fictional private-content canary for moderation testing.", "written_by": "assistant"}
            entry = self.goals._append(connection, goal, kind, member["handle"], content, self.now,
                                       author_user_id=user, status=status, refs=refs)
            if kind == "finding":
                source = connection.execute("SELECT id FROM goal_entries WHERE goal_id=? AND kind='step' AND seq<? "
                    "AND json_extract(content_json,'$.task_id')=? ORDER BY seq DESC LIMIT 1",
                    (goal["id"], entry["seq"], content["task_id"])).fetchone()
                connection.execute("INSERT INTO goal_findings(entry_id,task_id,task_entry_id) VALUES(?,?,?)",
                                   (entry["id"], content["task_id"], source["id"] if source else None))
            if reasons is not None:
                remember_hold(connection, entry["id"], reasons)
            return entry

    def finding(self, *, status="visible", reasons=None, user="lead"):
        task = self.goals.detail(user, self.slug)["goal"]["open_tasks"][0]
        return self.append("finding", user=user, status=status, reasons=reasons, content=payload(task["id"]))

    def report(self, entry, user="other", **changes):
        return self.moderation.report(user, {"entry_id": entry["id"], "reason": "misinformation",
            "details": "A fictional private reporter canary; never publish this text.", **changes})

    def raw(self, entry):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry["id"],)).fetchone()

    def state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    def count_reports(self, entry):
        with self.db.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM goal_reports WHERE entry_id=?", (entry["id"],)).fetchone()[0]

    def public(self, entry):
        return next(item for item in self.goals.public_record(self.slug)["entries"] if item["id"] == entry["id"])

    def action(self, entry, action):
        return self.moderation.action(entry["id"], action, "The operator reviewed the fictional research entry.")

    def enroll(self):
        self.storage = MemoryStorage()
        self.authority = RecoveryAuthority(self.storage, b"f" * 32, "ctb-" + "e" * 32 + "/")
        self.authority.enroll(self.db)


class GoalReportTests(ModerationFixture):
    def test_distinct_reporters_hide_at_threshold_without_publishing_report_data(self):
        entry = self.append()
        before = self.state()
        for user in ("other", "third"):
            ack = self.report(entry, user)
            self.assertEqual(ack, {"reported": True, "entry_id": entry["id"]})
            self.assertEqual(self.raw(entry)["status"], "visible")
            self.assertEqual(self.state(), before)
        # Fourth is not a member. Public reporting does not require joining.
        self.report(entry, "fourth", reason="privacy")
        self.assertEqual(self.raw(entry)["status"], "hidden")
        self.assertIsNone(self.public(entry)["content"])
        self.assertIsNotNone(self.raw(entry)["content_json"])
        self.assertEqual(self.raw(entry)["entry_sha256"], entry["entry_sha256"])
        record = self.goals.public_record(self.slug)["entries"]
        event = record[-1]["content"]
        self.assertEqual(event["reason"], THRESHOLD_REASON)
        self.assertEqual(event["source"], "report_threshold")
        self.assertEqual(event["reports"], 3)
        serialized = json.dumps(record)
        for private in ("reporter canary", "reporter_id", "fourth@", '"reason": "misinformation"'):
            self.assertNotIn(private, serialized)
        self.assertTrue(verify_record(record)["valid"])
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"] + 1)

    def test_duplicate_report_never_counts_twice_or_rewrites_original_details(self):
        entry = self.append()
        self.report(entry)
        before = self.state()
        self.report(entry, reason="copyright", details="A later fictional detail must not rewrite the first report.")
        self.assertEqual(self.count_reports(entry), 1)
        self.assertEqual(self.state(), before)
        own = self.goals.export_owner("other")["reports"]
        self.assertEqual(own[0]["reason"], "misinformation")
        self.assertIn("private reporter canary", own[0]["details"])
        self.assertEqual(self.goals.export_owner("third")["reports"], [])
        self.report(entry, "third")
        self.assertEqual(self.raw(entry)["status"], "visible")

    def test_operator_review_resolves_old_reports_and_only_new_distinct_reports_can_hide_again(self):
        entry = self.append()
        for user in ("other", "third", "fourth"):
            self.report(entry, user)
        self.action(entry, "unhide")
        before = self.state()
        # Keep exactly the same clock value: a timestamp cutoff would lose
        # new reports at this boundary, whereas explicit per-row review does not.
        for user in ("other", "third", "fourth"):
            self.report(entry, user)
        self.assertEqual(self.count_reports(entry), 3)
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.assertEqual(self.state(), before)
        for user in ("fifth", "sixth"):
            self.report(entry, user)
            self.assertEqual(self.raw(entry)["status"], "visible")
        self.report(entry, "seventh")
        self.assertEqual(self.raw(entry)["status"], "hidden")
        rows = self.moderation.reports()["reports"]
        reviewed = [row for row in rows if row["reviewed_at"] is not None]
        pending = [row for row in rows if row["reviewed_at"] is None]
        self.assertEqual({row["reporter_id"] for row in reviewed}, {"other", "third", "fourth"})
        self.assertEqual({row["reporter_id"] for row in pending}, {"fifth", "sixth", "seventh"})
        events = [item for item in self.goals.public_record(self.slug)["entries"] if item["kind"] == "operator" and
                  item["content"].get("source") == "report_threshold"]
        self.assertEqual(len(events), 2)
        self.assertEqual([item["content"]["reports"] for item in events], [3, 3])

    def test_reason_menu_details_and_body_are_closed_and_never_echo_rejected_data(self):
        self.assertTrue({"spam", "pressure_off_platform", "medical_claim", "misinformation", "dangerous", "copyright"}.issubset(REASONS))
        entry = self.append()
        for change in ({"reason": "unknown"}, {"reason": []}, {"details": None}, {"details": "x" * 1001},
                       {"details": "Fictional\u200bprivate"}, {"unexpected": "field"}):
            with self.subTest(change=change):
                self.error(422, self.report, entry, **change)
        for entry_id in (None, {}, "e_invalid", "e_" + "a" * 64):
            self.error(404, self.moderation.report, "other", {"entry_id": entry_id, "reason": "privacy"})
        self.assertEqual(self.count_reports(entry), 0)

    def test_only_current_public_content_can_be_reported_even_by_its_author(self):
        for status in ("held", "pending_peer_check", "hidden", "withdrawn"):
            entry = self.finding(status=status)
            with self.subTest(status=status):
                self.error(404, self.report, entry)
                self.error(404, self.report, entry, user="lead")
                self.assertEqual(self.count_reports(entry), 0)
        entry = self.append()
        self.error(404, self.report, entry, user="lead")
        self.setting("goals_led_per_member", 2)
        private = self.propose(charter=fictional_charter("Fictional: private moderation proposal"))
        target = self.goals.record("lead", private["slug"])["entries"][0]
        self.error(404, self.report, target)

    def test_suspended_or_missing_owners_and_feature_off_cannot_report(self):
        entry = self.append()
        self.error(401, self.report, entry, user="missing")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='other'", (storage_time(self.now),))
        self.error(403, self.report, entry)
        self.setting("feature.goals", "off")
        self.error(404, self.moderation.report, "third", {})
        self.assertEqual(self.count_reports(entry), 0)

    def test_suspended_reporters_do_not_count_toward_later_threshold(self):
        entry = self.append()
        self.report(entry, "other")
        self.report(entry, "third")
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='other'", (storage_time(self.now),))
        self.report(entry, "fourth")
        self.assertEqual(self.count_reports(entry), 3)
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.report(entry, "fifth")
        self.assertEqual(self.raw(entry)["status"], "hidden")
        self.assertEqual(self.goals.public_record(self.slug)["entries"][-1]["content"]["reports"], 3)

    def test_expired_feature_access_does_not_count_and_cannot_submit(self):
        entry = self.append()
        self.report(entry, "other")
        self.report(entry, "third")
        self.setting("feature.goals", "early_access")
        with self.db.transaction() as connection:
            connection.execute("UPDATE early_access_grants SET expires_at=? WHERE user_id='other'", (storage_time(self.now),))
        self.error(404, self.report, entry, user="other")
        self.report(entry, "fourth")
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.report(entry, "fifth")
        self.assertEqual(self.raw(entry)["status"], "hidden")

    def test_reported_pending_finding_preserves_peer_check_state_on_unhide(self):
        entry = self.finding(status="pending_peer_check")
        self.approve("other")
        session = self.begin("other")
        self.findings.review("other", session["session_id"], {"finding_id": entry["id"], "verdict": "supports",
            "note": "The fictional arithmetic was independently repeated.", "checked": ["method"]})
        self.assertIsNotNone(self.public(entry)["content"])
        for user in ("third", "fourth", "fifth"):
            self.report(entry, user)
        self.assertEqual(self.raw(entry)["status"], "hidden")
        self.action(entry, "unhide")
        self.assertEqual(self.raw(entry)["status"], "pending_peer_check")
        self.assertIsNotNone(self.public(entry)["content"])

    def test_report_ack_in_sensitive_goal_has_banner_without_reporter_or_content(self):
        self.setting("goals_led_per_member", 2)
        self.setting("goals_sensitive_open_max", 1)
        goal = self.open_goal(charter=fictional_charter("Fictional: animal study index", "animal_health"))
        entry = self.append(slug=goal["slug"])
        result = self.report(entry)
        self.assertEqual(set(result), {"reported", "entry_id", "banner"})
        self.assertIn("Not medical", result["banner"])

    def test_reports_queue_is_private_oldest_first_and_export_is_owner_scoped(self):
        first, second = self.append(), self.append()
        self.report(first, "other")
        self.now += timedelta(seconds=1)
        self.report(second, "third", details="Second private fictional report.")
        self.setting("feature.goals", "off")
        queue = self.moderation.reports()["reports"]
        self.assertEqual([row["reporter_id"] for row in queue], ["other", "third"])
        self.assertEqual([row["entry_id"] for row in queue], [first["id"], second["id"]])
        self.assertIn("private reporter canary", queue[0]["details"])
        self.assertIsNotNone(queue[0]["entry"]["content"])
        own = self.goals.export_owner("other")["reports"]
        self.assertEqual(len(own), 1)
        self.assertNotIn("reporter_id", own[0])
        self.assertNotIn("Second private fictional report", json.dumps(own))

    def test_concurrent_distinct_threshold_crossings_append_one_hide_event(self):
        entry = self.append()
        self.report(entry, "other")
        barrier = threading.Barrier(2)
        def submit(user):
            barrier.wait(timeout=5)
            return self.report(entry, user)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ("third", "fourth")))
        self.assertTrue(all(result["reported"] for result in results))
        self.assertEqual(self.raw(entry)["status"], "hidden")
        events = [item for item in self.goals.public_record(self.slug)["entries"] if item["kind"] == "operator" and
                  item["content"].get("source") == "report_threshold"]
        self.assertEqual(len(events), 1)


class GoalModerationActionTests(ModerationFixture):
    def test_generated_numeric_task_identifiers_are_not_screened_as_phone_numbers(self):
        self.setting("goals_led_per_member", 2)
        with patch("connectthebots_server.goals.secrets.token_hex", side_effect=lambda size: "1" * (size * 2)):
            goal = self.propose(charter=fictional_charter("Fictional: numeric metadata period"))
        self.assertEqual(goal["open_tasks"][0]["id"], "t_" + "1" * 24)

    def test_hiding_current_charter_pauses_scope_and_cancels_lease_without_automatic_resume(self):
        self.approve("other")
        work = self.begin("other")
        entry = next(item for item in self.goals.public_record(self.slug)["entries"] if item["kind"] == "charter")
        self.action(entry, "hide")
        public = self.goals.detail(None, self.slug)["goal"]
        self.assertEqual(public["state"], "paused")
        self.assertIsNone(public["charter"])
        self.assertEqual(public["open_tasks"], [])
        self.error(409, self.sessions.context, "other", work["session_id"])
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_sessions WHERE id=?", (work["session_id"],)).fetchone()
            self.assertEqual(row["end_reason"], "goal_paused")
            self.assertLessEqual(row["grace_ends_at"], storage_time(self.now))
        self.action(entry, "unhide")
        public = self.goals.detail(None, self.slug)["goal"]
        self.assertEqual(public["state"], "paused")
        self.assertIsNotNone(public["charter"])
        self.error(409, self.sessions.context, "other", work["session_id"])

    def test_report_threshold_hiding_current_charter_also_cancels_work(self):
        self.approve("other")
        work = self.begin("other")
        entry = next(item for item in self.goals.public_record(self.slug)["entries"] if item["kind"] == "charter")
        for user in ("other", "third", "fourth"):
            self.report(entry, user)
        public = self.goals.detail(None, self.slug)["goal"]
        self.assertEqual(public["state"], "paused")
        self.assertIsNone(public["charter"])
        self.error(409, self.sessions.context, "other", work["session_id"])

    def test_hiding_pending_revision_preserves_current_accepted_scope(self):
        revised = fictional_charter(category="animal_health")
        revised["aim"] = "Compare fictional published animal study methods without claiming treatment effects."
        self.goals.revise_charter("lead", self.slug, revised, True)
        with self.db.connect() as connection:
            pending = connection.execute("SELECT e.* FROM goal_entries e JOIN goals g ON g.pending_entry_id=e.id WHERE g.slug=?", (self.slug,)).fetchone()
        self.action(dict(pending), "hide")
        public = self.goals.detail(None, self.slug)["goal"]
        self.assertEqual(public["charter"]["category"], "mathematics")
        self.assertEqual(public["charter_revision"], 1)
        self.action(dict(pending), "unhide")
        self.assertEqual(self.raw(dict(pending))["status"], "held")
        self.error(409, self.action, dict(pending), "release")

    def test_held_queue_is_oldest_first_with_closed_codes_and_legacy_charter_fallback(self):
        note = self.append(status="held", reasons=["sensitive_claim"])
        self.now += timedelta(seconds=1)
        finding = self.finding(status="held", reasons=["sensitive_dose"])
        self.now += timedelta(seconds=1)
        charter = self.append("charter", status="held", content={"revision": 2, "charter": fictional_charter()})
        self.setting("feature.goals", "off")
        queue = self.moderation.held()["entries"]
        self.assertEqual([item["id"] for item in queue], [note["id"], finding["id"], charter["id"]])
        self.assertEqual([item["reasons"] for item in queue], [["sensitive_claim"], ["sensitive_dose"], ["charter_review"]])
        self.assertTrue(all(item["content"] is not None for item in queue))

    def test_hide_unhide_restores_exact_prior_status_including_safety_hold(self):
        for status in ("visible", "pending_peer_check", "held"):
            entry = self.finding(status=status, reasons=["sensitive_claim"] if status == "held" else None)
            with self.subTest(status=status):
                self.action(entry, "hide")
                self.assertEqual(self.raw(entry)["status"], "hidden")
                self.assertIsNone(self.public(entry)["content"])
                self.action(entry, "unhide")
                self.assertEqual(self.raw(entry)["status"], status)
                self.assertEqual(self.raw(entry)["entry_sha256"], entry["entry_sha256"])
                if status == "held":
                    self.assertIsNone(self.public(entry)["content"])
                    self.assertEqual(self.moderation.held()["entries"][0]["reasons"], ["sensitive_claim"])

    def test_release_finding_still_needs_first_independent_support(self):
        entry = self.finding(status="held", reasons=["sensitive_instruction"])
        self.action(entry, "release")
        self.assertEqual(self.raw(entry)["status"], "pending_peer_check")
        self.assertIsNone(self.public(entry)["content"])
        self.approve("other")
        work = self.begin("other")
        self.assertIn(entry["id"], {item["id"] for item in work["reviews"]})
        self.findings.review("other", work["session_id"], {"finding_id": entry["id"], "verdict": "supports",
            "note": "The fictional arithmetic was independently repeated.", "checked": ["method"]})
        self.assertIsNotNone(self.public(entry)["content"])

    def test_support_cannot_release_a_safety_hold_or_offer_it_to_reviewers(self):
        entry = self.finding(status="held", reasons=["sensitive_dose"])
        self.approve("other")
        work = self.begin("other")
        self.assertNotIn(entry["id"], {item["id"] for item in work["reviews"]})
        self.error(404, self.findings.review, "other", work["session_id"], {"finding_id": entry["id"], "verdict": "supports",
            "note": "A fictional blind support must not release the safety hold.", "checked": ["method"]})
        self.assertEqual(self.raw(entry)["status"], "held")
        self.assertIsNone(self.public(entry)["content"])

    def test_release_other_work_restores_visible_and_requires_a_public_reason(self):
        entry = self.append(status="held", reasons=["sensitive_claim"])
        for reason in (None, "", "tiny", "x" * 1001, "Invalid\u200btext"):
            self.error(422, self.moderation.action, entry["id"], "release", reason)
        self.error(422, self.moderation.action, entry["id"], "invented", "A fictional public reason.")
        self.action(entry, "release")
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.assertIsNotNone(self.public(entry)["content"])
        event = self.goals.public_record(self.slug)["entries"][-1]["content"]
        self.assertEqual(event["action"], "release")
        self.assertNotIn("reporter_id", event)

    def test_held_charter_release_never_bypasses_charter_review(self):
        entry = self.append("charter", status="held", content={"revision": 2, "charter": fictional_charter()})
        self.assertEqual(self.moderation.held()["entries"][0]["reasons"], ["charter_review"])
        self.error(409, self.action, entry, "release")
        self.action(entry, "hide")
        self.action(entry, "unhide")
        self.assertEqual(self.raw(entry)["status"], "held")
        self.error(409, self.action, entry, "release")

    def test_wrong_transition_unknown_prior_state_and_erased_content_never_restore(self):
        entry = self.append()
        self.error(409, self.action, entry, "release")
        self.error(409, self.action, entry, "unhide")
        self.action(entry, "hide")
        self.error(409, self.action, entry, "hide")
        self.error(409, self.action, entry, "release")
        legacy = self.append(status="hidden")
        self.error(409, self.action, legacy, "unhide")
        self.goals.withdrawal("lead", entry["id"])
        for action in ("hide", "unhide", "release"):
            self.error(409, self.action, entry, action)
        self.assertIsNone(self.raw(entry)["content_json"])
        self.assertEqual(self.raw(entry)["entry_sha256"], entry["entry_sha256"])

    def test_account_deletion_cannot_be_undone_by_unhide_or_release(self):
        entry = self.append(user="other")
        self.action(entry, "hide")
        with self.db.transaction() as connection:
            self.goals.erase_owner("other", connection=connection)
            connection.execute("DELETE FROM users WHERE id='other'")
        for action in ("unhide", "release"):
            self.error(409, self.action, entry, action)
        self.assertIsNone(self.raw(entry)["content_json"])
        self.assertIsNone(self.raw(entry)["author_user_id"])
        self.assertTrue(verify_record(self.goals.public_record(self.slug)["entries"])["valid"])

    def test_operator_queues_and_actions_work_when_feature_off_but_recovery_hold_refuses_mutation(self):
        entry = self.append(status="held", reasons=["sensitive_claim"])
        self.setting("feature.goals", "off")
        self.assertEqual(self.moderation.held()["entries"][0]["id"], entry["id"])
        self.action(entry, "hide")
        self.action(entry, "unhide")
        self.assertEqual(self.raw(entry)["status"], "held")
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        self.error(503, self.action, entry, "release")
        self.assertEqual(self.raw(entry)["status"], "held")

    def test_hidden_source_redacts_summary_task_record_and_review_offers_then_unhide_restores(self):
        self.approve("lead")
        lead = self.begin("lead")
        entry = self.finding(user="other")
        summary = self.tasks.summary("lead", self.slug, {"session_id": lead["session_id"],
            "text": "The fictional cited arithmetic result was independently described.", "finding_ids": [entry["id"]], "disputed": []})["summary"]
        changed = fictional_charter()["tasks"][0] | {"question": entry["content"]["detail"]}
        task = self.tasks.update("lead", self.slug, lead["task"]["id"], {"session_id": lead["session_id"], "task": changed,
            "reason": "The cited fictional result refines the task.", "finding_ids": [entry["id"]]})["entry"]
        self.action(entry, "hide")
        context = self.sessions.context("lead", lead["session_id"])
        self.assertIsNone(context["summary"])
        self.assertIsNone(context["task"])
        self.assertNotIn(entry["id"], {item["id"] for item in context["reviews"]})
        record = {item["id"]: item for item in self.goals.public_record(self.slug)["entries"]}
        for entry_id in (entry["id"], summary["id"], task["id"]):
            self.assertIsNone(record[entry_id]["content"])
        self.action(entry, "unhide")
        context = self.sessions.context("lead", lead["session_id"])
        self.assertEqual(context["summary"]["id"], summary["id"])
        self.assertEqual(context["task"]["question"], changed["question"])
        self.assertTrue(verify_record(self.goals.public_record(self.slug)["entries"])["valid"])


class GoalModerationRecoveryTests(ModerationFixture):
    def test_unhide_review_resolution_is_atomic_and_one_protected_advance(self):
        entry = self.append()
        for user in ("other", "third", "fourth"):
            self.report(entry, user)
        self.enroll()
        before = self.state()
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.action(entry, "unhide")
        self.storage.unavailable = False
        self.assertEqual(self.state(), before)
        self.assertEqual(self.raw(entry)["status"], "hidden")
        self.assertTrue(all(row["reviewed_at"] is None for row in self.moderation.reports()["reports"]))
        self.action(entry, "unhide")
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.assertTrue(all(row["reviewed_at"] == storage_time(self.now) for row in self.moderation.reports()["reports"]))
        self.assertEqual(self.state()["generation"], before["generation"] + 1)
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"] + 1)

    def test_charter_hide_outage_rolls_back_scope_and_sessions_then_commits_one_marker(self):
        self.approve("other")
        work = self.begin("other")
        entry = next(item for item in self.goals.public_record(self.slug)["entries"] if item["kind"] == "charter")
        self.enroll()
        before = self.state()
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.action(entry, "hide")
        self.storage.unavailable = False
        self.assertEqual(self.state(), before)
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.assertEqual(self.sessions.context("other", work["session_id"])["session_id"], work["session_id"])
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["state"], "open")
        self.action(entry, "hide")
        self.assertEqual(self.state()["generation"], before["generation"] + 1)
        self.assertEqual(self.state()["growth_generation"], before["growth_generation"] + 1)
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["state"], "paused")
        self.error(409, self.sessions.context, "other", work["session_id"])

    def test_ordinary_held_entry_and_metadata_insert_use_no_authority_marker(self):
        self.enroll()
        before = self.state()
        entry = self.append(status="held", reasons=["sensitive_claim"])
        with self.db.transaction() as connection:
            remember_hold(connection, entry["id"], ["sensitive_claim", "sensitive_claim"])
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.storage.files), 1)

    def test_hold_metadata_is_closed_and_original_reasons_cannot_be_replaced(self):
        entry = self.append(status="held", reasons=["sensitive_claim"])
        for reasons in ([], ["unknown"], [None], "sensitive_claim"):
            with self.db.transaction() as connection:
                self.error(422, remember_hold, connection, entry["id"], reasons)
        with self.db.transaction() as connection:
            self.error(409, remember_hold, connection, entry["id"], ["sensitive_dose"])
        self.assertEqual(self.moderation.held()["entries"][0]["reasons"], ["sensitive_claim"])

    def test_each_operator_transition_coalesces_to_one_protected_authority_advance(self):
        entry = self.finding(status="held", reasons=["sensitive_claim"])
        self.enroll()
        for action in ("hide", "unhide", "release"):
            before = self.state()
            self.action(entry, action)
            after = self.state()
            self.assertEqual(after["generation"], before["generation"] + 1)
            self.assertEqual(after["growth_generation"], before["growth_generation"] + 1)
        self.assertTrue(verify_record(self.goals.public_record(self.slug)["entries"])["valid"])

    def test_storage_outage_rolls_back_visibility_metadata_and_operator_event(self):
        entry = self.finding(status="held", reasons=["sensitive_claim"])
        self.enroll()
        before, record = self.state(), self.goals.public_record(self.slug)
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.action(entry, "hide")
        self.storage.unavailable = False
        self.assertEqual(self.state(), before)
        self.assertEqual(self.raw(entry)["status"], "held")
        self.assertEqual(self.goals.public_record(self.slug), record)
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT hidden_from_status FROM goal_entry_moderation WHERE entry_id=?", (entry["id"],)).fetchone()[0])

    def test_threshold_report_outage_rolls_back_report_and_hide_together_then_advances_once(self):
        entry = self.append()
        self.report(entry, "other")
        self.report(entry, "third")
        self.enroll()
        before = self.state()
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.report(entry, "fourth")
        self.storage.unavailable = False
        self.assertEqual(self.count_reports(entry), 2)
        self.assertEqual(self.raw(entry)["status"], "visible")
        self.assertEqual(self.state(), before)
        self.report(entry, "fourth")
        after = self.state()
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertEqual(after["growth_generation"], before["growth_generation"] + 1)

    def test_report_before_threshold_is_ordinary_and_recovery_hold_blocks_it(self):
        entry = self.append()
        self.enroll()
        before = self.state()
        self.report(entry)
        self.assertEqual(self.state(), before)
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        self.error(503, self.report, entry, user="third")
        self.assertEqual(self.count_reports(entry), 1)
