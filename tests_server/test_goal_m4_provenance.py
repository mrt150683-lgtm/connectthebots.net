"""Task-version provenance: no publishing through a hidden assigned task."""
from pathlib import Path
import json
import sqlite3

from connectthebots_server.db import Database, load_migrations
from connectthebots_server.goal_findings import (GoalFindingService, entry_readable, public_entry_content,
                                               review_offers)
from connectthebots_server.goal_moderation import GoalModerationService
from connectthebots_server.goal_record import append_entry, canonical_json, entry_view, verify_record
from connectthebots_server.goal_tasks import GoalTaskService
from connectthebots_server.goals import GoalsService
from connectthebots_server.recovery_authority import local_state
from tests_server.test_goal_findings import payload
from tests_server.test_goal_sessions import SessionFixture
from tests_server.test_goals import GoalFixture, add_fictional_users, fictional_charter


class TaskProvenanceTests(SessionFixture):
    def setUp(self):
        super().setUp()
        self.findings = GoalFindingService(self.db, lambda: self.now, self.goals, self.sessions)
        self.tasks = GoalTaskService(self.db, lambda: self.now, self.goals, self.sessions)
        self.moderation = GoalModerationService(self.db, lambda: self.now, self.goals)
        self.approve("lead")
        self.approve("other")
        self.approve("third")
        self.lead = self.begin("lead")
        self.other = self.begin("other")
        self.third = self.begin("third")
        self.task_id = self.other["task"]["id"]

    def raw(self, entry_id):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry_id,)).fetchone()

    def source(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND kind='step' "
                "AND json_extract(content_json,'$.task_id')=? AND json_type(content_json,'$.task')='object' "
                "ORDER BY seq DESC LIMIT 1", (self.raw_goal()["id"], self.task_id)).fetchone()

    def raw_goal(self):
        with self.db.connect() as connection:
            return self.goals._goal(connection, self.slug)

    def publish(self, **changes):
        return self.findings.finding("other", self.other["session_id"], payload(self.task_id, **changes))["entry"]

    def public(self):
        return {entry["id"]: entry for entry in self.goals.public_record(self.slug)["entries"]}

    def action(self, entry_id, action):
        return self.moderation.action(entry_id, action, "Fictional task provenance was reviewed.")

    def count(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT findings_count FROM goal_sessions WHERE id=?", (self.other["session_id"],)).fetchone()[0]

    def test_new_finding_records_exact_immutable_task_source_without_authority_or_hash_changes(self):
        source = self.source()
        with self.db.connect() as connection:
            before = local_state(connection)
        entry = self.publish()
        with self.db.connect() as connection:
            meta = connection.execute("SELECT * FROM goal_findings WHERE entry_id=?", (entry["id"],)).fetchone()
            self.assertEqual(meta["task_entry_id"], source["id"])
            self.assertEqual(local_state(connection), before)
        self.assertEqual(entry["refs"], [])
        self.assertNotIn("task_entry_id", entry)
        self.assertNotIn("task_entry_id", entry["content"])
        for field in ("entry_id", "task_id", "task_entry_id"):
            with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as connection:
                connection.execute("UPDATE goal_findings SET " + field + "=NULL WHERE entry_id=?", (entry["id"],))
        self.assertTrue(verify_record(list(self.public().values()))["valid"])

    def test_hidden_and_held_assigned_task_block_existing_lease_and_roll_back_counter(self):
        source = self.source()
        for status in ("hidden", "held"):
            with self.subTest(status=status):
                with self.db.transaction() as connection:
                    connection.execute("UPDATE goal_entries SET status=? WHERE id=?", (status, source["id"]))
                self.assertIsNone(self.sessions.context("other", self.other["session_id"])["task"])
                before = len(self.public())
                self.error(409, self.publish)
                self.assertEqual(self.count(), 0)
                self.assertEqual(len(self.public()), before)
                with self.db.transaction() as connection:
                    connection.execute("UPDATE goal_entries SET status='visible' WHERE id=?", (source["id"],))

    def test_closed_or_superseded_assigned_task_rejects_late_submission_without_consuming(self):
        original = self.publish()
        self.tasks.update("lead", self.slug, self.task_id, {"session_id": self.lead["session_id"], "state": "closed",
            "reason": "The fictional proof answered this task.", "finding_ids": [original["id"]]})
        self.error(409, self.publish)
        self.assertEqual(self.count(), 1)
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_tasks SET state='superseded' WHERE id=?", (self.task_id,))
        self.error(409, self.publish)
        self.assertEqual(self.count(), 1)

    def test_later_task_hide_retracts_finding_review_summary_and_offers_then_unhide_restores(self):
        source = self.source()
        finding = self.publish()
        review = self.findings.review("lead", self.lead["session_id"], {"finding_id": finding["id"], "verdict": "supports",
            "note": "The fictional assigned proof was independently repeated.", "checked": ["method"]})["entry"]
        summary = self.tasks.summary("lead", self.slug, {"session_id": self.lead["session_id"],
            "text": "The fictional task canary is supported by the repeated proof.", "finding_ids": [finding["id"]], "disputed": []})["summary"]
        self.assertIn(finding["id"], {entry["id"] for entry in self.sessions.context("third", self.third["session_id"])["reviews"]})
        self.action(source["id"], "hide")
        public = self.public()
        for entry in (finding, review, summary):
            self.assertIsNone(public[entry["id"]]["content"])
            self.assertIsNotNone(self.raw(entry["id"])["content_json"])
        self.assertIsNone(self.goals.detail(None, self.slug)["goal"]["latest_summary"])
        context = self.sessions.context("third", self.third["session_id"])
        self.assertIsNone(context["task"])
        self.assertIsNone(context["summary"])
        self.assertEqual(context["reviews"], [])
        self.assertIsNotNone(self.goals.owned_entry("other", finding["id"])["content"])
        with self.db.connect() as connection:
            self.assertTrue(entry_readable(connection, self.raw(finding["id"]), self.raw_goal(), operator=True))
        self.action(source["id"], "unhide")
        for entry in (finding, review, summary):
            self.assertIsNotNone(self.public()[entry["id"]]["content"])
        self.assertTrue(verify_record(list(self.public().values()))["valid"])

    def test_task_withdrawal_keeps_pointer_hashes_and_cannot_be_reconstructed_by_unhide(self):
        source = self.source()
        finding = self.publish()
        digest = self.raw(finding["id"])["entry_sha256"]
        self.goals.withdrawal("lead", source["id"], owner_control=True)
        self.assertIsNone(self.public()[finding["id"]]["content"])
        self.assertIsNone(self.raw(source["id"])["content_json"])
        self.assertEqual(self.raw(finding["id"])["entry_sha256"], digest)
        self.assertIsNotNone(self.raw(finding["id"])["content_json"])
        self.error(409, self.action, source["id"], "unhide")
        self.assertTrue(verify_record(list(self.public().values()))["valid"])

    def test_source_tracks_version_at_publish_not_whichever_task_version_is_latest(self):
        old_source = self.source()
        original = self.publish()
        task = fictional_charter()["tasks"][0] | {"title": "Check the fictional task a second way"}
        changed = self.tasks.update("lead", self.slug, self.task_id, {"session_id": self.lead["session_id"],
            "reason": "Clarify the fictional proof task.", "finding_ids": [], "task": task})
        later = self.publish(summary="The second fictional task version was addressed.")
        with self.db.connect() as connection:
            pointers = dict(connection.execute("SELECT entry_id,task_entry_id FROM goal_findings"))
        self.assertEqual(pointers[original["id"]], old_source["id"])
        self.assertEqual(pointers[later["id"]], changed["entry"]["id"])
        self.action(old_source["id"], "hide")
        self.assertIsNone(self.public()[original["id"]]["content"])
        self.assertIsNotNone(self.public()[later["id"]]["content"])

    def test_null_metadata_fails_closed_but_generic_record_finding_remains_generic(self):
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection, self.slug)
            member = self.members.member(connection, goal, "other")
            unknown = append_entry(connection, goal, "finding", member["handle"], payload(self.task_id), self.now, author_user_id="other")
            connection.execute("INSERT INTO goal_findings(entry_id,task_id) VALUES(?,?)", (unknown["id"], self.task_id))
            generic = append_entry(connection, goal, "finding", member["handle"], {"summary": "A fictional generic record finding."}, self.now, author_user_id="other")
        self.assertIsNone(self.public()[unknown["id"]]["content"])
        self.assertIsNotNone(self.public()[generic["id"]]["content"])
        offers = self.sessions.context("third", self.third["session_id"])["reviews"]
        self.assertEqual([entry["id"] for entry in offers], [generic["id"]])
        self.assertIsNotNone(self.goals.owned_entry("other", unknown["id"])["content"])

    def test_provenance_long_dag_is_iterative_and_ordinary_inserts_do_not_advance(self):
        source = self.source()
        with self.db.transaction() as connection:
            before = local_state(connection)
            goal = self.goals._goal(connection, self.slug)
            previous = None
            for number in range(1100):
                entry = append_entry(connection, goal, "finding", self.goal["lead"]["handle"],
                    {"summary": "Fictional provenance chain step " + str(number)}, self.now,
                    refs=[previous] if previous else [], author_user_id="lead")
                connection.execute("INSERT INTO goal_findings(entry_id,task_id,task_entry_id) VALUES(?,?,?)",
                    (entry["id"], self.task_id, source["id"]))
                previous = entry["id"]
            tail = connection.execute("SELECT * FROM goal_entries WHERE id=?", (previous,)).fetchone()
            self.assertTrue(public_entry_content(connection, tail))
            self.assertEqual(local_state(connection), before)
        self.action(source["id"], "hide")
        with self.db.connect() as connection:
            self.assertFalse(public_entry_content(connection, self.raw(previous)))


