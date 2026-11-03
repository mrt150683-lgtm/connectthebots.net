"""Real account erasure separates current leadership from content authorship."""
import json

from connectthebots_server.board import BoardService
from connectthebots_server.config import Settings
from connectthebots_server.goal_leadership import GoalLeadershipService
from connectthebots_server.goal_record import canonical_json, verify_record
from connectthebots_server.goal_tasks import GoalTaskService
from connectthebots_server.interactions import InteractionService
from connectthebots_server.lifecycle import AccountLifecycle
from connectthebots_server.moderation import ModerationService
from connectthebots_server.notifications import NotificationService
from connectthebots_server.operator_alerts import OperatorAlerts
from connectthebots_server.recovery_authority import local_state
from tests_server.test_goal_sessions import SessionFixture
from tests_server.test_goals import fictional_charter


class GoalHandoverLifecycleTests(SessionFixture):
    def setUp(self):
        super().setUp()
        clock = lambda: self.now
        settings = Settings("staging", "https://fictional.example.test", self.db.path, "fictional-lifecycle-" + "x" * 32)
        board = BoardService(self.db, clock)
        interactions = InteractionService(self.db, clock, board)
        alerts = OperatorAlerts(self.db, clock, settings)
        notifications = NotificationService(self.db, clock, settings, operator_alerts=alerts)
        moderation = ModerationService(self.db, clock, board, interactions, alerts)
        self.lifecycle = AccountLifecycle(self.db, clock, board, interactions, notifications, moderation, goals=self.goals)
        self.leadership = GoalLeadershipService(self.db, clock, self.goals, self.members, self.sessions)
        self.tasks = GoalTaskService(self.db, clock, self.goals, self.sessions)

    def goal_row(self):
        with self.db.connect() as connection:
            return self.goals._goal(connection, self.slug)

    def entry(self, entry_id):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goal_entries WHERE id=?", (entry_id,)).fetchone()

    def charter_entry(self):
        with self.db.connect() as connection:
            return self.goals._charter_entry(connection, self.goals._goal(connection, self.slug))

    def handle(self, user):
        with self.db.connect() as connection:
            return connection.execute("SELECT handle FROM goal_members WHERE goal_id=? AND user_id=?", (self.goal_row()["id"], user)).fetchone()[0]

    def offer(self, user):
        preview = self.leadership.preview(user, self.slug)
        self.leadership.volunteer(user, self.slug, {"charter_revision": preview["charter_revision"],
            "notice_version": preview["notice_version"], "approved_by_owner": True})

    def handover(self, user="other"):
        goal = self.goal_row()
        self.leadership.handover(goal["lead_user_id"], self.slug, {"handle": self.handle(user),
            "charter_revision": goal["charter_revision"], "approved_by_owner": True})
        self.offer(user)
        self.leadership.assign(self.slug, self.handle(user), "The fictional role and charter were independently approved.")

    def assert_chain_and_public_identity(self):
        record = self.goals.public_record(self.slug)["entries"]
        self.assertTrue(verify_record(record)["valid"])
        encoded = json.dumps(record)
        for private in ('"author_user_id"', '"lead_user_id"', '"user_id"', "@fictional.example.test"):
            self.assertNotIn(private, encoded)

    def test_deleting_new_lead_preserves_old_authors_scope_and_allows_fresh_successor(self):
        source = self.charter_entry()
        original = self.goal_row()["charter_json"]
        self.approve("lead")
        old_work = self.begin("lead")
        self.approve("third")
        peer_work = self.begin("third")
        self.handover()
        self.approve("other")
        new_work = self.begin("other")
        note = self.tasks.note("other", new_work["session_id"], {"text": "Fictional new lead authored note canary."})["entry"]
        task = self.tasks.create("other", self.slug, {"session_id": new_work["session_id"],
            "task": fictional_charter()["tasks"][0] | {"title": "Check the fictional successor task canary"}})["task"]
        with self.db.connect() as connection:
            before = local_state(connection)
        self.assertTrue(self.lifecycle.delete_account("other"))
        row = self.goal_row()
        self.assertIsNone(row["lead_user_id"])
        self.assertEqual(row["state"], "paused")
        self.assertEqual(row["charter_json"], original)
        self.assertFalse(row["review_pending"])
        self.assertEqual(self.entry(source["id"])["content_json"], source["content_json"])
        self.assertEqual(self.entry(source["id"])["entry_sha256"], source["entry_sha256"])
        self.assertIsNone(self.entry(note["id"])["content_json"])
        self.assertIsNone(self.entry(note["id"])["author_user_id"])
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT task_json FROM goal_tasks WHERE id=?", (task["id"],)).fetchone()[0])
            self.assertIsNone(connection.execute("SELECT 1 FROM users WHERE id='other'").fetchone())
            self.assertEqual(connection.execute("SELECT end_reason FROM goal_sessions WHERE id=?", (peer_work["session_id"],)).fetchone()[0], "account_deleted")
            self.assertEqual(connection.execute("SELECT end_reason FROM goal_sessions WHERE id=?", (old_work["session_id"],)).fetchone()[0], "lead_changed")
            self.assertEqual(local_state(connection)["growth_generation"], before["growth_generation"] + 1)
        self.offer("third")
        self.leadership.assign(self.slug, self.handle("third"), "A fictional successor accepted the preserved public charter.")
        self.assertEqual(self.goal_row()["state"], "open")
        self.error(409, self.begin, "lead")
        self.error(409, self.begin, "third")
        with self.db.connect() as connection:
            old_permission = connection.execute("SELECT * FROM goal_allocations WHERE user_id='lead'").fetchone()
            self.assertIsNotNone(old_permission["stopped_at"])
            self.assertIsNone(old_permission["lead_notice_version"])
        self.approve("third")
        self.begin("third")
        self.assert_chain_and_public_identity()

    def test_deleting_old_charter_author_erases_scope_and_pauses_current_lead_work(self):
        source = self.charter_entry()
        self.handover()
        self.approve("other")
        current_work = self.begin("other")
        self.approve("third")
        peer_work = self.begin("third")
        self.lifecycle.delete_account("lead")
        row = self.goal_row()
        self.assertEqual(row["lead_user_id"], "other")
        self.assertEqual(row["state"], "paused")
        self.assertIsNone(row["charter_json"])
        self.assertEqual(row["topic_key"], "")
        self.assertTrue(row["review_pending"])
        self.assertIsNone(self.entry(source["id"])["content_json"])
        for work in (current_work, peer_work):
            with self.db.connect() as connection:
                self.assertEqual(connection.execute("SELECT end_reason FROM goal_sessions WHERE id=?", (work["session_id"],)).fetchone()[0], "account_deleted")
        self.error(409, self.begin, "other")
        self.error(409, self.leadership.preview, "third", self.slug)
        self.assert_chain_and_public_identity()

    def test_old_author_erasure_preserves_new_authors_identical_pending_charter_until_review(self):
        self.setting("goals_sensitive_open_max", 1)
        charter = fictional_charter(category="animal_health")
        self.goals.revise_charter("lead", self.slug, charter, True)
        self.goals.operator_action(self.slug, "approve", "The fictional sensitive charter was explicitly reviewed.")
        old_source = self.charter_entry()
        self.handover()
        self.approve("other")
        self.goals.revise_charter("other", self.slug, charter, True)
        pending = self.goal_row()["pending_entry_id"]
        # Same inner charter prose, but a separate author's fresh exact approval.
        self.assertEqual(self.goal_row()["pending_charter_json"], self.goal_row()["charter_json"])
        self.lifecycle.delete_account("lead")
        row = self.goal_row()
        self.assertEqual(row["lead_user_id"], "other")
        self.assertIsNone(row["charter_json"])
        self.assertEqual(row["pending_charter_json"], canonical_json(charter | {"licence": {"text": "CC BY 4.0", "code": "Apache-2.0"}}))
        self.assertEqual(row["pending_entry_id"], pending)
        self.assertTrue(row["review_pending"])
        self.assertEqual(self.entry(pending)["author_user_id"], "other")
        self.assertIsNotNone(self.entry(pending)["content_json"])
        self.assertIsNone(self.entry(old_source["id"])["content_json"])
        public = {entry["id"]: entry for entry in self.goals.public_record(self.slug)["entries"]}
        self.assertIsNone(public[pending]["content"])
        self.assertIsNotNone(self.goals.detail("other", self.slug)["goal"]["pending_charter"])
        self.error(409, self.begin, "other")
        self.goals.operator_action(self.slug, "approve", "The surviving author's exact pending charter was reviewed.")
        self.assertEqual(self.goal_row()["charter_json"], row["pending_charter_json"])
        self.assertIsNone(self.entry(old_source["id"])["content_json"])
        self.error(409, self.begin, "other")
        self.approve("other")
        self.begin("other")
        self.assert_chain_and_public_identity()

    def test_deleting_new_lead_erases_own_pending_scope_but_keeps_old_approved_scope(self):
        source = self.charter_entry()
        self.handover()
        self.goals.revise_charter("other", self.slug, fictional_charter(category="animal_health"), True)
        pending = self.goal_row()["pending_entry_id"]
        self.lifecycle.delete_account("other")
        row = self.goal_row()
        self.assertIsNone(row["lead_user_id"])
        self.assertEqual(row["charter_json"], canonical_json(self.goal["charter"]))
        self.assertIsNone(row["pending_charter_json"])
        self.assertIsNone(row["pending_entry_id"])
        self.assertFalse(row["review_pending"])
        self.assertIsNone(self.entry(pending)["content_json"])
        self.offer("third")
        self.leadership.assign(self.slug, self.handle("third"), "The third fictional member accepts only the surviving approved scope.")
        self.assertEqual(self.goal_row()["charter_revision"], 1)
        self.assertEqual(self.goal_row()["latest_charter_revision"], 2)
        self.assert_chain_and_public_identity()

    def _withdrawn_scope_cannot_return(self, kind):
        self.handover()
        with self.db.connect() as connection:
            source = connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND kind=? ORDER BY seq LIMIT 1", (self.goal_row()["id"], kind)).fetchone()
        self.goals.withdrawal("lead", source["id"], owner_control=True)
        self.assertIsNone(self.goal_row()["charter_json"])
        self.lifecycle.delete_account("other")
        row = self.goal_row()
        self.assertIsNone(row["lead_user_id"])
        self.assertIsNone(row["charter_json"])
        self.assertIsNone(row["pending_charter_json"])
        self.assertEqual(row["state"], "paused")
        self.error(409, self.leadership.preview, "third", self.slug)
        self.error(409, self.goals.operator_action, self.slug, "resume", "This must not reconstruct an erased fictional scope.")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_tasks WHERE task_json IS NOT NULL").fetchone()[0], 0)
        self.assert_chain_and_public_identity()

    def test_deleting_successor_never_reconstructs_withdrawn_old_charter(self):
        self._withdrawn_scope_cannot_return("charter")

    def test_deleting_successor_never_reconstructs_withdrawn_old_task_copies(self):
        self._withdrawn_scope_cannot_return("step")

    def test_deleting_old_author_keeps_identical_independently_approved_current_scope_and_work(self):
        self.handover()
        self.goals.revise_charter("other", self.slug, fictional_charter(), True)
        independent = self.charter_entry()
        self.approve("other")
        current_work = self.begin("other")
        self.approve("third")
        peer_work = self.begin("third")
        self.lifecycle.delete_account("lead")
        row = self.goal_row()
        self.assertEqual(row["state"], "open")
        self.assertEqual(row["lead_user_id"], "other")
        self.assertEqual(row["charter_revision"], 2)
        self.assertFalse(row["review_pending"])
        self.assertIsNotNone(self.entry(independent["id"])["content_json"])
        self.assertIsNotNone(self.goals.detail(None, self.slug)["goal"]["charter"])
        for work in (current_work, peer_work):
            with self.db.connect() as connection:
                self.assertIsNone(connection.execute("SELECT end_reason FROM goal_sessions WHERE id=?", (work["session_id"],)).fetchone()[0])
        self.assert_chain_and_public_identity()
