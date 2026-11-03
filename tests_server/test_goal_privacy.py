"""Goal export, withdrawal and atomic erasure, using fictional owner content."""
from datetime import timedelta
from hashlib import sha256
import json
import unittest

from connectthebots_server import growth_config
from connectthebots_server.agent_common import AgentError
from connectthebots_server.goal_record import canonical_json, verify_record
from connectthebots_server.goals import GoalsService
from connectthebots_server.recovery import RecoveryError, RecoveryService
from connectthebots_server.recovery_authority import AuthorityError, RecoveryAuthority, local_state
from connectthebots_server.sessions import storage_time
from tests_server import test_account_data as account_cases
from tests_server import test_goals as goal_cases
from tests_server import test_offsite_recovery as offsite_cases


@unittest.skipUnless(account_cases.AVAILABLE, "optional server extra not installed")
class GoalPrivacyTests(unittest.TestCase):
    def setUp(self):
        # Compose the HTTP fixture rather than inheriting and repeating all of
        # its existing account-control tests in this module.
        self.account = account_cases.AccountDataTests("runTest")
        self.addCleanup(self.account.doCleanups)
        self.account.setUp()
        self.db, self.app = self.account.db, self.account.app
        self.clock = lambda: self.account.now
        self.goals = GoalsService(self.db, self.clock)
        with self.db.transaction() as connection:
            growth_config.store(connection, "feature.goals", "everyone", self.clock())
            for owner in ("ama", "joon"):
                connection.execute("UPDATE users SET created_at=? WHERE id=?",
                                   (storage_time(self.clock() - timedelta(days=30)), owner))
                connection.execute(
                    "INSERT INTO early_access_grants(user_id,source,reference,granted_at,expires_at) VALUES(?,?,?,?,?)",
                    (owner, "referral_perk", "fictional-privacy", storage_time(self.clock()),
                     storage_time(self.clock() + timedelta(days=30))))

    def propose(self, owner="ama", *, category="mathematics"):
        charter = goal_cases.fictional_charter("Fictional: " + owner + " clock puzzle", category)
        charter["aim"] = "Fictional " + owner + " authored aim canary for projection erasure."
        charter["tasks"][0]["question"] = "How can the fictional " + owner + " task canary be reproduced?"
        return self.goals.propose(owner, charter, True)["goal"]

    def entries(self, slug):
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT e.* FROM goal_entries e JOIN goals g ON g.id=e.goal_id WHERE g.slug=? ORDER BY e.seq",
                (slug,))]

    def append(self, slug, owner, kind, content, *, refs=(), status="visible"):
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection, slug)
            return self.goals._append(connection, goal, kind, {"ama": "c_00000001", "joon": "c_00000002", "sol": "c_00000003"}[owner],
                                      content, self.clock(), refs=refs, author_user_id=owner, status=status)

    def open(self, *, category="mathematics"):
        goal = self.propose(category=category)
        if category != "mathematics":
            with self.db.transaction() as connection:
                growth_config.store(connection, "goals_sensitive_open_max", 1, self.clock())
        self.goals.operator_action(goal["slug"], "approve", "Fictional charter approved for privacy checking.")
        return goal["slug"]

    def assert_chain(self, slug):
        previous = "0" * 64
        for sequence, row in enumerate(self.entries(slug), 1):
            self.assertEqual(row["seq"], sequence)
            self.assertEqual(row["previous_sha256"], previous)
            canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=True).encode("ascii")
            if row["content_json"] is not None:
                self.assertEqual(sha256(canonical(json.loads(row["content_json"]))).hexdigest(),
                                 row["content_sha256"])
            envelope = {"goal": slug, "seq": sequence, "kind": row["kind"],
                        "author": row["author_handle"], "created_at": row["created_at"],
                        "refs": json.loads(row["refs_json"]), "content_sha256": row["content_sha256"],
                        "previous": previous}
            self.assertEqual(sha256(canonical(envelope)).hexdigest(), row["entry_sha256"])
            previous = row["entry_sha256"]

    def test_account_export_contains_only_own_goal_content_even_when_feature_off(self):
        own, peer = self.propose(), self.propose("joon")
        with self.db.transaction() as connection:
            growth_config.store(connection, "feature.goals", "off", self.clock())
        result = self.account.post("/app/account/export")
        self.assertEqual(result.status_code, 200, result.text)
        exported = json.dumps(result.json()["common_goals"])
        self.assertIn(own["slug"], exported)
        self.assertIn("Fictional ama authored aim canary", exported)
        self.assertNotIn(peer["slug"], exported)
        self.assertNotIn("Fictional joon authored aim canary", exported)
        self.assertNotIn(self.account.emails["joon"], exported)

    def test_report_export_contains_only_owners_reports_with_feature_off(self):
        own, peer = self.propose(), self.propose("joon")
        for goal in (own, peer):
            self.goals.operator_action(goal["slug"], "approve", "Fictional charter approved for report testing.")
        own_entry, peer_entry = self.entries(own["slug"])[0], self.entries(peer["slug"])[0]
        created = storage_time(self.clock())
        report = {"id": "r_fictional_ama", "entry_id": peer_entry["id"], "reason": "privacy",
                  "details": "Fictional ama private report canary.", "created_at": created, "reviewed_at": None}
        # Seed fictional report rows to verify private owner scope and the new
        # reviewed-state metadata without involving another owner's content.
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO goal_reports(id,entry_id,reporter_id,reason,details,created_at) VALUES(?,?,?,?,?,?)",
                (report["id"], report["entry_id"], "ama", report["reason"], report["details"], created))
            connection.execute(
                "INSERT INTO goal_reports(id,entry_id,reporter_id,reason,details,created_at) VALUES(?,?,?,?,?,?)",
                ("r_fictional_joon", own_entry["id"], "joon", "misinformation",
                 "Fictional joon private report canary.", created))
            growth_config.store(connection, "feature.goals", "off", self.clock())
        direct = self.goals.export_owner("ama")
        self.assertEqual(direct["reports"], [report])
        response = self.account.post("/app/account/export")
        self.assertEqual(response.status_code, 200, response.text)
        exported = response.json()["common_goals"]
        self.assertEqual(exported["reports"], [report])
        for data in (direct, exported):
            serialized = json.dumps(data)
            self.assertNotIn("r_fictional_joon", serialized)
            self.assertNotIn("Fictional joon private report canary", serialized)
            self.assertNotIn("reporter_id", serialized)

    def test_dashboard_deletion_erases_projections_but_preserves_chain_and_peer_content(self):
        own, peer = self.propose(), self.propose("joon")
        self.goals.operator_action(own["slug"], "approve", "Fictional charter approved after review.")
        original = self.entries(own["slug"])
        with self.db.transaction() as connection:
            growth_config.store(connection, "feature.goals", "off", self.clock())
        result = self.account.post("/app/account/delete", confirmation="DELETE")
        self.assertEqual(result.status_code, 200, result.text)
        after = self.entries(own["slug"])
        by_id = {row["id"]: row for row in after}
        for row in original:
            erased = by_id[row["id"]]
            if row["author_user_id"] == "ama":
                self.assertIsNone(erased["content_json"])
                self.assertIsNone(erased["author_user_id"])
            else:
                self.assertEqual(erased["content_json"], row["content_json"])
            self.assertEqual(erased["content_sha256"], row["content_sha256"])
            self.assertEqual(erased["entry_sha256"], row["entry_sha256"])
        self.assertGreater(len(after), len(original))
        with self.db.connect() as connection:
            goal = connection.execute("SELECT * FROM goals WHERE slug=?", (own["slug"],)).fetchone()
            self.assertEqual(goal["state"], "paused")
            self.assertIsNone(goal["lead_user_id"])
            self.assertIsNone(goal["charter_json"])
            self.assertIsNone(goal["pending_charter_json"])
            self.assertFalse(connection.execute(
                "SELECT 1 FROM goal_tasks WHERE goal_id=? AND task_json IS NOT NULL", (goal["id"],)).fetchone())
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIn("Fictional joon authored aim canary", json.dumps(self.goals.export_owner("joon")))
        self.assert_chain(own["slug"])
        self.assert_chain(peer["slug"])

    def test_pending_charter_and_task_copies_are_erased_with_their_author(self):
        own = self.propose(category="animal_health")
        with self.db.transaction() as connection:
            growth_config.store(connection, "goals_sensitive_open_max", 1, self.clock())
        self.goals.operator_action(own["slug"], "approve", "Fictional sensitive charter reviewed.")
        changed = goal_cases.fictional_charter("Fictional: ama clock puzzle", "animal_health")
        changed["aim"] = "Fictional pending charter canary awaiting operator review."
        self.goals.revise_charter("ama", own["slug"], changed, True)
        with self.db.connect() as connection:
            self.assertIn("Fictional pending charter canary", connection.execute(
                "SELECT pending_charter_json FROM goals WHERE slug=?", (own["slug"],)).fetchone()[0])
        self.app.state.lifecycle.delete_account("ama")
        with self.db.connect() as connection:
            goal = connection.execute("SELECT id,charter_json,pending_charter_json FROM goals WHERE slug=?",
                                      (own["slug"],)).fetchone()
            self.assertIsNone(goal["charter_json"])
            self.assertIsNone(goal["pending_charter_json"])
            self.assertFalse(connection.execute("SELECT 1 FROM goal_tasks WHERE goal_id=? AND task_json IS NOT NULL",
                                                (goal["id"],)).fetchone())
        serialized = json.dumps(self.entries(own["slug"]))
        self.assertNotIn("Fictional ama authored aim canary", serialized)
        self.assertNotIn("Fictional pending charter canary", serialized)
        self.assert_chain(own["slug"])

    def test_erasure_uses_callers_transaction_and_rolls_back_with_account(self):
        own = self.propose()
        original = self.entries(own["slug"])
        with self.assertRaisesRegex(RuntimeError, "fictional rollback"):
            with self.db.transaction() as connection:
                self.app.state.lifecycle.delete_account("ama", connection=connection)
                self.assertIsNone(connection.execute("SELECT 1 FROM users WHERE id='ama'").fetchone())
                raise RuntimeError("fictional rollback")
        self.assertEqual(self.entries(own["slug"]), original)
        with self.db.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM users WHERE id='ama'").fetchone())
            self.assertIn("Fictional ama authored aim canary", connection.execute(
                "SELECT charter_json FROM goals WHERE slug=?", (own["slug"],)).fetchone()[0])

    def enroll(self):
        self.storage = offsite_cases.MemoryStorage()
        self.authority = RecoveryAuthority(self.storage, b"f" * 32, "ctb-" + "d" * 32 + "/")
        self.authority.enroll(self.db)

    def authority_state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    def test_ordinary_creation_uses_no_marker_and_erasure_advances_once(self):
        self.enroll()
        before = self.authority_state()
        own = self.propose()
        self.assertEqual(self.authority_state()["generation"], before["generation"])
        recovery = RecoveryService(self.db, self.clock, self.app.state.settings)
        snapshot_state = recovery._validate(self.db.path)
        self.app.state.lifecycle.delete_account("ama")
        after = self.authority_state()
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertEqual(after["growth_generation"], before["growth_generation"] + 1)
        self.authority.require(after)
        with self.assertRaises(RecoveryError):
            recovery._authority_matches(snapshot_state, recovery._validate(self.db.path))
        self.assert_chain(own["slug"])

    def test_storage_failure_rolls_back_account_and_goal_erasure_together(self):
        own = self.propose()
        original = self.entries(own["slug"])
        self.enroll()
        before = self.authority_state()
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.app.state.lifecycle.delete_account("ama")
        self.storage.unavailable = False
        self.assertEqual(self.authority_state(), before)
        self.assertEqual(self.entries(own["slug"]), original)
        with self.db.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM users WHERE id='ama'").fetchone())
        self.assertIn("Fictional ama authored aim canary", json.dumps(self.goals.export_owner("ama")))

    def test_withdrawal_needs_only_the_author_and_survives_feature_off_and_record_cap(self):
        slug = self.open()
        note = self.append(slug, "ama", "note", {"text": "Fictional withdrawn note canary."})
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO report_keys(id,user_id,key_hash,last_four,name,created_at) VALUES('fictional-key','joon','fictional-hash','0000','Fictional',?)", (storage_time(self.clock()),))
            connection.execute("INSERT INTO agent_receipts(key_id,nonce_hash,body_hash,response_json,status,created_at) VALUES('fictional-key','fictional-nonce','fictional-body',?,200,?)",
                               (json.dumps(note), storage_time(self.clock())))
        with self.assertRaises(AgentError) as error:
            self.goals.withdrawal("joon", note["id"])
        self.assertEqual(error.exception.status, 404)
        for number in range(10):
            self.append(slug, "ama", "note", {"text": "Fictional cap filler.", "number": number})
        with self.db.transaction() as connection:
            growth_config.store(connection, "goal_entries_per_goal_per_week", 10, self.clock())
            growth_config.store(connection, "feature.goals", "off", self.clock())
        self.assertEqual(self.goals.owned_entry("ama", note["id"])["content"], note["content"])
        with self.assertRaises(AgentError) as error:
            self.goals.withdrawal("ama", note["id"])
        self.assertEqual(error.exception.status, 404)
        withdrawn = self.goals.withdrawal("ama", note["id"], owner_control=True)["entry"]
        self.assertIsNone(withdrawn["content"])
        self.assertEqual(withdrawn["status"], "withdrawn")
        self.assertEqual(withdrawn["entry_sha256"], note["entry_sha256"])
        with self.db.connect() as connection:
            receipt = connection.execute("SELECT nonce_hash,body_hash,response_json FROM agent_receipts WHERE key_id='fictional-key'").fetchone()
            self.assertEqual(tuple(receipt), ("fictional-nonce", "fictional-body", "{}"))
        before = self.entries(slug)
        self.goals.withdrawal("ama", note["id"], owner_control=True)
        self.assertEqual(self.entries(slug), before)
        self.assert_chain(slug)

    def test_charter_withdrawal_removes_all_its_authored_task_copies_and_preserves_peer_work(self):
        slug = self.open()
        peer = self.append(slug, "joon", "note", {"text": "Fictional independent peer note remains."})
        original = self.entries(slug)
        charter = next(row for row in original if row["kind"] == "charter")
        self.goals.withdrawal("ama", charter["id"])
        with self.db.connect() as connection:
            goal = self.goals._goal(connection, slug)
            self.assertIsNone(goal["charter_json"])
            self.assertEqual(goal["topic_key"], "")
            self.assertEqual(goal["state"], "paused")
            self.assertTrue(goal["review_pending"])
            self.assertFalse(connection.execute("SELECT 1 FROM goal_tasks WHERE goal_id=? AND task_json IS NOT NULL", (goal["id"],)).fetchone())
        public = self.goals.public_record(slug)["entries"]
        self.assertEqual(next(row for row in public if row["id"] == peer["id"])["content"], peer["content"])
        self.assertTrue(verify_record(public)["valid"])
        serialized = json.dumps({"record": public, "detail": self.goals.detail(None, slug),
                                 "index": self.goals.index(), "export": self.goals.export_owner("ama")})
        self.assertNotIn("authored aim canary", serialized)
        self.assertNotIn("task canary", serialized)
        for row in original:
            after = next(entry for entry in public if entry["id"] == row["id"])
            self.assertEqual(after["entry_sha256"], row["entry_sha256"])
        with self.assertRaises(AgentError) as error:
            self.goals.operator_action(slug, "approve", "Cannot restore erased charter content.")
        self.assertEqual(error.exception.status, 409)

    def test_old_task_withdrawal_erases_embedded_charter_copy_but_preserves_replacement_task(self):
        slug = self.open()
        source = next(row for row in self.entries(slug) if row["kind"] == "step" and "task_added" in row["content_json"])
        old = json.loads(source["content_json"])
        changed = {**old["task"], "question": "Which different exact proof checks the fictional replacement?"}
        self.append(slug, "ama", "step", {"action": "task_changed", "task_id": old["task_id"], "task": changed})
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_tasks SET task_json=? WHERE id=?", (canonical_json(changed), old["task_id"]))
        self.goals.withdrawal("ama", source["id"])
        detail = self.goals.detail(None, slug)["goal"]
        self.assertIsNone(detail["charter"])
        self.assertEqual(detail["open_tasks"][0]["question"], changed["question"])
        self.assertNotIn(old["task"]["question"], json.dumps(self.goals.public_record(slug)))
        self.assert_chain(slug)

    def test_summary_projection_drops_unavailable_sources_without_erasing_independent_author(self):
        slug = self.open()
        finding = self.append(slug, "joon", "finding", {"claim": "Fictional source synthesis canary."})
        summary = self.append(slug, "ama", "summary", {"text": "Fictional source synthesis canary, discussed independently.",
                              "finding_ids": [finding["id"]], "disputed": []}, refs=[finding["id"]])
        self.assertEqual(self.goals.detail(None, slug)["goal"]["latest_summary"]["id"], summary["id"])
        self.goals.withdrawal("joon", finding["id"])
        public = self.goals.public_record(slug)["entries"]
        self.assertIsNone(next(row for row in public if row["id"] == summary["id"])["content"])
        self.assertIsNone(self.goals.detail("ama", slug)["goal"]["latest_summary"])
        own = self.goals.export_owner("ama")
        self.assertIsNone(own["goals_led"][0]["latest_summary"])
        self.assertEqual(next(row for row in own["entries"] if row["id"] == summary["id"])["content"], summary["content"])
        self.assertNotIn(summary["id"], {entry["id"] for entry in self.goals.export_owner("joon")["entries"]})
        with self.db.connect() as connection:
            stored = connection.execute("SELECT content_json,entry_sha256,status FROM goal_entries WHERE id=?", (summary["id"],)).fetchone()
            self.assertEqual(json.loads(stored["content_json"]), summary["content"])
            self.assertEqual(stored["entry_sha256"], summary["entry_sha256"])
            self.assertEqual(stored["status"], "visible")
        self.assertTrue(verify_record(public)["valid"])

    def test_pending_sensitive_record_releases_only_current_support_and_hold_always_wins(self):
        slug = self.open(category="animal_health")
        finding = self.append(slug, "joon", "finding", {"claim": "Fictional sensitive finding canary."}, status="pending_peer_check")
        def projected(viewer=None):
            return next(entry for entry in self.goals.record(viewer, slug)["entries"] if entry["id"] == finding["id"])
        self.assertIsNone(projected()["content"])
        self.assertIsNone(projected("ama")["content"])
        self.assertEqual(projected("joon")["content"], finding["content"])
        dissent = self.append(slug, "ama", "review", {"finding_id": finding["id"], "verdict": "does_not_support",
                              "checked": ["fictional arithmetic"], "note": "Fictional private dissent canary."}, refs=[finding["id"]])
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO goal_review_checks(entry_id,finding_id,reviewer_handle,reviewer_user_id,independent) VALUES(?,?,?,?,1)",
                               (dissent["id"], finding["id"], dissent["author"], "ama"))
        def downloaded():
            response = self.account.client.get(f"/goals/{slug}/record.jsonl")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(verify_record([json.loads(line) for line in response.text.splitlines()])["valid"])
            return response.text
        self.assertNotIn("private dissent canary", downloaded())
        self.assertIn("private dissent canary", json.dumps(self.goals.record("ama", slug)))
        # A separate fictional independent reviewer supplies the first support.
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                               ("sol", "fictional-sol", "sol@fictional.example.test", "Fictional Sol", storage_time(self.clock()), storage_time(self.clock())))
        review = self.append(slug, "sol", "review", {"finding_id": finding["id"], "verdict": "supports", "checked": ["fictional arithmetic"], "note": "Fictional check."}, refs=[finding["id"]])
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO goal_review_checks(entry_id,finding_id,reviewer_handle,reviewer_user_id,independent) VALUES(?,?,?,?,1)",
                               (review["id"], finding["id"], review["author"], "sol"))
        self.assertEqual(projected()["status"], "pending_peer_check")
        self.assertEqual(projected()["finding_status"], "disputed")
        self.assertEqual(projected()["content"], finding["content"])
        self.assertIn("private dissent canary", downloaded())
        summary = self.append(slug, "ama", "summary", {"text": "Fictional sensitive finding canary synthesized.",
                              "finding_ids": [], "disputed": [finding["id"]]}, refs=[finding["id"]])
        self.assertEqual(self.goals.detail(None, slug)["goal"]["latest_summary"]["id"], summary["id"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE id=?", (finding["id"],))
        self.assertIsNone(projected()["content"])
        self.assertEqual(projected("joon")["content"], finding["content"])
        self.assertNotIn("private dissent canary", downloaded())
        self.assertNotIn("sensitive finding canary", downloaded())
        self.assertIsNone(self.goals.detail("ama", slug)["goal"]["latest_summary"])
        self.assertIsNone(self.goals.export_owner("ama")["goals_led"][0]["latest_summary"])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='pending_peer_check' WHERE id=?", (finding["id"],))
        self.goals.withdrawal("sol", review["id"])
        self.assertIsNone(projected()["content"])
        self.assertEqual(projected()["finding_status"], "disputed")
        self.assertTrue(verify_record(self.goals.public_record(slug)["entries"])["valid"])

    def test_transitive_source_hold_redacts_work_projections_but_keeps_other_authors_entries(self):
        slug = self.open()
        source = self.append(slug, "joon", "finding", {"claim": "Fictional transitive source canary."})
        child = self.append(slug, "ama", "finding", {"claim": "Fictional transitive source canary synthesized by a different author."}, refs=[source["id"]])
        review = self.append(slug, "joon", "review", {"finding_id": child["id"], "verdict": "supports", "note": "Fictional transitive source canary checked.", "checked": ["arithmetic"]}, refs=[child["id"]])
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO goal_review_checks(entry_id,finding_id,reviewer_handle,reviewer_user_id,independent) VALUES(?,?,?,?,1)",
                               (review["id"], child["id"], review["author"], "joon"))
            task = connection.execute("SELECT * FROM goal_tasks WHERE goal_id=(SELECT id FROM goals WHERE slug=?) AND state='open'", (slug,)).fetchone()
            changed = {**json.loads(task["task_json"]), "question": "Fictional transitive source canary is the changed task question."}
            connection.execute("UPDATE goal_tasks SET task_json=? WHERE id=?", (canonical_json(changed), task["id"]))
        step = self.append(slug, "ama", "step", {"action": "task_changed", "task_id": task["id"], "task": changed}, refs=[child["id"]])
        summary = self.append(slug, "ama", "summary", {"text": "Fictional transitive source canary synthesized.", "finding_ids": [child["id"]], "disputed": []}, refs=[child["id"]])
        self.assertIn("transitive source canary", json.dumps(self.goals.detail(None, slug)))
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE id=?", (source["id"],))
        for change in ("held", "withdrawn"):
            if change == "withdrawn":
                self.goals.withdrawal("joon", source["id"])
            record = self.goals.public_record(slug)["entries"]
            for entry in (source, child, review, step, summary):
                self.assertIsNone(next(row for row in record if row["id"] == entry["id"])["content"])
            self.assertNotIn("transitive source canary", json.dumps(self.goals.detail(None, slug)))
            download = self.account.client.get(f"/goals/{slug}/record.jsonl")
            self.assertEqual(download.status_code, 200)
            self.assertNotIn("transitive source canary", download.text)
            self.assertTrue(verify_record(record)["valid"])
            own = self.goals.export_owner("ama")
            self.assertIsNone(own["goals_led"][0]["latest_summary"])
            self.assertEqual(next(row for row in own["entries"] if row["id"] == child["id"])["content"], child["content"])
        with self.db.connect() as connection:
            for entry in (child, step, summary):
                stored = connection.execute("SELECT content_json,entry_sha256 FROM goal_entries WHERE id=?", (entry["id"],)).fetchone()
                self.assertEqual(json.loads(stored["content_json"]), entry["content"])
                self.assertEqual(stored["entry_sha256"], entry["entry_sha256"])

    def test_withdrawal_is_one_protected_commit_and_outage_or_hold_cannot_partly_erase(self):
        slug = self.open()
        charter = next(row for row in self.entries(slug) if row["kind"] == "charter")
        self.enroll()
        before, original = self.authority_state(), self.entries(slug)
        self.storage.unavailable = True
        with self.assertRaises(AuthorityError):
            self.goals.withdrawal("ama", charter["id"])
        self.storage.unavailable = False
        self.assertEqual(self.entries(slug), original)
        self.assertEqual(self.authority_state(), before)
        self.goals.withdrawal("ama", charter["id"])
        after = self.authority_state()
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertEqual(after["growth_generation"], before["growth_generation"] + 1)
        self.assert_chain(slug)
        other = self.append(slug, "ama", "note", {"text": "Fictional held erasure target."})
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        with self.assertRaises(AgentError) as error:
            self.goals.withdrawal("ama", other["id"], owner_control=True)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.goals.owned_entry("ama", other["id"])["content"], other["content"])


if __name__ == "__main__":
    unittest.main()
