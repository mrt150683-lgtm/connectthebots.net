"""Agent-first growth: taste interview, referrals, topic boards, ranks and check-in.

All people here are fictional. Each test drives the real HTTP agent API with
per-owner agent keys, except the pure matching rules at the top.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server import agent_texts, growth_config
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, random_token, storage_time
    from connectthebots_server.taste import pair_match, validate_answer
    from connectthebots_server.topic_boards import topic_key
    from connectthebots_server.agent_common import AgentError


def answer(items, tier="share", skipped=False):
    return {"items": items, "tier": tier, "skipped": skipped}


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class TasteRuleTests(unittest.TestCase):
    def test_explanation_names_mutually_visible_shared_taste(self):
        mine = {"films": answer(["90s horror", "Paddington 2"]), "teams": answer(["Norwich City"]), "food": answer(["ramen"])}
        theirs = {"films": answer(["90S Horror"]), "teams": answer(["norwich city"]), "food": answer(["tacos"])}
        result = pair_match(mine, theirs, set(), set())
        self.assertEqual(result["explanation"], "You both support Norwich City and love 90s horror.")
        self.assertEqual([entry["topic"] for entry in result["shared"]], ["films", "teams"])

    def test_never_and_undisclosed_ask_answers_are_not_used(self):
        mine = {"music": answer(["Bjork"], "never"), "books": answer(["Terry Pratchett"], "ask")}
        theirs = {"music": answer(["Bjork"]), "books": answer(["Terry Pratchett"])}
        self.assertIsNone(pair_match(mine, theirs, set(), set()))
        # The viewer's own ask-first overlap is hinted to the viewer only.
        mine["films"] = answer(["Alien"])
        theirs["films"] = answer(["Alien"])
        result = pair_match(mine, theirs, set(), set())
        self.assertEqual(result["ask_first_overlaps"], ["books"])
        self.assertNotIn("Pratchett", result["explanation"])
        self.assertNotIn("Bjork", json.dumps(result))
        disclosed = pair_match(mine, theirs, {"books"}, set())
        self.assertIn("read Terry Pratchett", disclosed["explanation"])

    def test_values_filters_politics_and_looking_for(self):
        base = {"hobbies": answer(["climbing"])}
        left = {**base, "politics": answer(["left"], "share")}
        right = {**base, "politics": answer(["right"], "share")}
        centre = {**base, "politics": answer(["centre"], "share")}
        self.assertIsNone(pair_match(left, right, set(), set()))
        self.assertIn("compatible", pair_match(left, centre, set(), set())["explanation"])
        self.assertIsNotNone(pair_match(left, {**base, "politics": answer(["right"], "ask")}, set(), set()))
        friends = {**base, "looking_for": answer(["friends"])}
        club = {**base, "looking_for": answer(["club"])}
        self.assertIsNone(pair_match(friends, club, set(), set()))
        both = pair_match(friends, {**base, "looking_for": answer(["friends", "club"])}, set(), set())
        self.assertTrue(both["explanation"].endswith("are into climbing and are both looking for friends."))

    def test_answer_validation_defaults_sensitive_topics_to_ask_first(self):
        self.assertEqual(validate_answer({"topic": "politics", "items": ["Centre"]})["tier"], "ask")
        with self.assertRaises(AgentError) as refused:
            validate_answer({"topic": "politics", "items": ["left"], "tier": "share"})
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(validate_answer({"topic": "politics", "items": ["left"], "tier": "share", "owner_confirmed_share": True})["tier"], "share")
        self.assertTrue(validate_answer({"topic": "politics", "skipped": True})["skipped"])
        for bad in ({"topic": "politics", "items": ["far out"]}, {"topic": "looking_for", "items": ["dates"]},
                    {"topic": "religion", "items": ["x"]}, {"topic": "films", "items": []}, {"topic": "films", "items": ["a‮"]}):
            with self.subTest(bad=bad), self.assertRaises(AgentError):
                validate_answer(bad)

    def test_topic_keys_ignore_order_case_and_small_words(self):
        self.assertEqual(topic_key("Bird watching"), topic_key("watching the BIRD"))
        self.assertEqual(topic_key("Food and drink"), "drink food")


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class AgentApiTests(unittest.TestCase):
    PEOPLE = ("ama", "joon", "rui", "sol", "tam")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://agents.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-only-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.db = self.app.state.database
        self.keys, self.sessions = {}, {}
        with self.db.transaction() as connection:
            for person in self.PEOPLE:
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (person, "fictional-" + person, person + "@example.test", person.title() + " Example",
                                    storage_time(self.now), storage_time(self.now)))
                self.sessions[person] = self.app.state.auth.sessions.create(connection, person)
        for person in self.PEOPLE:
            self.keys[person] = self.app.state.reports.create_key(person, "Fictional agent")["key"]

    def call(self, person, method, path, body=None, **headers):
        headers = {"Authorization": "Bearer " + self.keys[person], **headers}
        if body is not None:
            return self.client.request(method, path, content=json.dumps(body), headers={**headers, "Content-Type": "application/json"})
        return self.client.request(method, path, headers=headers)

    def ok(self, person, method, path, body=None, status=200):
        response = self.call(person, method, path, body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def interview(self, person, answers, complete=True):
        self.ok(person, "PUT", "/api/v1/agent/interview/answers", {"answers": answers})
        if complete:
            self.ok(person, "POST", "/api/v1/agent/interview/complete", {})

    def horror_fan(self, person, team="Norwich City", extra=()):
        self.interview(person, [
            {"topic": "films", "items": ["90s horror"], "tier": "share"},
            {"topic": "teams", "items": [team], "tier": "share"},
            {"topic": "music", "items": ["Bjork"], "tier": "never"},
            {"topic": "books", "items": ["Terry Pratchett"], "tier": "ask"},
            {"topic": "looking_for", "items": ["friends"], "tier": "share"},
            {"topic": "politics", "skipped": True}, *extra])

    # Authority ------------------------------------------------------------
    def test_requires_current_agent_key_and_refuses_report_only_keys(self):
        self.assertEqual(self.client.get("/api/v1/agent/me").status_code, 401)
        with self.db.transaction() as connection:
            connection.execute("UPDATE report_keys SET agent_api=0 WHERE user_id='ama'")
        refused = self.call("ama", "GET", "/api/v1/agent/me")
        self.assertEqual(refused.status_code, 403)
        self.assertIn("reports only", refused.json()["error"])
        me = self.ok("joon", "GET", "/api/v1/agent/me")
        self.assertRegex(me["referrals"]["code"], r"^CTB-[2-9A-HJ-NP-Z]{6}$")
        self.assertRegex(me["member"]["public_id"], r"^m_[0-9a-f]{16}$")
        self.assertNotIn("joon@example.test", json.dumps(me))
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='joon'", (storage_time(self.now),))
        self.assertEqual(self.call("joon", "GET", "/api/v1/agent/me").status_code, 401)

    def test_me_confirms_connection_and_names_the_next_step(self):
        session = self.sessions["ama"]
        self.client.cookies.set(SESSION_COOKIE, session)
        page = self.client.get("/app").text
        self.assertIn('data-agent-connected="no"', page)
        self.assertIn("Waiting for your agent.", page)
        me = self.ok("ama", "GET", "/api/v1/agent/me")
        self.assertIs(me["connected"], True)
        self.assertEqual(me["next_step"]["call"], ["GET /api/v1/agent/texts", "GET /api/v1/agent/interview"])
        self.assertIn('data-agent-connected="yes"', self.client.get("/app").text)
        self.interview("ama", [{"topic": "films", "items": ["90s horror"], "tier": "share"}], complete=False)
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/me")["next_step"]["call"], ["GET /api/v1/agent/interview"])
        self.horror_fan("ama")
        step = self.ok("ama", "GET", "/api/v1/agent/me")["next_step"]
        self.assertEqual(step["call"], ["POST /api/v1/agent/checkin"])
        self.assertIn("every 7 days", step["why"])

    def test_texts_are_public_versioned_and_hashed(self):
        texts = self.client.get("/api/v1/agent/texts").json()
        self.assertEqual(texts["version"], agent_texts.VERSION)
        self.assertEqual(set(texts["texts"]), set(agent_texts.TEXTS))
        for item in texts["texts"].values():
            self.assertEqual(item["sha256"], agent_texts.digest(item["text"]))

    # Interview and matching ---------------------------------------------
    def test_interview_completion_needs_enough_answered_topics(self):
        self.ok("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [
            {"topic": "films", "items": ["Alien"]}, {"topic": "politics", "skipped": True}]})
        refused = self.call("ama", "POST", "/api/v1/agent/interview/complete", {})
        self.assertEqual(refused.status_code, 409)
        state = self.ok("ama", "GET", "/api/v1/agent/interview")
        self.assertEqual(state["answered"], 1)
        self.assertEqual(state["answers"]["politics"]["tier"], "ask")
        self.assertEqual(len(state["next_questions"]), 2)
        self.assertNotIn("films", [question["topic"] for question in state["next_questions"]])
        politics = self.call("ama", "PUT", "/api/v1/agent/interview/answers", {"answers": [{"topic": "politics", "items": ["left"], "tier": "share"}]})
        self.assertEqual(politics.status_code, 409)

    def test_checkin_reports_new_matches_once_with_reasons_and_respects_tiers(self):
        self.horror_fan("ama")
        self.horror_fan("joon")
        self.horror_fan("rui", team="Ipswich Town")
        self.interview("sol", [{"topic": "films", "items": ["90s horror"]}, {"topic": "teams", "items": ["Norwich City"]},
                               {"topic": "food", "items": ["ramen"]}, {"topic": "hobbies", "items": ["chess"]},
                               {"topic": "looking_for", "items": ["club"]}])
        checkin = self.ok("ama", "POST", "/api/v1/agent/checkin", {})
        new = checkin["matches"]["new"]
        self.assertEqual([match["explanation"] for match in new][:1], ["You both support Norwich City, love 90s horror and are both looking for friends."])
        self.assertEqual(len(new), 2)  # joon and rui; sol wants a club, not friends
        # Both keep books ask-first, so neither side can even hint at it yet.
        self.assertEqual(new[0]["ask_first_overlaps"], [])
        text = json.dumps(checkin)
        for private in ("Bjork", "Pratchett", "joon@example.test", "Joon Example"):
            self.assertNotIn(private, text)
        again = self.ok("ama", "POST", "/api/v1/agent/checkin", {})
        self.assertEqual(again["matches"]["new"], [])
        self.assertEqual(again["matches"]["earlier_still_matching"], 2)
        self.assertEqual(again["previous_checkin_at"], checkin["checked_in_at"])

        joon = new[0]["member"]["public_id"]
        refused = self.call("ama", "POST", f"/api/v1/agent/matches/{joon}/disclose", {"topics": ["books"]})
        self.assertEqual(refused.status_code, 409)
        never = self.call("ama", "POST", f"/api/v1/agent/matches/{joon}/disclose", {"topics": ["music"], "approved_by_owner": True})
        self.assertEqual(never.status_code, 409)
        self.ok("joon", "POST", f"/api/v1/agent/matches/{self.ok('ama', 'GET', '/api/v1/agent/me')['member']['public_id']}/disclose",
                {"topics": ["books"], "approved_by_owner": True,
                 "revisions": {"books": self.ok("joon", "GET", "/api/v1/agent/interview")["answers"]["books"]["revision"]}})
        # Joon's yes lets Ama's agent see the overlap with Ama's own private answer
        # and ask Ama; Joon's side still cannot see Ama's books answer.
        seen_by_ama = self.ok("ama", "GET", "/api/v1/agent/matches")["matches"][0]
        self.assertEqual(seen_by_ama["ask_first_overlaps"], ["books"])
        self.assertNotIn("Pratchett", seen_by_ama["explanation"])
        self.assertNotIn("Pratchett", json.dumps(self.ok("joon", "GET", "/api/v1/agent/matches")))
        self.ok("ama", "POST", f"/api/v1/agent/matches/{joon}/disclose", {"topics": ["books"], "approved_by_owner": True,
                "revisions": {"books": self.ok("ama", "GET", "/api/v1/agent/interview")["answers"]["books"]["revision"]}})
        seen_by_joon = self.ok("joon", "GET", "/api/v1/agent/matches")["matches"]
        self.assertIn("read Terry Pratchett", seen_by_joon[0]["explanation"])
        self.assertNotIn("Pratchett", json.dumps(self.ok("rui", "GET", "/api/v1/agent/matches")))

        self.ok("ama", "POST", "/api/v1/agent/block", {"member": joon})
        self.assertNotIn(joon, json.dumps(self.ok("ama", "GET", "/api/v1/agent/matches")))

    # Referrals -----------------------------------------------------------
    def code(self, person):
        return self.ok(person, "GET", "/api/v1/agent/referrals")["code"]

    def test_referral_counts_after_interview_and_rewards_both_sides(self):
        code = self.code("ama")
        own = self.call("ama", "POST", "/api/v1/agent/referral", {"code": code})
        self.assertEqual(own.status_code, 409)
        self.assertEqual(self.call("joon", "POST", "/api/v1/agent/referral", {"code": "CTB-AAAAAA"}).status_code, 404)
        entered = self.ok("joon", "POST", "/api/v1/agent/referral", {"code": code.lower().replace("-", " ")})
        self.assertIsNone(entered["referred_by"]["qualified_at"])
        self.assertEqual(self.call("joon", "POST", "/api/v1/agent/referral", {"code": code}).status_code, 409)
        self.assertEqual(self.code("ama"), code)
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/referrals")["referred"], {"entered": 1, "qualified": 0, "under_review": 0})
        self.horror_fan("joon")
        for person in ("ama", "joon"):
            status = self.ok(person, "GET", "/api/v1/agent/referrals")
            self.assertTrue(status["early_access"]["active"], person)
            self.assertEqual(status["features"], ["extra_board_creation", "extra_matches"])
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/referrals")["referred"]["qualified"], 1)
        late = self.call("joon", "POST", "/api/v1/agent/referral", {"code": self.code("rui")})
        self.assertEqual(late.status_code, 409)
        self.now += timedelta(days=31)
        self.assertFalse(self.ok("joon", "GET", "/api/v1/agent/referrals")["early_access"]["active"])

    def test_first_match_mode_and_cluster_flagging_are_configurable(self):
        with self.db.transaction() as connection:
            growth_config.store(connection, "referral_qualifies_on", "first_match", self.now)
            growth_config.store(connection, "referral_cluster_threshold", 2, self.now)
        code = self.code("ama")
        self.ok("joon", "POST", "/api/v1/agent/referral", {"code": code})
        self.horror_fan("joon")
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/referrals")["referred"]["qualified"], 0)
        self.horror_fan("rui")
        self.ok("joon", "POST", "/api/v1/agent/checkin", {})
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/referrals")["referred"]["qualified"], 1)
        # A burst of entries on one code inside an hour is held for review.
        first = self.ok("sol", "POST", "/api/v1/agent/referral", {"code": self.code("tam")})
        self.assertFalse(first["referred_by"]["under_review"])
        held = self.ok("ama", "POST", "/api/v1/agent/referral", {"code": self.code("tam")})
        self.assertTrue(held["referred_by"]["under_review"])
        self.assertEqual(self.ok("tam", "GET", "/api/v1/agent/referrals")["referred"]["under_review"], 2)
        self.assertEqual(self.ok("tam", "GET", "/api/v1/agent/referrals")["referred"]["qualified"], 0)
        from connectthebots_server.growth import operate
        from types import SimpleNamespace
        flagged = operate(self.db, lambda: self.now, SimpleNamespace(action="flagged"))["flagged"]
        self.assertEqual(len(flagged), 2)
        result = operate(self.db, lambda: self.now, SimpleNamespace(action="clear", id=flagged[0]["id"]))
        self.assertTrue(result["cleared"])

    def test_daily_code_limit_refuses_agents(self):
        with self.db.transaction() as connection:
            growth_config.store(connection, "referral_code_daily_limit", 1, self.now)
        code = self.code("ama")
        self.ok("joon", "POST", "/api/v1/agent/referral", {"code": code})
        limited = self.call("rui", "POST", "/api/v1/agent/referral", {"code": code})
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_signup_referral_is_recorded_and_never_blocks_account_creation(self):
        auth = self.app.state.auth
        code = self.code("ama")

        class Identity:
            sub, email, display_name = "fictional-new", "new@example.test", "New Example"

        def attempt(referral):
            with self.db.connect() as connection:
                generation = connection.execute("SELECT erasure_generation FROM recovery_state").fetchone()[0]
            return {"erasure_generation": generation, "invite_hash": None, "referral_code": referral}

        auth.complete_login(Identity, attempt(code), None)
        with self.db.connect() as connection:
            row = connection.execute("SELECT r.entered_via,u.id FROM referrals r JOIN users u ON u.id=r.referred_id WHERE u.google_sub='fictional-new'").fetchone()
            self.assertEqual(row["entered_via"], "signup")
            self.assertIsNotNone(connection.execute("SELECT code FROM referral_codes WHERE user_id=?", (row["id"],)).fetchone())

        class Other:
            sub, email, display_name = "fictional-other", "other@example.test", "Other Example"
        auth.complete_login(Other, attempt("CTB-AAAAAA"), None)
        with self.db.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1 FROM users WHERE google_sub='fictional-other'").fetchone())

    def test_sharing_requires_owner_approval_and_records_it(self):
        refused = self.call("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code"})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Ask your person first", refused.json()["error"])
        self.ok("ama", "PATCH", "/api/v1/agent/profile", {"first_name": "Ama"})
        shared = self.ok("ama", "POST", "/api/v1/agent/share", {"kind": "missing_person", "gap": "hardware skills",
                                                                "friend": "Kofi", "approved_by_owner": True}, status=201)
        self.assertIn("Hi Kofi, Ama thought of you", shared["text"])
        self.assertIn(shared["code"], shared["text"])
        self.assertEqual(self.call("ama", "GET", "/api/v1/agent/signature").status_code, 409)
        self.assertEqual(self.call("ama", "PATCH", "/api/v1/agent/profile", {"signature_opt_in": True}).status_code, 409)
        self.horror_fan("ama")
        self.ok("ama", "PATCH", "/api/v1/agent/profile", {"signature_opt_in": True, "approved_by_owner": True})
        card = self.ok("ama", "GET", "/api/v1/agent/signature")
        self.assertIn("Matchable on Connect the Bots: Ama's agent. Into 90s horror, Norwich City.", card["text"])
        self.assertNotIn("CTB-", card["text"])
        self.assertNotIn("Pratchett", card["text"])
        with self.db.connect() as connection:
            kinds = [row[0] for row in connection.execute("SELECT kind FROM share_approvals WHERE user_id='ama' ORDER BY created_at")]
        self.assertEqual(sorted(kinds), ["missing_person", "signature"])

    # Boards ----------------------------------------------------------------
    def post(self, person, slug, body, tags=(), status=201):
        return self.ok(person, "POST", f"/api/v1/boards/{slug}/posts", {"body": body, "tags": list(tags), "approved_by_owner": True}, status=status)

    def test_index_counts_windows_and_since_for_cheap_scanning(self):
        index = self.ok("ama", "GET", "/api/v1/boards/index")
        self.assertEqual(index["total_boards"], 16)
        self.assertTrue(all(board["posts"]["all"] == 0 for board in index["boards"]))
        self.assertEqual(self.call("ama", "POST", "/api/v1/boards/hobbies/posts", {"body": "Anyone fancy a weekly chess game?"}).status_code, 409)
        self.post("ama", "hobbies", "Anyone fancy a weekly chess game in the evenings?")
        self.now += timedelta(days=10)
        self.post("joon", "hobbies", "Looking for a pottery buddy on Saturday mornings.")
        self.now += timedelta(hours=1)
        index = self.ok("rui", "GET", "/api/v1/boards/index")
        hobbies = next(board for board in index["boards"] if board["slug"] == "hobbies")
        self.assertEqual(hobbies["posts"], {"all": 2, "30d": 2, "7d": 1, "24h": 1})
        unchanged = self.ok("rui", "GET", "/api/v1/boards/index?since=" + index["generated_at"])
        self.assertEqual(unchanged["boards"], [])
        self.now += timedelta(minutes=5)
        self.post("sol", "music", "Starting a choir for people who think they cannot sing.")
        changed = self.ok("rui", "GET", "/api/v1/boards/index?since=" + index["generated_at"])
        self.assertEqual([board["slug"] for board in changed["boards"]], ["music"])
        self.assertEqual(self.call("rui", "GET", "/api/v1/boards/index?since=yesterday").status_code, 422)
        compact = len(json.dumps(self.ok("rui", "GET", "/api/v1/boards/index")))
        self.assertLess(compact, 4000)

    def test_social_boards_are_semi_anonymous_and_keep_people_on_platform(self):
        self.ok("ama", "PATCH", "/api/v1/agent/profile", {"first_name": "Ama", "age": 34, "sex": "female"})
        self.assertEqual(self.call("ama", "PATCH", "/api/v1/agent/profile", {"first_name": "Ama Example"}).status_code, 422)
        self.assertEqual(self.call("ama", "PATCH", "/api/v1/agent/profile", {"age": 16}).status_code, 422)
        for body in ("New in town, message me on WhatsApp", "Email me at ama@example.test please",
                     "Call 07700 900123 for a coffee", "See https://example.test/ama for more"):
            with self.subTest(body=body):
                refused = self.call("ama", "POST", "/api/v1/boards/company-and-friendship/posts", {"body": body, "approved_by_owner": True})
                self.assertEqual(refused.status_code, 422)
        post = self.post("ama", "company-and-friendship", "New in town and would love company for Sunday walks.")["post"]
        self.assertEqual(post["author"], {"public_id": post["author"]["public_id"], "first_name": "Ama", "age": 34, "sex": "female"})
        listed = self.ok("joon", "GET", "/api/v1/boards/company-and-friendship/posts")
        self.assertEqual(listed["rules"], "social")
        self.assertNotIn("Example", json.dumps(listed))
        general = self.post("ama", "hobbies", "Learning to juggle, happy to share tips and practise together.")["post"]
        self.assertNotIn("age", general["author"])
        self.assertEqual(self.call("joon", "POST", f"/api/v1/boards/posts/{post['id']}/replies",
                                   {"body": "Text me on telegram", "approved_by_owner": True}).status_code, 422)
        self.ok("joon", "POST", f"/api/v1/boards/posts/{post['id']}/replies", {"body": "I walk most Sundays, happy to join!", "approved_by_owner": True}, status=201)
        thread = self.ok("ama", "GET", f"/api/v1/boards/posts/{post['id']}")
        self.assertEqual(len(thread["replies"]), 1)
        # Blocking hides both ways, silently.
        self.ok("ama", "POST", "/api/v1/agent/block", {"member": thread["replies"][0]["author"]["public_id"]})
        self.assertEqual(self.ok("ama", "GET", f"/api/v1/boards/posts/{post['id']}")["replies"], [])
        self.assertEqual(self.call("joon", "GET", f"/api/v1/boards/posts/{post['id']}").status_code, 404)
        self.assertEqual(self.ok("joon", "GET", "/api/v1/boards/company-and-friendship/posts")["posts"], [])

    def test_reports_hide_content_at_threshold_and_own_content_cannot_be_reported(self):
        post = self.post("ama", "games", "Selling cheap followers, contact me for a deal on likes!")["post"]
        self.assertEqual(self.call("ama", "POST", "/api/v1/boards/report", {"target": "post", "id": post["id"], "reason": "spam"}).status_code, 404)
        for person in ("joon", "rui"):
            result = self.ok(person, "POST", "/api/v1/boards/report", {"target": "post", "id": post["id"], "reason": "spam"}, status=201)
            self.assertFalse(result["hidden_pending_review"])
        self.ok("joon", "POST", "/api/v1/boards/report", {"target": "post", "id": post["id"], "reason": "spam"}, status=201)
        self.assertTrue(self.ok("sol", "POST", "/api/v1/boards/report", {"target": "post", "id": post["id"], "reason": "spam"}, status=201)["hidden_pending_review"])
        self.assertEqual(self.ok("tam", "GET", "/api/v1/boards/games/posts")["posts"], [])

    def test_agents_create_boards_and_similar_unmatched_topics_spawn_one(self):
        created = self.ok("ama", "POST", "/api/v1/boards", {"title": "Sourdough bakers", "description": "Starters, crumb shots and swaps.", "category": "food"}, status=201)
        self.assertEqual(created["origin"], "agent")
        duplicate = self.call("joon", "POST", "/api/v1/boards", {"title": "bakers of sourdough", "description": "Another one for bread.", "category": "food"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn(created["slug"], duplicate.json()["error"])
        first = self.ok("ama", "GET", "/api/v1/boards/search?q=bird%20watching")
        self.assertEqual(first["boards"], [])
        self.assertIn("counted", first["note"])
        self.ok("joon", "GET", "/api/v1/boards/search?q=watching%20birds")  # a different key: not counted together
        self.ok("rui", "GET", "/api/v1/boards/search?q=Bird%20Watching")
        self.post("sol", "outdoors", "Early morning walk by the estuary this weekend, all welcome.", tags=["watching bird"])
        index = self.ok("tam", "GET", "/api/v1/boards/index")
        spawned = [board for board in index["boards"] if board["title"].lower() == "bird watching"]
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["posts"]["all"], 0)
        found = self.ok("tam", "GET", "/api/v1/boards/search?q=bird%20watching")
        self.assertEqual(found["boards"][0]["origin"], "auto")
        # Board creation is rate limited; early access raises the limit.
        for title in ("Knitting circle", "Urban sketching"):
            self.ok("ama", "POST", "/api/v1/boards", {"title": title, "description": "A place for this hobby.", "category": "art"}, status=201)
        limited = self.call("ama", "POST", "/api/v1/boards", {"title": "Kite flying", "description": "A place for this hobby.", "category": "outdoors"})
        self.assertEqual(limited.status_code, 429)

    def test_checkin_points_to_board_activity_and_empty_boards_to_post_first(self):
        self.horror_fan("ama", extra=[{"topic": "hobbies", "items": ["games"], "tier": "share"}])
        self.post("joon", "games", "Board game night every Thursday, beginners welcome.")
        checkin = self.ok("ama", "POST", "/api/v1/agent/checkin", {})
        self.assertEqual([board["slug"] for board in checkin["boards"]["new_activity"]], ["games"])
        self.assertIn("company-and-friendship", [board["slug"] for board in checkin["boards"]["post_first"]])
        self.assertTrue(all(suggestion["requires_owner_approval"] for suggestion in checkin["sharing_suggestions"]))
        self.assertLessEqual(len(checkin["sharing_suggestions"]), 2)
        self.now += timedelta(days=8)
        self.post("ama", "games", "Looking for someone to co-design a card game about trains.")
        self.now += timedelta(days=8)
        later = self.ok("ama", "POST", "/api/v1/agent/checkin", {})
        self.assertEqual(later["boards"]["new_activity"], [])
        self.assertEqual(later["sharing_suggestions"][0]["kind"], "missing_person")

    # Leaderboards and early access --------------------------------------------
    def test_leaderboards_count_engagement_with_others_never_volume(self):
        posts = [self.post("ama", "music", f"Post number {n} about my favourite records.")["post"] for n in range(5)]
        busy = self.ok("joon", "GET", "/api/v1/agent/leaderboards")
        self.assertEqual(busy["boards"]["replies"], [])
        for person in ("joon", "rui"):
            self.ok(person, "POST", f"/api/v1/boards/posts/{posts[0]['id']}/replies", {"body": "Great taste!", "approved_by_owner": True}, status=201)
        self.ok("ama", "POST", f"/api/v1/boards/posts/{posts[0]['id']}/replies", {"body": "Thanks both!", "approved_by_owner": True}, status=201)
        self.ok("joon", "POST", f"/api/v1/boards/posts/{posts[1]['id']}/replies", {"body": "Agreed", "approved_by_owner": True}, status=201)
        boards = self.ok("sol", "GET", "/api/v1/agent/leaderboards")
        self.assertEqual([(entry["rank"], entry["value"], entry["post_id"]) for entry in boards["boards"]["replies"]],
                         [(1, 2, posts[0]["id"]), (2, 1, posts[1]["id"])])
        self.assertIsNone(boards["boards"]["replies"][0]["member"]["name"])
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/leaderboards")["you"]["replies"], {"rank": 1, "value": 2, "of": 2})
        self.ok("ama", "PATCH", "/api/v1/agent/profile", {"first_name": "Ama", "show_name_on_leaderboards": True, "approved_by_owner": True})
        self.assertEqual(self.ok("sol", "GET", "/api/v1/agent/leaderboards")["boards"]["replies"][0]["member"]["name"], "Ama")
        # A conversation counts only once both people have written.
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO introductions(id,requester_id,recipient_id,status,created_at,expires_at,requester_accepted_at,recipient_accepted_at,context_hash) VALUES('i1','ama','joon','accepted',?,?,?,?,'x')",
                               (storage_time(self.now),) * 4)
            connection.execute("INSERT INTO conversations(id,introduction_id,requester_id,recipient_id,opened_at) VALUES('c1','i1','ama','joon',?)", (storage_time(self.now),))
            connection.execute("INSERT INTO messages(id,conversation_id,sender_id,body,created_at) VALUES('m1','c1','ama','Hello',?)", (storage_time(self.now),))
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/leaderboards")["boards"]["connections"], [])
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO messages(id,conversation_id,sender_id,body,created_at) VALUES('m2','c1','joon','Hi!',?)", (storage_time(self.now),))
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/leaderboards")["you"]["connections"], {"rank": 1, "value": 1, "of": 2})
        self.now += timedelta(days=31)
        self.assertEqual(self.ok("ama", "GET", "/api/v1/agent/leaderboards")["boards"]["connections"], [])

    def test_top_referrers_get_early_access_recalculated_daily_with_live_config(self):
        with self.db.transaction() as connection:
            growth_config.store(connection, "referral_perk_days", 0, self.now)
            for index, (referrer, referred) in enumerate((("ama", "joon"), ("ama", "rui"), ("sol", "tam"))):
                connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at,qualified_at) VALUES(?,?,?,?,?,?,?)",
                                   (f"r{index}", referrer, referred, "CTB-AAAAAA", "agent", storage_time(self.now), storage_time(self.now)))
        self.assertTrue(self.ok("ama", "GET", "/api/v1/agent/referrals")["early_access"]["active"])
        self.ok("ama", "GET", "/api/v1/agent/me")
        self.assertFalse(self.ok("sol", "GET", "/api/v1/agent/me")["referrals"]["early_access"]["active"])
        with self.db.transaction() as connection:
            growth_config.store(connection, "early_access_percent", 100, self.now)
        self.assertFalse(self.ok("sol", "GET", "/api/v1/agent/me")["referrals"]["early_access"]["active"])  # not until the daily run
        self.now += timedelta(days=1, minutes=1)
        self.assertTrue(self.ok("sol", "GET", "/api/v1/agent/me")["referrals"]["early_access"]["active"])
        with self.db.transaction() as connection:
            growth_config.store(connection, "early_access_percent", 0, self.now)
        from connectthebots_server.growth import operate
        from types import SimpleNamespace
        operate(self.db, lambda: self.now, SimpleNamespace(action="recalculate"))
        self.assertFalse(self.ok("ama", "GET", "/api/v1/agent/me")["referrals"]["early_access"]["active"])

    def test_growth_config_validates_operator_values(self):
        with self.db.transaction() as connection:
            for key, value in (("early_access_percent", 101), ("referral_qualifies_on", "signup"), ("nonsense", 1), ("leaderboard_window_days", "30")):
                with self.subTest(key=key), self.assertRaises(growth_config.GrowthConfigError):
                    growth_config.store(connection, key, value, self.now)
            connection.execute("INSERT INTO growth_config(key,value_json,updated_at) VALUES('early_access_percent','\"lots\"','x')")
            self.assertEqual(growth_config.load(connection)["early_access_percent"], 10)
        self.assertEqual(growth_config.parse_cli_value("early_access_percent", "25"), 25)

    # Retries, owner views and data rights ------------------------------------------
    def test_idempotent_retry_confirms_once_without_replaying_content(self):
        body = {"body": "Anyone up for a pub quiz team?", "approved_by_owner": True}
        first = self.call("ama", "POST", "/api/v1/boards/hobbies/posts", body, **{"Idempotency-Key": "quiz-1"})
        again = self.call("ama", "POST", "/api/v1/boards/hobbies/posts", body, **{"Idempotency-Key": "quiz-1"})
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertIs(again.json()["already_applied"], True)
        self.assertNotIn(body["body"], again.text)
        changed = self.call("ama", "POST", "/api/v1/boards/hobbies/posts", {**body, "body": "Different text entirely."}, **{"Idempotency-Key": "quiz-1"})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.ok("joon", "GET", "/api/v1/boards/index")["boards"][0]["posts"]["all"] +
                         sum(board["posts"]["all"] for board in self.ok("joon", "GET", "/api/v1/boards/index")["boards"][1:]), 1)

    def test_dashboard_export_and_deletion_cover_agent_network(self):
        self.horror_fan("ama")
        self.post("ama", "hobbies", "Learning to juggle, happy to share tips and practise together.")
        self.client.cookies.set(SESSION_COOKIE, self.sessions["ama"])
        page = self.client.get("/app")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("data-agent-network", page.text)
        self.assertIn('data-interview-state="complete"', page.text)
        self.assertRegex(page.text, r'data-referral-code>CTB-[2-9A-HJ-NP-Z]{6}<')
        self.assertIn("ask first", page.text)
        self.assertIn("/prompts.html", page.text)
        csrf = self.app.state.auth.sessions.current(self.sessions["ama"], refresh=False).csrf
        export = self.client.post("/app/account/export", data={"csrf": csrf})
        self.assertEqual(export.status_code, 200, export.text)
        network = export.json()["agent_network"]
        self.assertEqual(network["answers"]["teams"]["items"], ["Norwich City"])
        self.assertEqual(len(network["boards"]["posts"]), 1)
        self.app.state.lifecycle.delete_account("ama")
        with self.db.connect() as connection:
            for table, column in (("taste_answers", "user_id"), ("board_posts", "user_id"), ("referral_codes", "user_id"),
                                  ("member_profiles", "user_id"), ("interview_state", "user_id")):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}='ama'").fetchone()[0], 0, table)

    def test_signin_page_offers_optional_referral_code(self):
        page = self.client.get("/signin?ref=ctb-7k3qxm")
        self.assertEqual(page.status_code, 200)
        self.assertIn('value="CTB-7K3QXM"', page.text)
        # In open mode the one primary button carries the code, so following a shared link needs no typing.
        primary = re.search(r'<form method="post" action="/auth/google/start">(.*?)</form>', page.text, re.DOTALL).group(1)
        self.assertIn('name="referral" value="CTB-7K3QXM"', primary)
        self.assertIn("Continue with Google", primary)
        self.assertNotIn('href="/auth/google/start"', page.text)
        plain = self.client.get("/signin")
        self.assertIn('<a class="google-btn" href="/auth/google/start">Continue with Google</a>', plain.text)
        self.assertNotIn("<script", re.sub(r'<script src="[^"]+"></script>', "", page.text))


    # Referral campaign ---------------------------------------------------------
    def test_public_place_share_needs_a_yes_or_standing_permission_within_limits(self):
        self.horror_fan("ama")
        code = self.code("ama")
        thread = "https://forum.example.test/c/makers/t/finding-a-cofounder"
        self.assertFalse(self.ok("ama", "GET", "/api/v1/agent/share-permission")["active"])
        refused = self.call("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "place": thread})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Ask your person first", refused.json()["error"])
        self.assertEqual(self.call("ama", "PUT", "/api/v1/agent/share-permission",
                                   {"places": ["https://forum.example.test/c/makers"], "weekly_limit": 2}).status_code, 409)
        for places, limit in ((["https://connectthebots.net/boards"], 2), (["http://forum.example.test/c/makers"], 2),
                              (["https://user:pw@forum.example.test/"], 2), ([], 2), (["https://forum.example.test/c/makers"], 8),
                              (["https://agents.example.test/r"], 2)):
            with self.subTest(places=places, limit=limit):
                self.assertEqual(self.call("ama", "PUT", "/api/v1/agent/share-permission",
                                           {"places": places, "weekly_limit": limit, "approved_by_owner": True}).status_code, 422)
        permission = self.ok("ama", "PUT", "/api/v1/agent/share-permission",
                             {"places": ["https://Forum.example.test/c/makers/"], "weekly_limit": 2, "approved_by_owner": True})
        self.assertEqual((permission["places"], permission["left_this_week"]), (["https://forum.example.test/c/makers"], 2))
        shared = self.ok("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "place": thread}, status=201)
        self.assertEqual((shared["via"], shared["link"]), ("standing", f"https://agents.example.test/r/{code}"))
        self.assertIn("that it is a referral link", shared["must_include"])
        again = self.call("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "place": thread + "/"})
        self.assertEqual(again.status_code, 409)
        self.assertIn("Don't repeat posts", again.json()["error"])
        self.assertEqual(self.call("ama", "POST", "/api/v1/agent/share",
                                   {"kind": "referral_code", "place": "https://forum.example.test/c/makersville"}).status_code, 409)
        self.ok("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "place": "https://forum.example.test/c/makers/t/clubs"}, status=201)
        used_up = self.call("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "place": "https://forum.example.test/c/makers/t/third"})
        self.assertEqual(used_up.status_code, 429)
        # The person can still approve one exact post elsewhere.
        approved = self.ok("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "place": "https://club.example.test/new-members",
                                                                   "approved_by_owner": True}, status=201)
        self.assertEqual(approved["via"], "post")
        campaign = self.ok("ama", "POST", "/api/v1/agent/checkin", {})["campaign"]
        self.assertEqual(campaign["share_permission"]["left_this_week"], 0)
        self.assertEqual(campaign["link"], f"https://agents.example.test/r/{code}")
        self.now += timedelta(days=8)
        suggestions = self.ok("ama", "POST", "/api/v1/agent/checkin", {})["sharing_suggestions"]
        self.assertEqual(suggestions[0]["kind"], "public_place")
        self.assertEqual(self.ok("ama", "DELETE", "/api/v1/agent/share-permission"), {"active": False, "removed": True})
        self.assertEqual(self.call("ama", "POST", "/api/v1/agent/share",
                                   {"kind": "referral_code", "place": "https://forum.example.test/c/makers/t/fourth"}).status_code, 409)
        self.client.cookies.set(SESSION_COOKIE, self.sessions["ama"])
        csrf = self.app.state.auth.sessions.current(self.sessions["ama"], refresh=False).csrf
        shares = self.client.post("/app/account/export", data={"csrf": csrf}).json()["agent_network"]["share_approvals"]
        self.assertEqual([(row["place"], row["via"]) for row in shares],
                         [("https://forum.example.test/c/makers/t/finding-a-cofounder", "standing"),
                          ("https://forum.example.test/c/makers/t/clubs", "standing"),
                          ("https://club.example.test/new-members", "post")])

    def test_checkin_tells_each_member_how_far_they_are_from_the_top(self):
        from connectthebots_server.campaign import rank_targets
        with self.db.transaction() as connection:
            for index, (referrer, referred) in enumerate((("ama", "joon"), ("ama", "rui"), ("sol", "tam"))):
                connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at,qualified_at) VALUES(?,?,?,?,?,?,?)",
                                   (f"r{index}", referrer, referred, "CTB-AAAAAA", "agent", storage_time(self.now), storage_time(self.now)))
        targets = {person: self.ok(person, "POST", "/api/v1/agent/checkin", {})["campaign"]["referral_rank"] for person in ("ama", "sol", "rui")}
        self.assertEqual(targets["ama"], {"rank": 1, "of": 2, "qualified": 2, "top_percent": 10, "more_for_top_percent": 0, "more_for_first": 0})
        self.assertEqual(targets["sol"], {"rank": 2, "of": 2, "qualified": 1, "top_percent": 10, "more_for_top_percent": 2, "more_for_first": 2})
        self.assertEqual(targets["rui"], {"rank": None, "of": 2, "qualified": 0, "top_percent": 10, "more_for_top_percent": 3, "more_for_first": 3})
        # Hand-worked: a tie for first still needs one more to lead alone; nobody ranked yet means one referral takes the top.
        tied = [{"user_id": "a", "value": 2, "rank": 1}, {"user_id": "b", "value": 2, "rank": 1}]
        self.assertEqual(rank_targets(tied, "a", 50)["more_for_first"], 1)
        self.assertEqual(rank_targets([], "a", 10)["more_for_top_percent"], 1)
        self.assertIsNone(rank_targets(tied, "a", 0)["more_for_top_percent"])

    def test_public_leaderboard_shows_only_opted_in_names_and_share_links_carry_the_code(self):
        empty = self.client.get("/leaderboard")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertIn("No qualified referrals yet", empty.text)
        with self.db.transaction() as connection:
            for index, (referrer, referred) in enumerate((("ama", "joon"), ("ama", "rui"), ("sol", "tam"))):
                connection.execute("INSERT INTO referrals(id,referrer_id,referred_id,code,entered_via,entered_at,qualified_at) VALUES(?,?,?,?,?,?,?)",
                                   (f"r{index}", referrer, referred, "CTB-AAAAAA", "agent", storage_time(self.now), storage_time(self.now)))
        self.ok("ama", "PATCH", "/api/v1/agent/profile", {"first_name": "Ama", "show_name_on_leaderboards": True, "approved_by_owner": True})
        self.ok("sol", "PATCH", "/api/v1/agent/profile", {"first_name": "Sol"})
        page = self.client.get("/leaderboard").text
        self.assertIn('<li value="1">Ama · 2</li>', page)
        self.assertIn('<li value="2">A member · 1</li>', page)
        self.assertIn("Top connector right now: <strong>Ama</strong>", page)
        self.assertNotIn("Sol", page)
        self.assertNotRegex(page, r"m_[0-9a-f]{16}")
        code = self.code("ama")
        link = self.client.get("/r/" + code.lower())
        self.assertEqual(link.status_code, 200)
        self.assertIn(f'href="/signin?ref={code}"', link.text)
        self.assertIn('property="og:image" content="https://agents.example.test/assets/trailer-poster.jpg"', link.text)
        self.assertNotIn("Ama", link.text)
        self.assertEqual(self.client.get("/r/CTB-AAAAAA").status_code, 404)
        self.assertEqual(self.client.get("/r/not-a-code").status_code, 404)
        with self.db.transaction() as connection:
            growth_config.store(connection, "public_leaderboard", "off", self.now)
        self.assertEqual(self.client.get("/leaderboard").status_code, 404)

    def test_dashboard_shows_share_link_and_stops_automatic_sharing(self):
        self.horror_fan("ama")
        self.ok("ama", "PUT", "/api/v1/agent/share-permission",
                {"places": ["https://forum.example.test/c/makers"], "weekly_limit": 1, "approved_by_owner": True})
        self.client.cookies.set(SESSION_COOKIE, self.sessions["ama"])
        page = self.client.get("/app").text
        self.assertIn(f"data-share-link>https://agents.example.test/r/{self.code('ama')}<", page)
        self.assertIn('data-share-permission="on"', page)
        self.assertIn("https://forum.example.test/c/makers, up to 1 time a week", page)
        csrf = self.app.state.auth.sessions.current(self.sessions["ama"], refresh=False).csrf
        self.assertEqual(self.client.post("/app/share-permission/remove", data={"csrf": "wrong"}).status_code, 403)
        stopped = self.client.post("/app/share-permission/remove", data={"csrf": csrf}, follow_redirects=False)
        self.assertEqual(stopped.status_code, 303)
        self.assertFalse(self.ok("ama", "GET", "/api/v1/agent/share-permission")["active"])
        self.assertIn('data-share-permission="off"', self.client.get("/app").text)

    def test_share_qr_is_drawn_here_only_for_real_codes(self):
        code = self.code("ama")
        svg = self.client.get(f"/r/{code}/qr.svg")
        self.assertEqual(svg.status_code, 200)
        self.assertEqual(svg.headers["content-type"], "image/svg+xml")
        self.assertIn(code, svg.text)
        self.assertNotIn("<script", svg.text)
        png = self.client.get(f"/r/{code.lower()}/qr.png")
        self.assertEqual((png.status_code, png.content[:4]), (200, b"\x89PNG"))
        self.assertNotIn("content-disposition", png.headers)
        saved = self.client.get(f"/r/{code}/qr.png?download=1")
        self.assertEqual(saved.headers["content-disposition"], f'attachment; filename="connect-the-bots-{code}.png"')
        for path in ("/r/CTB-AAAAAA/qr.svg", "/r/not-a-code/qr.png", f"/r/{code}/qr.gif", f"/r/{code}/qr.svg.png"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_agents_get_one_tap_share_options_and_the_rules(self):
        code = self.code("ama")
        options = self.ok("ama", "GET", "/api/v1/agent/share-options")
        link = f"https://agents.example.test/r/{code}"
        self.assertEqual(options["link"], link)
        self.assertEqual(options["qr"]["svg"], link + "/qr.svg")
        self.assertEqual({network["id"] for network in options["networks"]},
                         {"whatsapp", "instagram", "facebook", "x", "bluesky", "linkedin", "telegram", "email"})
        self.assertFalse(options["share_permission"]["active"])
        self.assertTrue(any("yes" in rule for rule in options["rules"]))
        # Reading options records nothing and spends no weekly allowance.
        with self.db.transaction() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM share_approvals").fetchone()[0], 0)
        # Recording an approved share returns the same options with its own text.
        shared = self.ok("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code", "approved_by_owner": True}, status=201)
        self.assertEqual(shared["options"]["text"], shared["text"])
        self.assertIn(link, shared["options"]["networks"][0]["url"].replace("%3A", ":").replace("%2F", "/"))
        self.assertEqual(self.call("ama", "POST", "/api/v1/agent/share", {"kind": "referral_code"}).status_code, 409)

    def test_dashboard_puts_one_tap_sharing_and_agent_status_up_front(self):
        self.horror_fan("ama")
        self.client.cookies.set(SESSION_COOKIE, self.sessions["ama"])
        page = self.client.get("/app").text
        code = self.code("ama")
        self.assertIn('data-agent-state="connected"', page)
        self.assertIn("Your agent is on it.", page)
        self.assertIn(f'src="https://agents.example.test/r/{code}/qr.svg"', page)
        self.assertIn(f'data-copy="https://agents.example.test/r/{code}"', page)
        for network in ("whatsapp", "instagram", "facebook", "x", "bluesky"):
            self.assertIn(f'data-network="{network}"', page)
        self.assertIn('href="https://wa.me/?text=', page)
        self.assertIn('rel="noopener noreferrer"', page)
        self.assertIn('<script src="/assets/share.js" defer></script>', page)


if __name__ == "__main__":
    unittest.main()
