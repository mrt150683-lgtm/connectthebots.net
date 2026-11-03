"""Common-goal charter, operator and HTTP acceptance with fictional owners."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from connectthebots_server import growth_config
from connectthebots_server.agent_common import AgentError
from connectthebots_server.db import Database
from connectthebots_server.goals import GoalsService
from connectthebots_server.recovery_authority import local_state
from connectthebots_server.sessions import storage_time

SERVER = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if SERVER:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings


def fictional_charter(title="Fictional: tidal clock puzzle", category="mathematics"):
    return {
        "title": title,
        "aim": "Find a reproducible solution to a fictional tidal clock puzzle.",
        "in_scope": ["Exact arithmetic and independently reproducible proofs."],
        "out_of_scope": ["Claims about actual tides or identifiable people."],
        "category": category,
        "success_signals": ["An exact derivation checked by independent contributors."],
        "tasks": [{"title": "Prove the fictional clock period", "kind": "proof",
                   "question": "Which exact period solves the fictional clock puzzle?",
                   "deliverable": "A derivation with every arithmetic step reproducible.",
                   "done_when": "Another contributor can independently repeat the derivation.",
                   "capabilities": ["mathematics"]}],
        "joining": {}, "membership": "open",
    }


def add_fictional_users(database, now):
    with database.transaction() as connection:
        for owner in ("lead", "other", "third"):
            connection.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                (owner, "fictional-google-" + owner, owner + "@fictional.example.test",
                 "Private fictional " + owner, storage_time(now - timedelta(days=30)), storage_time(now)))
            connection.execute(
                "INSERT INTO early_access_grants(user_id,source,reference,granted_at,expires_at) VALUES(?,?,?,?,?)",
                (owner, "referral_perk", "fictional-launch", storage_time(now), storage_time(now + timedelta(days=30))))


class GoalFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.db = Database(Path(self.temp.name) / "goals.db", lambda: self.now)
        self.db.migrate()
        add_fictional_users(self.db, self.now)
        self.goals = GoalsService(self.db, lambda: self.now)
        self.setting("feature.goals", "everyone")

    def setting(self, key, value):
        with self.db.transaction() as connection:
            growth_config.store(connection, key, value, self.now)

    def propose(self, owner="lead", charter=None):
        return self.goals.propose(owner, charter or fictional_charter(), True)["goal"]

    def open_goal(self, owner="lead", charter=None):
        goal = self.propose(owner, charter)
        self.goals.operator_action(goal["slug"], "approve", "Fictional operator reviewed the charter.")
        return self.goals.detail(owner, goal["slug"])["goal"]

    def error(self, status, operation, *args, **kwargs):
        with self.assertRaises(AgentError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        return caught.exception


class GoalCharterTests(GoalFixture):
    def test_exact_owner_approval_is_required_and_no_row_is_created(self):
        for approval in (None, False, 1, "true", {}):
            with self.subTest(approval=approval):
                self.error(409, self.goals.propose, "lead", fictional_charter(), approval)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goals").fetchone()[0], 0)

    def test_every_charter_field_has_deterministic_validation(self):
        invalid = [
            ("title", "tiny"), ("title", "x" * 81), ("title", "bad\u200b title"),
            ("aim", "too short"), ("aim", "x" * 601),
            ("in_scope", []), ("in_scope", ["tiny"]), ("in_scope", ["valid scope"] * 11),
            ("out_of_scope", []), ("out_of_scope", ["x" * 201]),
            ("category", "unrecognized"), ("membership", "secret"),
            ("success_signals", []), ("success_signals", ["valid signal"] * 6),
            ("tasks", []), ("tasks", [fictional_charter()["tasks"][0]] * 51),
            ("joining", []), ("licence", {"text": "CC0", "code": "Apache-2.0"}),
            ("unexpected", "never accepted"),
        ]
        for field, value in invalid:
            with self.subTest(field=field, value=str(value)[:80]):
                charter = fictional_charter()
                charter[field] = value
                self.error(422, self.goals.propose, "lead", charter, True)
        for field in fictional_charter():
            with self.subTest(missing=field):
                charter = fictional_charter()
                del charter[field]
                self.error(422, self.goals.propose, "lead", charter, True)
        for field, value in (("title", "tiny"), ("kind", "execute"), ("question", "short"),
                             ("deliverable", "short"), ("done_when", "short"),
                             ("capabilities", []), ("capabilities", ["mind_reading"]),
                             ("unexpected", "field")):
            with self.subTest(task_field=field):
                charter = fictional_charter()
                charter["tasks"][0][field] = value
                self.error(422, self.goals.propose, "lead", charter, True)

    def test_forbidden_joining_criteria_and_invalid_menu_values_are_rejected(self):
        for key in ("politics", "age", "sex", "location", "personality", "personality_fingerprint", "match_settings"):
            charter = fictional_charter()
            charter["joining"] = {key: "fictional"}
            with self.subTest(key=key):
                self.error(422, self.goals.propose, "lead", charter, True)
        for joining in ({"interview_complete": 1}, {"account_age_days": -1},
                        {"capabilities": {"any_of": ["mind_reading"]}},
                        {"capabilities": {"any_of": ["coding"], "all_of": ["design"]}},
                        {"interests": [{"topic": "politics", "item": "fictional"}]}):
            with self.subTest(joining=joining):
                charter = fictional_charter()
                charter["joining"] = joining
                self.error(422, self.goals.propose, "lead", charter, True)

    def test_refused_work_and_contact_details_never_create_a_goal(self):
        for aim in ("Build fictional weapons for testing this refusal.",
                    "Study fictional pathogen work for this refusal.",
                    "Develop fictional explosives for this refusal.",
                    "Design surveillance of people for this refusal.",
                    "Develop a cyber attack for this fictional refusal.",
                    "Coordinate election persuasion for this fictional refusal.",
                    "Write to fictional-person@example.test about this puzzle."):
            with self.subTest(aim=aim):
                charter = fictional_charter()
                charter["aim"] = aim
                self.error(422, self.goals.propose, "lead", charter, True)

    def test_proposed_goal_and_record_are_private_until_operator_approval(self):
        goal = self.propose()
        slug = goal["slug"]
        self.assertEqual(goal["state"], "proposed")
        self.assertEqual(self.goals.index("other")["goals"], [])
        self.assertEqual(self.goals.index()["goals"], [])
        self.error(404, self.goals.detail, "other", slug)
        self.error(404, self.goals.detail, None, slug)
        self.error(404, self.goals.record, "other", slug)
        self.error(404, self.goals.record, None, slug)
        self.assertEqual(self.goals.record("lead", slug)["entries"][0]["kind"], "charter")
        self.goals.operator_action(slug, "approve", "Fictional scope has been reviewed.")
        self.assertEqual(self.goals.detail(None, slug)["goal"]["state"], "open")
        self.assertEqual([row["slug"] for row in self.goals.index()["goals"]], [slug])

    def test_refusal_records_reason_without_listing_the_goal(self):
        goal = self.propose()
        reason = "Fictional charter needs a narrower question."
        self.goals.operator_action(goal["slug"], "refuse", reason)
        self.assertEqual(self.goals.index()["goals"], [])
        entries = self.goals.record("lead", goal["slug"])["entries"]
        self.assertEqual(entries[-1]["kind"], "operator")
        self.assertIn(reason, json.dumps(entries[-1]["content"]))

    def test_launch_leadership_requires_current_early_access(self):
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM early_access_grants WHERE user_id='lead'")
        self.error(403, self.goals.propose, "lead", fictional_charter(), True)
        self.now += timedelta(days=31)
        self.error(403, self.goals.propose, "other", fictional_charter(), True)

    def test_first_approval_rechecks_revoked_expired_and_suspended_lead_without_mutation(self):
        cases = (("lead", "Fictional: prism geometry", "revoked", 409),
                 ("other", "Fictional: lunar arithmetic", "expired", 409),
                 ("third", "Fictional: crystal topology", "suspended", 403))
        for owner, title, change, expected_status in cases:
            with self.subTest(change=change):
                goal = self.propose(owner, fictional_charter(title))
                with self.db.transaction() as connection:
                    if change == "revoked":
                        connection.execute("DELETE FROM early_access_grants WHERE user_id=?", (owner,))
                    elif change == "expired":
                        connection.execute("UPDATE early_access_grants SET expires_at=? WHERE user_id=?", (storage_time(self.now), owner))
                    else:
                        connection.execute("UPDATE users SET suspended_at=? WHERE id=?", (storage_time(self.now), owner))
                with self.db.connect() as connection:
                    before = dict(connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone())
                    count = connection.execute("SELECT COUNT(*) FROM goal_entries WHERE goal_id=?", (before["id"],)).fetchone()[0]
                    authority = local_state(connection)
                self.error(expected_status, self.goals.operator_action, goal["slug"], "approve", "Fictional initial review.")
                with self.db.connect() as connection:
                    self.assertEqual(dict(connection.execute("SELECT * FROM goals WHERE slug=?", (goal["slug"],)).fetchone()), before)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM goal_entries WHERE goal_id=?", (before["id"],)).fetchone()[0], count)
                    self.assertEqual(local_state(connection), authority)
        self.assertEqual(self.goals.index()["goals"], [])

    def test_lead_site_and_duplicate_topic_limits(self):
        goal = self.propose()
        self.error(409, self.goals.propose, "lead", fictional_charter("Fictional: prism arithmetic puzzle"), True)
        self.goals.operator_action(goal["slug"], "approve", "Fictional approval.")
        self.error(409, self.goals.propose, "other", fictional_charter(), True)
        other = self.propose("other", fictional_charter("Fictional: prism arithmetic puzzle"))
        self.setting("goals_open_max", 1)
        queued = self.propose("third", fictional_charter("Fictional: lunar geometry puzzle"))
        self.assertEqual(queued["state"], "proposed")
        self.error(409, self.goals.operator_action, other["slug"], "approve", "Fictional review.")
        self.assertEqual(self.goals.detail("other", other["slug"])["goal"]["state"], "proposed")

    def test_sensitive_pilot_gate_and_pending_revision_keep_old_charter(self):
        sensitive = fictional_charter("Fictional: animal study catalogue", "animal_health")
        goal = self.propose(charter=sensitive)
        self.error(409, self.goals.operator_action, goal["slug"], "approve", "Fictional review.")
        self.setting("goals_sensitive_open_max", 1)
        self.goals.operator_action(goal["slug"], "approve", "Fictional later pilot authorization.")
        revised = deepcopy(sensitive)
        revised["aim"] = "Catalogue a revised set of fictional published study designs."
        self.goals.revise_charter("lead", goal["slug"], revised, True)
        shown = self.goals.detail(None, goal["slug"])["goal"]
        self.assertEqual(shown["charter"]["aim"], sensitive["aim"])
        self.assertNotIn(revised["aim"], json.dumps(shown))
        self.assertNotIn(revised["aim"], json.dumps(self.goals.record(None, goal["slug"])))
        self.goals.operator_action(goal["slug"], "approve", "Fictional revision reviewed.")
        self.assertEqual(self.goals.detail(None, goal["slug"])["goal"]["charter"]["aim"], revised["aim"])

    def test_rejected_sensitive_revision_is_hidden_and_old_charter_stays_public(self):
        self.setting("goals_sensitive_open_max", 1)
        original = fictional_charter("Fictional: animal study catalogue", "animal_health")
        goal = self.open_goal(charter=original)
        revised = deepcopy(original)
        revised["aim"] = "A fictional rejected charter canary must remain absent from public views."
        self.goals.revise_charter("lead", goal["slug"], revised, True)
        self.goals.operator_action(goal["slug"], "refuse", "Fictional revision requires a narrower scope.")
        own = self.goals.record("lead", goal["slug"])["entries"]
        entry = next(row for row in own if row["kind"] == "charter" and row["content"]
                     and row["content"]["charter"]["aim"] == revised["aim"])
        self.assertEqual(entry["status"], "hidden")
        peer = self.goals.record("other", goal["slug"])["entries"]
        self.assertIsNone(next(row for row in peer if row["id"] == entry["id"])["content"])
        public = self.goals.detail(None, goal["slug"])["goal"]
        self.assertEqual(public["charter"]["aim"], original["aim"])
        self.assertEqual(public["charter_revision"], 1)
        self.assertNotIn(revised["aim"], json.dumps([public, peer, self.goals.index()]))

    def test_declined_revisions_keep_unique_history_and_later_accept_the_submitted_revision(self):
        self.setting("goals_sensitive_open_max", 1)
        charter = fictional_charter("Fictional: animal study catalogue", "animal_health")
        goal = self.open_goal(charter=charter)
        for revision in (2, 3, 4):
            revised = deepcopy(charter)
            revised["aim"] = f"Fictional uniquely identified charter submission number {revision}."
            self.goals.revise_charter("lead", goal["slug"], revised, True)
            self.assertEqual(self.goals.detail(None, goal["slug"])["goal"]["charter_revision"], 1)
            action = "approve" if revision == 4 else "refuse"
            self.goals.operator_action(goal["slug"], action, "Fictional decision for this exact submission.")
        authored = self.goals.record("lead", goal["slug"])["entries"]
        revisions = [entry["content"]["revision"] for entry in authored
                     if entry["kind"] == "charter" and entry["content"]]
        self.assertEqual(revisions, [1, 2, 3, 4])
        accepted = self.goals.detail(None, goal["slug"])["goal"]
        self.assertEqual(accepted["charter_revision"], 4)
        self.assertEqual(accepted["charter"]["aim"], revised["aim"])
        self.assertEqual([entry["status"] for entry in authored if entry["kind"] == "charter"],
                         ["visible", "hidden", "hidden", "visible"])

    def test_operator_can_decide_pending_revision_at_record_cap_but_authored_writes_still_stop(self):
        self.setting("goals_sensitive_open_max", 1)
        self.setting("goal_entries_per_goal_per_week", 10)
        charter = fictional_charter("Fictional: animal study catalogue", "animal_health")
        goal = self.open_goal(charter=charter)
        self.assertEqual(len(self.goals.record("lead", goal["slug"])["entries"]), 3)
        for revision in (2, 3, 4):
            revised = deepcopy(charter)
            revised["aim"] = f"Fictional capped record submission number {revision} for review."
            self.goals.revise_charter("lead", goal["slug"], revised, True)
            self.goals.operator_action(goal["slug"], "refuse", "Fictional declined submission.")
        self.assertEqual(len(self.goals.record("lead", goal["slug"])["entries"]), 9)
        revised = deepcopy(charter)
        revised["aim"] = "Fictional fifth charter approved at the ordinary record cap."
        self.goals.revise_charter("lead", goal["slug"], revised, True)
        self.assertEqual(len(self.goals.record("lead", goal["slug"])["entries"]), 10)
        approved = self.goals.operator_action(goal["slug"], "approve", "Fictional operator decision at cap.")["goal"]
        self.assertEqual(approved["state"], "open")
        self.assertEqual(approved["charter_revision"], 5)
        self.assertEqual(approved["charter"]["aim"], revised["aim"])
        entries = self.goals.record("lead", goal["slug"])["entries"]
        self.assertEqual(len(entries), 13)
        self.assertEqual([entry["seq"] for entry in entries], list(range(1, 14)))
        self.assertEqual([entry["content"]["action"] for entry in entries[10:]],
                         ["task_superseded", "task_added", "approve"])
        revised["aim"] = "Fictional ordinary author cannot exceed the record submission cap."
        error = self.error(429, self.goals.revise_charter, "lead", goal["slug"], revised, True)
        self.assertGreater(error.retry_after, 0)
        self.assertEqual(self.goals.record("lead", goal["slug"])["entries"], entries)
        self.assertEqual(self.goals.detail(None, goal["slug"])["goal"]["charter_revision"], 5)

    def test_pending_sensitive_record_has_approved_banner_without_disclosing_to_peer(self):
        goal = self.open_goal()
        revised = fictional_charter(category="animal_health")
        revised["aim"] = "Fictional sensitive pending charter canary for author-only records."
        self.goals.revise_charter("lead", goal["slug"], revised, True)
        own = self.goals.record("lead", goal["slug"])
        approved_banner = ("Research notes written by members' AI assistants. Not medical, veterinary, legal or financial advice "
            "and not checked by professionals. Do not use anything here to diagnose, treat or dose a person or an animal, "
            "or to make financial or legal decisions; ask an appropriately qualified professional.")
        self.assertEqual(own.get("banner"), approved_banner)
        self.assertIn(revised["aim"], json.dumps(own))
        self.assertNotIn(revised["aim"], json.dumps(self.goals.record("other", goal["slug"])))
        self.assertNotIn(revised["aim"], json.dumps(self.goals.detail("other", goal["slug"])))

    def test_existing_goal_revision_review_does_not_require_renewing_launch_grant(self):
        self.setting("goals_sensitive_open_max", 1)
        charter = fictional_charter("Fictional: animal study catalogue", "animal_health")
        goal = self.open_goal(charter=charter)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM early_access_grants WHERE user_id='lead'")
        revised = deepcopy(charter)
        revised["aim"] = "Fictional existing lead submits a revision after the initial grant ends."
        self.goals.revise_charter("lead", goal["slug"], revised, True)
        self.goals.operator_action(goal["slug"], "approve", "Fictional review of an existing goal.")
        self.assertEqual(self.goals.detail(None, goal["slug"])["goal"]["charter_revision"], 2)

    def test_only_lead_may_revise_and_current_owner_is_rechecked(self):
        goal = self.open_goal()
        revised = fictional_charter()
        revised["aim"] = "A revised exact solution to this wholly fictional clock puzzle."
        self.error(409, self.goals.revise_charter, "lead", goal["slug"], revised, False)
        with self.assertRaises(AgentError) as denied:
            self.goals.revise_charter("other", goal["slug"], revised, True)
        self.assertIn(denied.exception.status, (403, 404))
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='lead'", (storage_time(self.now),))
        self.error(403, self.goals.revise_charter, "lead", goal["slug"], revised, True)

    def test_public_projections_use_only_per_goal_handles_and_fixed_licences(self):
        self.setting("goals_led_per_member", 2)
        first = self.open_goal()
        second = self.open_goal(charter=fictional_charter("Fictional: prism arithmetic puzzle"))
        records = [self.goals.record(None, row["slug"])["entries"] for row in (first, second)]
        handles = [entries[0]["author"] for entries in records]
        self.assertNotEqual(*handles)
        for handle in handles:
            self.assertRegex(handle, r"^c_[0-9a-f]{8}$")
        encoded = json.dumps([first, second, records, self.goals.index()])
        for private in ("fictional-google-", "@fictional.example.test", "Private fictional", "lead_user_id", "author_user_id"):
            self.assertNotIn(private, encoded)
        self.assertEqual(first["charter"]["licence"], {"text": "CC BY 4.0", "code": "Apache-2.0"})

    def test_ordinary_charter_entries_do_not_advance_recovery_but_operator_decisions_do(self):
        def state():
            with self.db.connect() as connection:
                return local_state(connection)
        before = state()
        goal = self.propose()
        after_propose = state()
        self.assertEqual(after_propose["revision"], before["revision"])
        self.goals.operator_action(goal["slug"], "approve", "Fictional approval.")
        after_approve = state()
        self.assertGreater(after_approve["revision"], after_propose["revision"])
        self.goals.operator_action(goal["slug"], "pause", "Fictional operator pause.")
        self.assertGreater(state()["revision"], after_approve["revision"])

    def test_operator_reason_is_required_and_inactive_lead_pauses_goal(self):
        goal = self.open_goal()
        self.error(422, self.goals.operator_action, goal["slug"], "pause", "")
        self.now += timedelta(days=22)
        self.assertEqual(self.goals.detail(None, goal["slug"])["goal"]["state"], "paused")


@unittest.skipUnless(SERVER, "optional server extra not installed")
class GoalHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.settings = Settings.from_env({"APP_ENV": "staging", "PUBLIC_BASE_URL": "https://goals.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "http-goals.db"), "SESSION_SECRET": "fictional-goal-secret-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open"})
        self.app = create_app(self.settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.public_base_url))
        self.db = self.app.state.database
        add_fictional_users(self.db, self.now)
        self.keys = {owner: self.app.state.reports.create_key(owner, "Fictional goal agent")["key"]
                     for owner in ("lead", "other", "third")}
        self.goals = GoalsService(self.db, lambda: self.now)

    def enable(self, value="everyone"):
        with self.db.transaction() as connection:
            growth_config.store(connection, "feature.goals", value, self.now)

    def call(self, owner, method, path, body=None, **headers):
        if owner is not None:
            headers = {"Authorization": "Bearer " + self.keys[owner], **headers}
        if body is not None:
            headers = {"Content-Type": "application/json", **headers}
            return self.client.request(method, path, content=json.dumps(body), headers=headers)
        return self.client.request(method, path, headers=headers)

    def test_feature_off_hides_every_goal_route_before_auth_body_or_retry(self):
        cases = (("GET", "/api/v1/goals", None), ("POST", "/api/v1/goals", {}),
                 ("GET", "/api/v1/goals/fictional-puzzle", None),
                 ("PUT", "/api/v1/goals/fictional-puzzle/charter", {}),
                 ("GET", "/api/v1/goals/fictional-puzzle/record", None))
        for owner in (None, "lead"):
            for method, path, body in cases:
                with self.subTest(owner=owner, method=method, path=path):
                    self.assertEqual(self.call(owner, method, path, body, **{"Idempotency-Key": "off"}).status_code, 404)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_receipts").fetchone()[0], 0)

    def test_api_current_keys_approval_and_cross_owner_privacy(self):
        self.enable()
        self.assertEqual(self.call(None, "POST", "/api/v1/goals", {}).status_code, 401)
        body = {"charter": fictional_charter(), "approved_by_owner": True}
        self.assertEqual(self.call("lead", "POST", "/api/v1/goals", {"charter": fictional_charter()}).status_code, 409)
        proposed = self.call("lead", "POST", "/api/v1/goals", body)
        self.assertEqual(proposed.status_code, 201, proposed.text)
        slug = proposed.json()["goal"]["slug"]
        self.assertEqual(self.call("other", "GET", "/api/v1/goals/" + slug).status_code, 404)
        self.assertEqual(self.call("other", "GET", "/api/v1/goals/" + slug + "/record").status_code, 404)
        self.goals.operator_action(slug, "approve", "Fictional public review.")
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO user_blocks(blocker_id,blocked_id,created_at) VALUES('other','lead',?)", (storage_time(self.now),))
        public = self.call("other", "GET", "/api/v1/goals/" + slug)
        self.assertEqual(public.status_code, 200)
        self.assertIn(self.call("other", "PUT", "/api/v1/goals/" + slug + "/charter", body).status_code, (403, 404))
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='lead'", (storage_time(self.now),))
        self.assertEqual(self.call("lead", "PUT", "/api/v1/goals/" + slug + "/charter", body).status_code, 401)

    def test_idempotent_retry_never_replays_content_and_off_still_wins(self):
        self.enable()
        body = {"charter": fictional_charter(), "approved_by_owner": True}
        headers = {"Idempotency-Key": "fictional-goal-once"}
        first = self.call("lead", "POST", "/api/v1/goals", body, **headers)
        self.assertEqual(first.status_code, 201, first.text)
        again = self.call("lead", "POST", "/api/v1/goals", body, **headers)
        self.assertEqual(again.status_code, 200)
        self.assertIs(again.json()["already_applied"], True)
        self.assertNotIn(body["charter"]["aim"], again.text)
        changed = deepcopy(body)
        changed["charter"]["aim"] = "A changed fictional request must not share an existing receipt."
        self.assertEqual(self.call("lead", "POST", "/api/v1/goals", changed, **headers).status_code, 409)
        self.enable("off")
        self.assertEqual(self.call("lead", "POST", "/api/v1/goals", body, **headers).status_code, 404)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM goals").fetchone()[0], 1)
            self.assertEqual([row[0] for row in connection.execute("SELECT response_json FROM agent_receipts")], ["{}"])

    def test_early_access_flag_and_report_only_revoked_credentials_are_current(self):
        self.enable("early_access")
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM early_access_grants WHERE user_id='other'")
        self.assertEqual(self.call("other", "GET", "/api/v1/goals").status_code, 404)
        self.assertEqual(self.call("lead", "GET", "/api/v1/goals").status_code, 200)
        with self.db.transaction() as connection:
            connection.execute("UPDATE report_keys SET agent_api=0 WHERE user_id='lead'")
        self.assertEqual(self.call("lead", "GET", "/api/v1/goals").status_code, 403)
        with self.db.transaction() as connection:
            connection.execute("UPDATE report_keys SET agent_api=1,revoked_at=? WHERE user_id='lead'", (storage_time(self.now),))
        self.assertEqual(self.call("lead", "GET", "/api/v1/goals").status_code, 401)

    def test_cli_operator_commands_record_reasons_without_admin_http_routes(self):
        from connectthebots_server.admin import main
        self.enable()
        goal = self.goals.propose("lead", fictional_charter(), True)["goal"]
        with patch("connectthebots_server.config.Settings.from_env", return_value=self.settings):
            for argv in (["goals", "pending"], ["goals", "approve", goal["slug"], "Fictional approved scope."],
                         ["goals", "pause", goal["slug"], "Fictional pause reason."],
                         ["goals", "close", goal["slug"], "Fictional final operator summary."]):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                    self.assertEqual(main(argv), 0, stderr.getvalue())
                self.assertIsInstance(json.loads(stdout.getvalue()), dict)
        record = self.goals.record("lead", goal["slug"])["entries"]
        self.assertIn("Fictional final operator summary.", json.dumps(record[-1]["content"]))
        refused = self.goals.propose("other", fictional_charter("Fictional: prism arithmetic puzzle"), True)["goal"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("connectthebots_server.config.Settings.from_env", return_value=self.settings), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            self.assertEqual(main(["goals", "refuse", refused["slug"], "Fictional refusal reason."]), 0, stderr.getvalue())
        self.assertIn("Fictional refusal reason.", json.dumps(self.goals.record("other", refused["slug"])["entries"][-1]["content"]))
        for path in ("/admin/goals", "/api/v1/admin/goals", "/operator/goals"):
            self.assertEqual(self.client.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
