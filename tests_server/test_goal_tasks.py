"""Fictional task transitions, cited summaries and fresh session context."""
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import patch

from connectthebots_server import agent_texts
from connectthebots_server.goal_record import verify_record
from connectthebots_server.goal_tasks import GoalTaskService
from connectthebots_server.recovery_authority import local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_goal_sessions import SessionFixture
from tests_server.test_goals import fictional_charter


class TaskFixture(SessionFixture):
    def setUp(self):
        super().setUp()
        self.tasks = GoalTaskService(self.db, lambda: self.now, self.goals, self.sessions)
        self.approve("lead")
        self.lead_session = self.begin("lead")
        self.task_id = self.lead_session["task"]["id"]

    def task(self, title="Check the fictional prism arithmetic"):
        return fictional_charter()["tasks"][0] | {"title": title}

    def create_task(self, title="Check the fictional prism arithmetic"):
        return self.tasks.create("lead", self.slug, {"session_id": self.lead_session["session_id"], "task": self.task(title)})

    def change(self, task_id=None, **changes):
        body = {"session_id": self.lead_session["session_id"], "reason": "A reproducible fictional result changed the task.",
                "finding_ids": [], **changes}
        return self.tasks.update("lead", self.slug, task_id or self.task_id, body)

    def finding(self, *, task_id=None, user="other", status="visible", now=None, slug=None):
        """Hand-built record fixture, independent of the finding writer."""
        slug = slug or self.slug
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection, slug)
            member = self.members.member(connection, goal, user)
            task_id = task_id or connection.execute("SELECT id FROM goal_tasks WHERE goal_id=? ORDER BY id LIMIT 1", (goal["id"],)).fetchone()[0]
            content = {"task_id": task_id, "kind": "proof_step", "summary": "The fictional period is exactly twelve steps.",
                       "detail": "Enumerating each fictional position gives twelve distinct states.",
                       "method": "Enumerate the twelve fictional states and check the return to zero.",
                       "sources": [], "confidence": "tentative", "unknowns": [], "builds_on": []}
            entry = self.goals._append(connection, goal, "finding", member["handle"], content, now or self.now,
                                       author_user_id=user, status=status)
            connection.execute("INSERT INTO goal_findings(entry_id,task_id,task_entry_id) VALUES(?,?,(SELECT id FROM goal_entries "
                "WHERE goal_id=? AND kind='step' AND seq<? AND json_extract(content_json,'$.task_id')=? ORDER BY seq DESC LIMIT 1))",
                (entry["id"], task_id, goal["id"], entry["seq"], task_id))
            return entry

    def summary_body(self, **changes):
        return {"session_id": self.lead_session["session_id"], "text": "The fictional proof is reproducible; remaining questions are listed separately.",
                "finding_ids": [], "disputed": [], **changes}

    def review(self, finding, *, user="third", verdict="does_not_support", independent=True):
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection, self.slug)
            member = self.members.member(connection, goal, user)
            entry = self.goals._append(connection, goal, "review", member["handle"],
                {"finding_id": finding["id"], "verdict": verdict,
                 "note": "The fictional enumeration was independently inspected.", "checked": ["method"], "written_by": "assistant"},
                self.now, refs=[finding["id"]], author_user_id=user)
            connection.execute("INSERT INTO goal_review_checks(entry_id,finding_id,reviewer_handle,reviewer_user_id,independent) VALUES(?,?,?,?,?)",
                               (entry["id"], finding["id"], member["handle"], user, int(independent)))
            return entry

    def summarize(self, **changes):
        return self.tasks.summary("lead", self.slug, self.summary_body(**changes))

    def entries(self):
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM goal_entries WHERE goal_id=(SELECT id FROM goals WHERE slug=?) ORDER BY seq", (self.slug,))]