class TaskProvenanceMigrationTests(GoalFixture):
    def test_upgrade_backfills_latest_prior_source_and_fails_closed_after_unknown_erased_step(self):
        migrations = load_migrations()
        migration_index = [migration.name for migration in migrations].index("0023_goal_task_provenance.sql")
        old = Database(Path(self.temp.name) / "fictional-m3.db", lambda: self.now, migrations=migrations[:migration_index])
        old.migrate()
        add_fictional_users(old, self.now)
        goals = GoalsService(old, lambda: self.now)
        with old.transaction() as connection:
            from connectthebots_server import growth_config
            growth_config.store(connection, "feature.goals", "everyone", self.now)
        proposed = goals.propose("lead", fictional_charter(), True)["goal"]
        goals.operator_action(proposed["slug"], "approve", "The fictional charter is ready for migration testing.")
        with old.transaction() as connection:
            goal = goals._goal(connection, proposed["slug"])
            task = connection.execute("SELECT * FROM goal_tasks WHERE goal_id=?", (goal["id"],)).fetchone()
            first_source = connection.execute("SELECT * FROM goal_entries WHERE kind='step' ORDER BY seq LIMIT 1").fetchone()

            def finding():
                entry = append_entry(connection, goal, "finding", goal["lead_handle"], payload(task["id"]), self.now, author_user_id="lead")
                connection.execute("INSERT INTO goal_findings(entry_id,task_id) VALUES(?,?)", (entry["id"], task["id"]))
                return entry

            first = finding()
            changed = append_entry(connection, goal, "step", goal["lead_handle"], {"action": "task_changed", "task_id": task["id"],
                "task": json.loads(task["task_json"])}, self.now, author_user_id="lead")
            second = finding()
            erased = append_entry(connection, goal, "step", goal["lead_handle"], {"action": "task_changed", "task_id": task["id"],
                "task": json.loads(task["task_json"])}, self.now, author_user_id="lead")
            connection.execute("UPDATE goal_entries SET content_json=NULL,status='withdrawn' WHERE id=?", (erased["id"],))
            unknown = finding()
            envelopes = {row["id"]: (row["entry_sha256"], row["content_sha256"], row["refs_json"]) for row in connection.execute("SELECT * FROM goal_entries")}
        upgraded = Database(old.path, lambda: self.now)
        upgraded.migrate()
        with upgraded.connect() as connection:
            pointers = dict(connection.execute("SELECT entry_id,task_entry_id FROM goal_findings"))
            self.assertEqual(pointers[first["id"]], first_source["id"])
            self.assertEqual(pointers[second["id"]], changed["id"])
            self.assertIsNone(pointers[unknown["id"]])
            self.assertEqual(envelopes, {row["id"]: (row["entry_sha256"], row["content_sha256"], row["refs_json"]) for row in connection.execute("SELECT * FROM goal_entries")})
            row = connection.execute("SELECT * FROM goal_entries WHERE id=?", (unknown["id"],)).fetchone()
            self.assertFalse(public_entry_content(connection, row))
            record = [entry_view(row, proposed["slug"]) for row in connection.execute("SELECT * FROM goal_entries ORDER BY seq")]
            self.assertTrue(verify_record(record)["valid"])
