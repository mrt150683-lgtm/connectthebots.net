"""Fictional public Common goals views use anonymous, current projections."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import html
import re
import sqlite3
import unittest
from unittest.mock import patch

from connectthebots_server import agent_texts, growth_config
from connectthebots_server.recovery_authority import AuthorityError
from connectthebots_server.sessions import SESSION_COOKIE, storage_time
from tests_server import test_goals as cases


@unittest.skipUnless(cases.SERVER, "optional server extra not installed")
class GoalPageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cases.GoalHttpTests("runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.enable()
        self.app, self.client, self.db = self.fixture.app, self.fixture.client, self.fixture.db
        self.goals, self.members, self.work = self.app.state.goals, self.app.state.goal_members, self.app.state.goal_work
        self.findings, self.tasks = self.app.state.goal_findings, self.app.state.goal_tasks
        self.moderation = self.app.state.goal_moderation
        with self.db.transaction() as connection:
            growth_config.store(connection, "goals_led_per_member", 5, self.fixture.now)
            growth_config.store(connection, "goals_sensitive_open_max", 3, self.fixture.now)
        self.charter = cases.fictional_charter("Fictional public prism puzzle")
        self.goal = self.open_goal(self.charter)
        self.slug = self.goal["slug"]
        self.leases = {}

    def open_goal(self, charter):
        goal = self.goals.propose("lead", charter, True)["goal"]
        self.goals.operator_action(goal["slug"], "approve", "The operator reviewed this fictional public scope.")
        return self.goals.detail(None, goal["slug"])["goal"]

    def use_lead_cookie(self):
        with self.db.transaction() as connection:
            token = self.app.state.auth.sessions.create(connection, "lead")
        self.client.cookies.set(SESSION_COOKIE, token)

    def lease(self, owner="other", *, show_name=False):
        if owner not in self.leases:
            self.members.join(owner, self.slug, {"capabilities": ["mathematics"], "approved_by_owner": True})
            body = {"sessions_per_week": 3, "minutes_per_session": 20,
                    "until": (self.fixture.now.date() + timedelta(days=30)).isoformat(),
                    "show_first_name": show_name, "charter_revision": 1,
                    "notice_version": agent_texts.GOAL_CONTRIBUTION_VERSION, "approved_by_owner": True}
            if owner == "lead":
                body.update(lead_notice_version=agent_texts.GOAL_LEAD_VERSION, lead_approved_by_owner=True)
            self.work.set_allocation(owner, self.slug, body)
            self.leases[owner] = self.work.start(owner, self.slug, {})
        return self.leases[owner]

    def finding(self, summary="The fictional prism returns after twelve exact states.", *, owner="other"):
        work = self.lease(owner)
        return self.findings.finding(owner, work["session_id"], {
            "task_id": work["task"]["id"], "kind": "proof_step", "summary": summary,
            "detail": "The fictional twelve states were enumerated exactly without an outside assumption.",
            "method": "Repeat the fictional enumeration from zero and compare all twelve positions.",
            "sources": [{"url": "https://papers.example.test/fictional-prism", "title": "A fictional prism study",
                         "quote": "The fictional sequence contained twelve distinct positions.",
                         "accessed": self.fixture.now.date().isoformat()}],
            "confidence": "tentative", "unknowns": ["A second fictional derivation is still useful."], "builds_on": []})["entry"]

    def review(self, finding, *, owner="lead"):
        work = self.lease(owner)
        return self.findings.review(owner, work["session_id"], {"finding_id": finding["id"], "verdict": "supports",
            "note": "Independently repeated the fictional twelve-state enumeration.", "checked": ["method", "sources"]})

    def sensitive(self):
        charter = cases.fictional_charter("Fictional published animal methods", "animal_health")
        self.goal = self.open_goal(charter)
        self.charter, self.slug, self.leases = charter, self.goal["slug"], {}

    def page(self, path=None):
        response = self.client.get(path or "/goals/" + self.slug)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        return response.text

    def test_public_index_has_reason_category_counts_and_honest_activity(self):
        document = self.page("/goals")
        for value in (self.charter["title"], self.charter["aim"], "Mathematics", "Led by a member", "Last entry"):
            self.assertIn(value, document)
        self.assertIn('href="/goals/' + self.slug + '"', document)
        self.assertNotRegex(document.lower(), r"\blive\b|now working|agents working now|leaderboard|ranking")
        item = self.goals.index()["goals"][0]
        self.assertNotIn("aim", item)
        self.assertNotIn("charter_available", item)
        self.assertNotIn("next_after", self.goals.index())

    def test_public_detail_has_scope_tasks_summary_sources_and_record_command(self):
        finding = self.finding()
        self.review(finding)
        self.review(finding, owner="third")
        self.tasks.summary("lead", self.slug, {"session_id": self.leases["lead"]["session_id"],
            "text": "A fictional exact derivation has two independent checks.", "finding_ids": [finding["id"]], "disputed": []})
        document = self.page()
        for value in (self.charter["aim"], self.charter["in_scope"][0], self.charter["out_of_scope"][0],
                      self.charter["tasks"][0]["title"], "Confirmed", "A fictional prism study",
                      "The fictional sequence contained twelve distinct positions.",
                      "A fictional exact derivation has two independent checks.", "Written by an assistant"):
            self.assertIn(html.escape(value, quote=True), document)
        self.assertIn('rel="noopener noreferrer"', document)
        self.assertIn('/goals/' + self.slug + '/record.jsonl', document)
        self.assertIn('href="/goals/verify_goal_record.py"', document)
        self.assertIn('python verify_goal_record.py https://goals.example.test/goals/' + self.slug + '/record.jsonl', document)
        self.assertNotIn("Private fictional", document)
        self.assertNotIn("@fictional.example.test", document)

    def test_proposed_sensitive_goal_is_not_revealed_even_to_signed_in_lead(self):
        proposed = self.goals.propose("lead", cases.fictional_charter("Fictional private animal draft", "animal_health"), True)["goal"]
        self.use_lead_cookie()
        with patch.object(self.app.state.auth.sessions, "current", side_effect=AssertionError("Public pages must not inspect a session")):
            private = self.client.get("/goals/" + proposed["slug"])
            missing = self.client.get("/goals/fictional-not-present")
            listing = self.client.get("/goals")
        self.assertEqual(private.status_code, 404)
        self.assertEqual(private.content, missing.content)
        self.assertNotIn("Fictional private animal draft", listing.text)
        self.assertNotIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(private.text))

    def test_verifier_command_uses_https_url_or_saved_record_for_http(self):
        for base_url, expected in (("https://goals.example.test", "https://goals.example.test/goals/" + self.slug + "/record.jsonl"),
                                   ("http://localhost:8765", "record.jsonl")):
            with self.subTest(base_url=base_url), patch.object(self.app.state.auth, "settings",
                    replace(self.app.state.auth.settings, public_base_url=base_url)):
                document = self.page()
                command = html.unescape(re.search(r"<pre><code>(.*?)</code></pre>", document).group(1))
                self.assertEqual(command, "python verify_goal_record.py " + expected)
                if base_url.startswith("http:"):
                    self.assertIn("save the record above as <code>record.jsonl</code> in the same folder", document)
                    self.assertNotIn("python verify_goal_record.py http://", document)

    def test_sensitive_banner_and_held_finding_stay_publicly_redacted_for_author(self):
        self.sensitive()
        held = self.finding("The fictional cited paper records a study dose of 5 mg/kg.", owner="lead")
        self.assertEqual(held["status"], "held")
        self.use_lead_cookie()
        document = self.page()
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(document))
        self.assertIn("Held for operator review", document)
        self.assertNotIn("study dose of 5 mg/kg", document)
        self.assertNotIn("A fictional prism study", document)
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(self.page("/goals")))

    def test_pending_sensitive_finding_requires_independent_support_before_page_content(self):
        self.sensitive()
        finding = self.finding()
        document = self.page()
        self.assertIn("Awaiting an independent check", document)
        self.assertNotIn(finding["content"]["summary"], document)
        self.review(finding)
        self.assertIn(finding["content"]["summary"], self.page())

    def test_pending_charter_is_not_published_for_signed_in_lead(self):
        self.sensitive()
        pending = deepcopy(self.charter)
        pending["aim"] = "Fictional unpublished pending animal-methods charter canary."
        self.goals.revise_charter("lead", self.slug, pending, True)
        self.use_lead_cookie()
        document = self.page()
        self.assertIn(self.charter["aim"], document)
        self.assertNotIn(pending["aim"], document)

    def test_hidden_scope_redacts_reason_tasks_and_dependent_summary_then_unhide_restores(self):
        finding = self.finding()
        self.lease("lead")
        self.tasks.summary("lead", self.slug, {"session_id": self.leases["lead"]["session_id"],
            "text": "Fictional summary canary derived from the published task.", "finding_ids": [finding["id"]], "disputed": []})
        with self.db.connect() as connection:
            charter_entry = connection.execute("SELECT e.id FROM goal_entries e JOIN goals g ON g.id=e.goal_id WHERE g.slug=? AND e.kind='charter'", (self.slug,)).fetchone()[0]
        self.moderation.action(charter_entry, "hide", "The operator temporarily hid this fictional scope.")
        self.use_lead_cookie()
        for path in ("/goals", "/goals/" + self.slug):
            document = self.page(path)
            self.assertNotIn(self.charter["aim"], document)
            self.assertNotIn(self.charter["title"], document)
            self.assertNotIn("Fictional summary canary", document)
            self.assertNotIn(self.charter["tasks"][0]["title"], document)
        self.moderation.action(charter_entry, "unhide", "The operator restored the retained fictional scope.")
        self.assertIn(self.charter["aim"], self.page())

    def test_withdrawn_finding_and_summary_content_do_not_return_on_pages(self):
        finding = self.finding()
        self.lease("lead")
        summary = "Fictional separately authored summary depending on that result."
        self.tasks.summary("lead", self.slug, {"session_id": self.leases["lead"]["session_id"],
            "text": summary, "finding_ids": [finding["id"]], "disputed": []})
        self.goals.withdrawal("other", finding["id"])
        document = self.page()
        self.assertNotIn(finding["content"]["summary"], document)
        self.assertNotIn(summary, document)
        self.assertIn("Withdrawn", document)
        self.assertIn("record envelope and hashes remain", document)

    def test_optional_name_is_removed_from_current_displays_on_stop(self):
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO member_profiles(user_id,public_id,first_name,updated_at) VALUES('lead','fictional-public-lead','Ama Fictional',?)", (storage_time(self.fixture.now),))
        self.lease("lead", show_name=True)
        finding = self.finding(owner="lead")
        self.assertIn("Led by Ama Fictional", self.page("/goals"))
        self.assertIn("Ama Fictional", self.page())
        self.work.stop("lead", self.slug)
        self.assertNotIn("Ama Fictional", self.page("/goals"))
        self.assertNotIn("Ama Fictional", self.page())
        self.assertIn(finding["content"]["summary"], self.page())

    def test_index_and_findings_have_bounded_stable_cursor_pages(self):
        second = self.open_goal(cases.fictional_charter("Fictional quartz puzzle"))
        first = self.page("/goals?limit=1")
        self.assertEqual(len(re.findall(r'data-goal-slug=', first)), 1)
        self.assertIn("More goals", first)
        next_index = self.page('/goals?limit=1&after=' + self.slug)
        self.assertIn(second["slug"], next_index)
        self.assertNotIn('data-goal-slug="' + self.slug + '"', next_index)
        entries = [self.finding("Fictional exact prism contribution number " + str(number) + ".") for number in range(3)]
        first = self.page('/goals/' + self.slug + '?limit=2')
        self.assertIn(entries[0]["content"]["summary"], first)
        self.assertNotIn(entries[2]["content"]["summary"], first)
        last = self.page('/goals/' + self.slug + '?limit=2&after=' + str(entries[1]["seq"]))
        self.assertIn(entries[2]["content"]["summary"], last)
        self.assertNotIn(entries[0]["content"]["summary"], last)

    def test_invalid_queries_and_sensitive_safe_errors_preserve_exact_banner(self):
        self.sensitive()
        with patch.object(self.app.state.agents.limiter, "admit", return_value=None):
            for query in ("after=-1", "after=9223372036854775808", "limit=0", "limit=51", "limit=1&limit=2", "secret=field"):
                with self.subTest(query=query):
                    response = self.client.get('/goals/' + self.slug + '?' + query)
                    self.assertEqual(response.status_code, 422)
                    self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(response.text))
            for query in ("category=private", "after=bad/slash", "limit=999999999", "category=physics&category=history"):
                self.assertEqual(self.client.get('/goals?' + query).status_code, 422)

    def test_optional_index_window_applies_since_filter_before_limit(self):
        self.open_goal(cases.fictional_charter("Fictional quartz puzzle"))
        latest = self.open_goal(cases.fictional_charter("Fictional zircon puzzle"))
        since = storage_time(self.fixture.now + timedelta(seconds=30))
        self.fixture.now += timedelta(minutes=1)
        self.goals.operator_action(latest['slug'], 'pause', 'A later fictional operator review paused this scope.')
        result = self.goals.index(None, since=since, include_reason=True, limit=1)
        self.assertEqual([goal['slug'] for goal in result['goals']], [latest['slug']])
        self.assertIsNone(result['next_after'])

    def test_default_off_wins_before_query_and_session_and_goal_lookup(self):
        self.fixture.enable("off")
        with patch.object(self.app.state.auth.sessions, "current", side_effect=AssertionError("No session lookup")), \
             patch.object(self.goals, "_goal", side_effect=AssertionError("No goal lookup")):
            for path in ('/goals?category=invalid', '/goals/' + self.slug + '?after=bad', '/goals/fictional-private?limit=0'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"error": "Not found"})

    def test_early_access_public_listing_is_readable_without_account(self):
        self.fixture.enable("early_access")
        self.client.cookies.clear()
        self.assertIn(self.charter["title"], self.page("/goals"))
        self.assertIn(self.charter["aim"], self.page())

    def test_page_data_is_escaped_and_never_loaded_as_remote_embeds(self):
        charter = cases.fictional_charter('Fictional <script>prism</script> proof')
        goal = self.open_goal(charter)
        document = self.page('/goals/' + goal["slug"])
        self.assertIn(html.escape(charter["title"]), document)
        self.assertNotIn('<script>prism</script>', document)
        for tag in ('script', 'iframe', 'object', 'embed', 'img', 'video'):
            self.assertNotRegex(document, '<' + tag + r'\b')
        self.assertNotRegex(document, r'(?:src|href)="https?://')

    def test_record_downloads_keep_route_precedence(self):
        heads = self.client.get('/goals/heads.json')
        self.assertEqual(heads.status_code, 200)
        self.assertIn(self.slug, heads.text)
        record = self.client.get('/goals/' + self.slug + '/record.jsonl')
        self.assertEqual(record.status_code, 200)
        self.assertIn('application/x-ndjson', record.headers['content-type'])
        self.assertNotIn('<html', record.text)

    def test_rate_and_recovery_errors_are_safe_and_bannered_for_listed_sensitive_goal(self):
        self.sensitive()
        with patch.object(self.app.state.agents.limiter, "admit", return_value=7):
            response = self.client.get('/goals/' + self.slug)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '7')
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(response.text))
        with patch.object(self.goals, "_pause_inactive", side_effect=AuthorityError("Fictional provider refusal")):
            response = self.client.get('/goals/' + self.slug)
        self.assertEqual(response.status_code, 503)
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(response.text))
        self.assertNotIn('Fictional provider refusal', response.text)

    def test_busy_error_keeps_notice_when_sensitive_public_scope_can_be_read(self):
        self.sensitive()
        busy = sqlite3.OperationalError("Fictional private busy diagnostic")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch('connectthebots_server.goal_pages._detail', side_effect=busy):
            response = self.client.get('/goals/' + self.slug)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['retry-after'], '2')
        self.assertIn(agent_texts.GOAL_SENSITIVE_BANNER, html.unescape(response.text))
        self.assertNotIn('Fictional private busy diagnostic', response.text)