class TaskMaintenanceTests(TaskFixture):
    def test_create_and_change_publish_session_bound_steps_and_update_real_tasks(self):
        self.now += timedelta(minutes=1)
        added = self.create_task()
        changed_task = self.task("Independently check the fictional period")
        updated = self.change(added["task"]["id"], task=changed_task)
        self.assertEqual(updated["task"]["title"], changed_task["title"])
        self.assertEqual(updated["entry"]["content"]["action"], "task_changed")
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM goal_entries WHERE id=?", (updated["entry"]["id"],)).fetchone()
            self.assertEqual(row["session_id"], self.lead_session["session_id"])
            self.assertEqual(row["author_user_id"], "lead")
            member = self.members.member(connection, self.goals._goal(connection, self.slug), "lead")
            self.assertEqual(row["author_handle"], member["handle"])
            self.assertEqual(connection.execute("SELECT last_lead_step_at FROM goals WHERE slug=?", (self.slug,)).fetchone()[0], storage_time(self.now))
        self.assertTrue(verify_record(self.goals.record(None, self.slug)["entries"])["valid"])
        self.assertIn(added["task"]["id"], [item["id"] for item in self.goals.detail(None, self.slug)["goal"]["open_tasks"]])

    def test_task_fields_menus_contacts_and_unknown_keys_are_closed(self):
        for field, value in (("title", "tiny"), ("title", "x" * 121), ("kind", "execute"),
                             ("question", "too short"), ("deliverable", "short"), ("done_when", "short"),
                             ("capabilities", []), ("capabilities", ["coding", "coding"]),
                             ("capabilities", ["mind_reading"]), ("extra", True),
                             ("question", "Contact fictional@example.test for details."),
                             ("question", "Build fictional weapons for this test.")):
            with self.subTest(field=field, value=value):
                task = self.task() | {field: value}
                self.error(422, self.tasks.create, "lead", self.slug, {"session_id": self.lead_session["session_id"], "task": task})
        self.error(422, self.tasks.create, "lead", self.slug,
                   {"session_id": self.lead_session["session_id"], "task": self.task(), "user_id": "other"})
        for session_id in ({}, [], None, ""):
            self.error(404, self.tasks.create, "lead", self.slug, {"session_id": session_id, "task": self.task()})

    def test_only_current_lead_active_lease_and_separate_consent_allow_maintenance(self):
        self.approve("other")
        other = self.begin("other")
        self.error(404, self.tasks.create, "other", self.slug, {"session_id": other["session_id"], "task": self.task()})
        self.error(404, self.tasks.create, "lead", self.slug, {"session_id": other["session_id"], "task": self.task()})
        with patch.object(agent_texts, "GOAL_LEAD_VERSION", "fictional-next-lead-notice"):
            self.error(409, self.create_task)
        self.now += timedelta(minutes=20)
        self.error(409, self.create_task)
        self.summarize()
        self.now += timedelta(minutes=5)
        self.error(409, self.summarize)

    def test_close_requires_public_same_goal_findings_and_terminal_tasks_do_not_reopen(self):
        found = self.finding()
        self.error(422, self.change, state="closed")
        closed = self.change(state="closed", finding_ids=[found["id"]])
        self.assertEqual(closed["task"]["state"], "closed")
        self.assertEqual(closed["entry"]["refs"], [found["id"]])
        self.assertEqual(closed["entry"]["content"]["action"], "task_closed")
        self.assertNotIn(self.task_id, [item["id"] for item in self.goals.detail(None, self.slug)["goal"]["open_tasks"]])
        self.error(409, self.change, state="open", task=self.task())

    def test_change_requires_reason_actual_change_and_bounded_finding_references(self):
        base = {"session_id": self.lead_session["session_id"], "reason": "A fictional reason.", "finding_ids": [], "task": self.task()}
        for key in ("reason", "finding_ids", "session_id"):
            body = dict(base)
            del body[key]
            self.error(422, self.tasks.update, "lead", self.slug, self.task_id, body)
        for refs in (["missing"], [self.lead_session["session_id"]], ["same"] * 2, [None], "wrong", [str(i) for i in range(101)]):
            with self.subTest(refs=str(refs)[:60]):
                self.error(422, self.change, task=self.task(), finding_ids=refs)
        self.error(409, self.change, task=fictional_charter()["tasks"][0])
        self.error(422, self.change, state="reopened")
        self.error(422, self.change, state="superseded", reason="Email fictional@example.test now.")

    def test_other_goal_tasks_and_findings_cannot_be_cited_or_changed(self):
        self.setting("goals_led_per_member", 2)
        second = self.open_goal(charter=fictional_charter("Fictional: unrelated prism proof"))
        self.members.join("other", second["slug"], {"capabilities": ["mathematics"], "approved_by_owner": True})
        found = self.finding(slug=second["slug"])
        self.error(422, self.change, state="closed", finding_ids=[found["id"]])
        foreign_task = second["open_tasks"][0]["id"]
        self.error(404, self.change, foreign_task, task=self.task())
        self.error(404, self.tasks.create, "lead", second["slug"], {"session_id": self.lead_session["session_id"], "task": self.task()})

    def test_open_task_limit_is_fifty_and_superseding_frees_a_slot(self):
        for index in range(49):
            self.create_task(f"Check fictional arithmetic case {index:02d}")
        self.error(409, self.create_task)
        self.change(state="superseded")
        added = self.create_task()
        self.assertEqual(added["task"]["state"], "open")
        self.assertEqual(len(self.goals.detail(None, self.slug)["goal"]["open_tasks"]), 50)

    def test_weekly_record_cap_rolls_back_entire_task_change(self):
        self.setting("goal_entries_per_goal_per_week", 10)
        while len(self.entries()) < 10:
            self.create_task(f"A fictional bounded arithmetic task {len(self.entries())}")
        before = self.goals.detail(None, self.slug)["goal"]["open_tasks"]
        self.error(429, self.create_task)
        self.error(429, self.change, task=self.task())
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["open_tasks"], before)
        self.assertEqual(len(self.entries()), 10)


