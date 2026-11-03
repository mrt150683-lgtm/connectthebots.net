"""Real local operator subprocesses, with fictional data and isolated env."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from connectthebots_server.goal_leadership import GoalLeadershipService
from connectthebots_server.goal_record import verify_record
from connectthebots_server.sessions import storage_time
from tests_server.test_goal_moderation import ModerationFixture


ROOT = Path(__file__).resolve().parents[1]


class GoalAdminFixture(ModerationFixture):
    def setUp(self):
        super().setUp()
        # The CLI intentionally uses real UTC. Keep fictional authority current
        # without patching a clock inside the separate Python process.
        self.now = datetime.now(timezone.utc)
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=?,last_login_at=?",
                               (storage_time(self.now - timedelta(days=30)), storage_time(self.now)))
            connection.execute("UPDATE early_access_grants SET granted_at=?,expires_at=?",
                               (storage_time(self.now), storage_time(self.now + timedelta(days=30))))
            connection.execute("UPDATE goals SET last_lead_step_at=?", (storage_time(self.now),))
        self.leadership = GoalLeadershipService(self.db, lambda: self.now, self.goals, self.members, self.sessions)
        self.cli_temp = Path(self.temp.name) / "cli-temp"
        self.cli_temp.mkdir()

    def cli(self, *args, expected=0):
        # Do not inherit storage credentials, mail settings, or another database
        # from the developer/runner's shell. Every command touches this fixture.
        environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
                       if key in os.environ}
        environment.update({"APP_ENV": "staging", "PUBLIC_BASE_URL": "https://fictional.example.test",
            "DATABASE_PATH": str(self.db.path.resolve()), "SESSION_SECRET": "fictional-goal-admin-" + "x" * 40,
            "SIGNUPS": "closed", "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "false",
            "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(self.cli_temp), "TMP": str(self.cli_temp)})
        isolated = ["-S"] if sys.flags.no_site else []
        result = subprocess.run([sys.executable, *isolated, "-m", "connectthebots_server.admin", "goals", *args],
                                cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(environment["SESSION_SECRET"], result.stdout + result.stderr)
        if expected == 0:
            self.assertEqual(result.stderr, "")
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, "")
        return result.stderr

    def volunteer(self, user="other"):
        preview = self.leadership.preview(user, self.slug)
        self.leadership.volunteer(user, self.slug, {"charter_revision": preview["charter_revision"],
            "notice_version": preview["notice_version"], "approved_by_owner": True})
        return preview["handle"]

    def lead_id(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT lead_user_id FROM goals WHERE slug=?", (self.slug,)).fetchone()[0]


class GoalAdminTests(GoalAdminFixture):
    def test_private_queues_and_moderation_dispatch_work_when_feature_off(self):
        held = self.append(status="held", reasons=["sensitive_claim"])
        reported = self.append()
        self.report(reported)
        self.setting("feature.goals", "off")
        queue = self.cli("held")
        self.assertEqual(queue["entries"][0]["id"], held["id"])
        self.assertEqual(queue["entries"][0]["reasons"], ["sensitive_claim"])
        self.assertIsNotNone(queue["entries"][0]["content"])
        reports = self.cli("reports")["reports"]
        self.assertEqual(reports[0]["reporter_id"], "other")
        self.assertIn("private reporter canary", reports[0]["details"])
        reason = "The operator checked the fictional source and recorded this decision."
        self.assertEqual(self.cli("release", held["id"], reason)["entry"]["status"], "visible")
        self.assertEqual(self.cli("hide", held["id"], reason)["entry"]["status"], "hidden")
        self.assertEqual(self.cli("unhide", held["id"], reason)["entry"]["status"], "visible")
        self.setting("feature.goals", "everyone")
        record = self.goals.public_record(self.slug)["entries"]
        public = json.dumps(record)
        self.assertIn(reason, public)
        self.assertNotIn("private reporter canary", public)
        self.assertNotIn("reporter_id", public)
        self.assertTrue(verify_record(record)["valid"])

    def test_assignment_dispatch_requires_role_approval_and_clears_scoped_work_permission(self):
        self.approve("lead")
        self.approve("other")
        self.goals.operator_action(self.slug, "pause", "Fictional pause for a reviewed leadership change.")
        with self.db.connect() as connection:
            handle = self.members.member(connection, self.goals._goal(connection, self.slug), "other")["handle"]
        self.cli("assign-lead", self.slug, handle, "Assign this fictional volunteer after review.", expected=2)
        self.assertEqual(self.lead_id(), "lead")
        self.assertEqual(self.volunteer(), handle)
        reason = "The operator accepted the fictional contributor's approved role."
        self.setting("feature.goals", "off")
        result = self.cli("assign-lead", self.slug, handle, reason)
        self.assertEqual(result["goal"]["state"], "open")
        self.assertEqual(self.lead_id(), "other")
        with self.db.connect() as connection:
            allowances = connection.execute("SELECT * FROM goal_allocations WHERE goal_id=(SELECT id FROM goals WHERE slug=?)", (self.slug,)).fetchall()
            self.assertEqual(len(allowances), 2)
            self.assertTrue(all(row["stopped_at"] is not None and row["lead_approved_at"] is None for row in allowances))
        self.setting("feature.goals", "everyone")
        event = self.goals.public_record(self.slug)["entries"][-1]["content"]
        self.assertEqual(event, {"action": "assign_lead", "handle": handle, "reason": reason})
        for private in ("user_id", "handover_id", "approved_at", "notice_version"):
            self.assertNotIn(private, event)

    def test_assignment_rechecks_revoked_role_approval_without_consuming_or_reassigning(self):
        self.goals.operator_action(self.slug, "pause", "Fictional pause for a role approval check.")
        handle = self.volunteer()
        self.leadership.withdraw("other", self.slug)
        original = self.goals.public_record(self.slug)
        error = self.cli("assign-lead", self.slug, handle, "A revoked approval must not assign this fictional lead.", expected=2)
        self.assertIn("approval", error)
        self.assertEqual(self.lead_id(), "lead")
        self.assertEqual(self.goals.public_record(self.slug), original)

    def test_reason_is_required_for_new_mutating_commands_and_invalid_target_is_safe(self):
        entry = self.append()
        before = self.goals.public_record(self.slug)
        for command in (("release", entry["id"]), ("hide", entry["id"]), ("unhide", entry["id"]),
                        ("resume", self.slug), ("assign-lead", self.slug, "c_fictional")):
            with self.subTest(command=command[0]):
                self.assertIn("reason", self.cli(*command, expected=2))
        private_reason = "Fictional private canary should not be echoed for an invalid target."
        error = self.cli("hide", "e_" + "a" * 64, private_reason, expected=2)
        self.assertNotIn(private_reason, error)
        self.assertEqual(self.goals.public_record(self.slug), before)

    def test_recovery_hold_refuses_cli_mutation_and_preserves_held_content(self):
        entry = self.append(status="held", reasons=["sensitive_claim"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        error = self.cli("release", entry["id"], "A recovery hold must block this fictional release.", expected=2)
        self.assertIn("Recovery review", error)
        self.assertEqual(self.raw(entry)["status"], "held")
        self.assertIsNotNone(self.raw(entry)["content_json"])

    def test_cli_cannot_unhide_or_release_withdrawn_content(self):
        entry = self.append()
        self.action(entry, "hide")
        self.goals.withdrawal("lead", entry["id"])
        for action in ("unhide", "release"):
            with self.subTest(action=action):
                self.cli(action, entry["id"], "Erased fictional content cannot be reconstructed.", expected=2)
        self.assertIsNone(self.raw(entry)["content_json"])
        self.assertEqual(self.raw(entry)["entry_sha256"], entry["entry_sha256"])

    def test_resume_dispatch_rechecks_current_public_charter(self):
        self.goals.operator_action(self.slug, "pause", "Fictional operator pause before reviewed resume.")
        reason = "The operator reviewed the unchanged fictional charter and resumed it."
        self.assertEqual(self.cli("resume", self.slug, reason)["goal"]["state"], "open")
        entry = next(item for item in self.goals.public_record(self.slug)["entries"] if item["kind"] == "charter")
        self.action(entry, "hide")
        self.cli("resume", self.slug, "A hidden charter cannot resume this fictional goal.", expected=2)
        self.assertEqual(self.goals.detail(None, self.slug)["goal"]["state"], "paused")