class SummaryAndNoteTests(TaskFixture):
    def test_summary_has_only_authored_text_and_ids_and_uses_latest_entry_projection(self):
        found = self.finding()
        first = self.summarize(finding_ids=[found["id"]])["summary"]
        second = self.summarize(text="A second fictional summary supersedes the earlier prose.", finding_ids=[found["id"]])["summary"]
        self.assertEqual(set(second["content"]), {"text", "finding_ids", "disputed", "written_by"})
        self.assertNotIn(found["content"]["detail"], json.dumps(second))
        context = self.sessions.context("lead", self.lead_session["session_id"])
        self.assertEqual(context["summary"]["id"], second["id"])
        self.assertNotEqual(context["summary"]["id"], first["id"])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT session_id FROM goal_entries WHERE id=?", (second["id"],)).fetchone()[0], self.lead_session["session_id"])

    def test_summary_rejects_unknown_fields_contact_oversize_duplicate_and_private_refs(self):
        found = self.finding()
        for change in ({"text": "x" * 3001}, {"text": "Write to fictional@example.test."}, {"text": ""},
                       {"unexpected": "field"}, {"finding_ids": [found["id"], found["id"]]},
                       {"finding_ids": ["missing"]}, {"finding_ids": [self.entries()[0]["id"]]},
                       {"finding_ids": [], "disputed": [found["id"]]}):
            with self.subTest(change=change):
                self.error(422, self.tasks.summary, "lead", self.slug, self.summary_body(**change))
        for status in ("held", "pending_peer_check", "hidden", "withdrawn"):
            private = self.finding(status=status)
            with self.subTest(status=status):
                self.error(422, self.summarize, finding_ids=[private["id"]])
                self.error(422, self.summarize, disputed=[private["id"]])

    def test_summary_projection_and_context_drop_now_hidden_dependency(self):
        found = self.finding()
        self.summarize(finding_ids=[found["id"]])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE id=?", (found["id"],))
        self.assertIsNone(self.sessions.context("lead", self.lead_session["session_id"])["summary"])
        self.assertIsNone(self.goals.detail(None, self.slug)["goal"]["latest_summary"])

    def test_disputed_findings_can_only_be_cited_in_the_separate_disputed_list(self):
        found = self.finding()
        self.review(found)
        self.error(422, self.summarize, finding_ids=[found["id"]])
        summary = self.summarize(disputed=[found["id"]])["summary"]
        self.assertEqual(summary["content"]["finding_ids"], [])
        self.assertEqual(summary["content"]["disputed"], [found["id"]])
        self.assertEqual(self.sessions.context("lead", self.lead_session["session_id"])["summary"]["id"], summary["id"])

    def test_disputed_pending_content_stays_private_until_independent_support(self):
        pending = self.finding(status="pending_peer_check")
        self.review(pending)
        # Dissent makes the status disputed, but cannot publish pending text.
        self.error(422, self.summarize, disputed=[pending["id"]])
        support = self.review(pending, user="lead", verdict="supports")
        summary = self.summarize(disputed=[pending["id"]])["summary"]
        self.assertIsNotNone(self.sessions.context("lead", self.lead_session["session_id"])["summary"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='hidden' WHERE id=?", (support["id"],))
        self.assertIsNone(self.sessions.context("lead", self.lead_session["session_id"])["summary"])
        self.error(422, self.summarize, disputed=[pending["id"]])

    def test_summary_cannot_use_other_owners_lease_or_outlive_stop(self):
        self.approve("other")
        other = self.begin("other")
        self.error(404, self.tasks.summary, "other", self.slug, self.summary_body(session_id=other["session_id"]))
        self.sessions.stop("lead", self.slug)
        self.error(409, self.summarize)

    def test_notes_allow_completion_grace_without_lead_permission_but_end_cancels_it(self):
        self.approve("other")
        other = self.begin("other")
        self.now += timedelta(minutes=20)
        note = self.tasks.note("other", other["session_id"], {"text": "The fictional arithmetic check is complete."})["entry"]
        self.assertEqual(note["kind"], "note")
        self.assertNotIn("user_id", note)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT session_id FROM goal_entries WHERE id=?", (note["id"],)).fetchone()[0], other["session_id"])
        self.error(404, self.tasks.note, "third", other["session_id"], {"text": "Fictional note."})
        for text in ("", "x" * 1501, "See https://fictional.example.test for contact."):
            self.error(422, self.tasks.note, "other", other["session_id"], {"text": text})
        self.finish(other)
        self.error(409, self.tasks.note, "other", other["session_id"], {"text": "No more fictional work."})


class SessionContextTests(TaskFixture):
    def test_complete_real_m3_ordinary_sessions_do_not_advance_recovery_authority(self):
        from connectthebots_server.goal_findings import GoalFindingService
        self.finish(self.lead_session, user="lead")
        self.approve("other")
        findings = GoalFindingService(self.db, lambda: self.now, self.goals, self.sessions)
        with self.db.connect() as connection:
            before = local_state(connection)
        lead = self.begin("lead")
        contributor = self.begin("other")
        finding = findings.finding("other", contributor["session_id"], {
            "task_id": contributor["task"]["id"], "kind": "proof_step",
            "summary": "The fictional period is exactly twelve steps.",
            "detail": "Enumerating each fictional position gives twelve distinct states.",
            "method": "Enumerate the twelve fictional states and check the return to zero.",
            "sources": [], "confidence": "tentative", "unknowns": [], "builds_on": [],
        })["entry"]
        findings.review("lead", lead["session_id"], {
            "finding_id": finding["id"], "verdict": "supports",
            "note": "The fictional enumeration is reproducible.", "checked": ["method"],
        })
        self.tasks.note("other", contributor["session_id"], {"text": "The fictional proof is complete."})
        self.tasks.summary("lead", self.slug, self.summary_body(session_id=lead["session_id"], finding_ids=[finding["id"]]))
        self.sessions.context("lead", lead["session_id"])
        self.now += timedelta(minutes=20)
        self.tasks.note("other", contributor["session_id"], {"text": "The final fictional check completed during grace."})
        self.finish(contributor, minutes=20)
        self.finish(lead, user="lead", minutes=20)
        with self.db.connect() as connection:
            self.assertEqual(local_state(connection), before)
        self.assertTrue(verify_record(self.goals.record(None, self.slug)["entries"])["valid"])

    def test_context_is_fresh_owner_only_read_without_counters_or_new_work_in_grace(self):
        self.approve("other")
        other = self.begin("other")
        before = other["session"]
        first = self.sessions.context("other", other["session_id"])
        second = self.sessions.context("other", other["session_id"])
        self.assertEqual(first, second)
        self.assertEqual(first["session"]["findings_count"], before["findings_count"])
        self.assertEqual(first["session"]["reviews_count"], before["reviews_count"])
        self.error(404, self.sessions.context, "third", other["session_id"])
        self.now += timedelta(minutes=20)
        self.error(409, self.sessions.context, "other", other["session_id"])
        self.tasks.note("other", other["session_id"], {"text": "Completion remains permitted during grace."})

    def test_context_offers_only_eligible_other_owner_content_and_refreshes_on_read(self):
        visible = self.finding(user="lead")
        pending = self.finding(user="lead", status="pending_peer_check")
        private = [self.finding(user="lead", status=status) for status in ("held", "hidden", "withdrawn")]
        own = self.finding(user="other")
        self.approve("other")
        started = self.begin("other")
        offered = {item["id"] for item in started["reviews"]}
        self.assertEqual(offered, {visible["id"], pending["id"]})
        fresh = self.finding(user="third")
        context = self.sessions.context("other", started["session_id"])
        offered = {item["id"] for item in context["reviews"]}
        self.assertIn(fresh["id"], offered)
        self.assertNotIn(own["id"], offered)
        for entry in private:
            self.assertNotIn(entry["id"], offered)
        self.assertEqual(context["session"]["reviews_count"], 0)
        self.assertNotIn("reviewer_user_id", json.dumps(context))

    def test_assignment_prefers_fewer_recent_findings_even_when_content_was_erased(self):
        self.change(state="superseded")
        first = self.create_task("Check fictional case with two recent findings")["task"]
        second = self.create_task("Check fictional case with one recent finding")["task"]
        self.finding(task_id=first["id"])
        erased = self.finding(task_id=first["id"], status="hidden")
        self.finding(task_id=second["id"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET content_json=NULL,status='withdrawn' WHERE id=?", (erased["id"],))
            connection.execute("UPDATE goal_tasks SET last_worked_at=? WHERE id=?", (storage_time(self.now), second["id"]))
        self.approve("other")
        started = self.begin("other")
        self.assertEqual(started["task"]["id"], second["id"])

    def test_seven_day_finding_boundary_then_oldest_last_worked_then_id_are_deterministic(self):
        self.change(state="superseded")
        first = self.create_task("Check fictional arithmetic first case")["task"]
        second = self.create_task("Check fictional arithmetic second case")["task"]
        self.finding(task_id=first["id"], now=self.now - timedelta(days=7))
        self.finding(task_id=second["id"], now=self.now - timedelta(days=7, microseconds=1))
        with self.db.transaction() as connection:
            for task in (first, second):
                connection.execute("UPDATE goal_tasks SET last_worked_at=? WHERE id=?", (storage_time(self.now - timedelta(days=1)), task["id"]))
        self.approve("other")
        started = self.begin("other")
        self.assertEqual(started["task"]["id"], min(first["id"], second["id"]))
        self.finish(started)
        older = max(first["id"], second["id"])
        started = self.begin("other")
        self.assertEqual(started["task"]["id"], older)

    def test_explicit_task_choice_still_requires_open_capability_overlap(self):
        newest = self.create_task()["task"]
        self.finding(task_id=newest["id"])
        code_task = self.task("Check the fictional algorithm implementation") | {"kind": "code", "capabilities": ["coding"]}
        code = self.tasks.create("lead", self.slug, {"session_id": self.lead_session["session_id"], "task": code_task})["task"]
        self.approve("other")
        self.error(409, self.sessions.start, "other", self.slug, {"task_id": code["id"]})
        self.error(422, self.sessions.start, "other", self.slug, {"task_id": {}})
        started = self.sessions.start("other", self.slug, {"task_id": newest["id"]})
        self.assertEqual(started["task"]["id"], newest["id"])
        self.finish(started)
        self.change(newest["id"], state="superseded")
        self.error(409, self.sessions.start, "other", self.slug, {"task_id": newest["id"]})

    def test_context_cannot_reintroduce_hidden_task_or_summary_content(self):
        self.summarize()
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND (kind='summary' OR (kind='step' AND json_extract(content_json,'$.task_id')=?))", (self.slug, self.task_id))
        context = self.sessions.context("lead", self.lead_session["session_id"])
        self.assertIsNone(context["task"])
        self.assertIsNone(context["summary"])
        self.approve("other")
        self.error(409, self.begin, "other")
