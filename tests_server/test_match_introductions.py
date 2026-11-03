"""Introductions straight from a fingerprint or taste match, without board postings.

Fictional members only. A match request needs the owner's yes, a pair that
matches under both people's own rules, and the recipient's own yes on their
dashboard. Each person sees only their own side of why they match.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SERVER = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if SERVER:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time


@unittest.skipUnless(SERVER, "optional server extra not installed")
class MatchIntroductionTests(unittest.TestCase):
    PEOPLE = ("rowan", "wren", "ivo", "tamsin")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://agents.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-only-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.db, self.interactions = self.app.state.database, self.app.state.interactions
        self.keys, self.sessions = {}, {}
        with self.db.transaction() as connection:
            for person in self.PEOPLE:
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (person, "fictional-" + person, person + "@example.test", person.title() + " Example",
                                    storage_time(self.now), storage_time(self.now)))
                self.sessions[person] = self.app.state.auth.sessions.create(connection, person)
        for person in self.PEOPLE:
            self.keys[person] = self.app.state.reports.create_key(person, "Fictional agent")["key"]
            self.ok(person, "PATCH", "/api/v1/agent/profile", {"first_name": person.title()})
        # Rowan and Wren share climbing and chess. Ivo shares nothing with them.
        # Tamsin has not finished the interview.
        self.interview("rowan", {"hobbies": ["climbing", "chess"]})
        self.interview("wren", {"hobbies": ["climbing", "chess"], "music": ["jazz"]})
        self.interview("ivo", {"hobbies": ["origami"]})
        self.ids = {person: self.ok(person, "GET", "/api/v1/agent/me")["member"]["public_id"] for person in self.PEOPLE}

    def call(self, person, method, path, body=None):
        headers = {"Authorization": "Bearer " + self.keys[person]}
        if body is not None:
            return self.client.request(method, path, content=json.dumps(body), headers={**headers, "Content-Type": "application/json"})
        return self.client.request(method, path, headers=headers)

    def ok(self, person, method, path, body=None, status=200):
        response = self.call(person, method, path, body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def interview(self, person, answers):
        self.ok(person, "PUT", "/api/v1/agent/interview/answers", {"answers": [
            *({"topic": topic, "items": items, "tier": "share"} for topic, items in answers.items()),
            {"topic": "looking_for", "items": ["friends"], "tier": "share"},
            {"topic": "food", "items": ["toast"], "tier": "never"},
            {"topic": "books", "items": ["atlases"], "tier": "never"},
            {"topic": "tv", "items": ["quiz shows"], "tier": "never"}]})
        self.ok(person, "POST", "/api/v1/agent/interview/complete", {})

    def ask(self, person, other, status=201, **body):
        body = {"note": "Fancy a climb and a game of chess?", "reason": "You both climb and play chess", "approved_by_owner": True} | body
        return self.ok(person, "POST", f"/api/v1/agent/matches/{self.ids[other]}/introduction", body, status=status)

    def test_owner_yes_and_a_current_match_are_required(self):
        self.ask("rowan", "wren", status=409, approved_by_owner=False)
        self.ask("rowan", "wren", status=409, approved_by_owner=None)
        self.ask("rowan", "wren", status=422, extra="no")
        self.ask("rowan", "wren", status=422, note="")
        self.ask("rowan", "ivo", status=404)                          # nothing in common
        self.ask("rowan", "tamsin", status=404)                       # interview not finished
        self.ask("rowan", "rowan", status=404)
        self.ok("rowan", "POST", "/api/v1/agent/matches/m_0000000000000000/introduction",
                {"note": "Hi", "reason": "Hi", "approved_by_owner": True}, status=404)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM introductions").fetchone()[0], 0)

    def test_request_needs_no_posting_and_opens_only_on_the_recipients_yes(self):
        sent = self.ask("rowan", "wren")["introduction"]
        self.assertEqual((sent["source"], sent["state"], sent["direction"]), ("match", "requested", "outgoing"))
        self.assertEqual(sent["actions"], ["withdraw", "block"])
        self.assertIsNone(sent["requester_posting"])
        self.assertEqual(sent["member"], {"public_id": self.ids["wren"], "first_name": "Wren"})
        self.assertIn("climbing", sent["match"]["explanation"])
        self.assertNotIn("strength", sent["match"])
        self.assertNotIn("ask_first_overlaps", sent["match"])
        self.ask("rowan", "wren", status=409)                         # one pending request per pair
        self.ask("wren", "rowan", status=409)
        # The recipient decides on the dashboard, never through an agent key.
        self.client.cookies.set(SESSION_COOKIE, self.sessions["wren"])
        page = self.client.get(f"/app/introductions/{sent['id']}").text
        self.assertIn("Someone you match would like an introduction.", page)
        self.assertIn("Why your agents matched you with Rowan", page)
        self.assertIn("Fancy a climb and a game of chess?", page)
        self.assertIn('action="/app/introductions/' + sent["id"] + '/accept"', page)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 0)
            event = connection.execute("SELECT kind FROM notification_events WHERE user_id='wren' AND kind<>'security_first_login'").fetchone()
        self.assertEqual(event["kind"], "introduction_request")
        incoming = self.interactions.get_introduction("wren", sent["id"])
        result = self.interactions.accept("wren", sent["id"], incoming["context_hash"])
        self.assertEqual(result["introduction"]["state"], "accepted")
        self.assertIsNotNone(result["conversation"]["id"])

    def test_each_person_sees_only_their_own_side(self):
        # Rowan asks for jazz; that preference is Rowan's alone and Wren must never see it.
        jazz = {"kind": "interest", "topic": "music", "item": "jazz", "importance": "nice"}
        self.ok("rowan", "PUT", "/api/v1/agent/match-settings", {"preferences": [jazz], "approved_by_owner": True})
        sent = self.ask("rowan", "wren")["introduction"]
        self.assertIn("Fits what you asked for: jazz (music).", sent["match"]["explanation"])
        incoming = self.interactions.get_introduction("wren", sent["id"])
        self.assertEqual(incoming["member"], {"public_id": self.ids["rowan"], "first_name": "Rowan"})
        self.assertNotIn("asked for", json.dumps(incoming))
        self.assertNotIn("jazz", json.dumps(incoming))
        self.assertEqual(incoming["match"]["asked_for"], [])
        exported = self.interactions.export_owner("wren")["introductions"][0]
        self.assertNotIn("jazz", json.dumps(exported))
        self.assertIsNone(exported["own_posting_snapshot"])

    def test_request_lapses_when_the_pair_stops_matching(self):
        sent = self.ask("rowan", "wren")["introduction"]
        # Wren now must have someone into jazz; Rowan is not, so the match is gone both ways.
        must = {"kind": "interest", "topic": "music", "item": "jazz", "importance": "must"}
        self.ok("wren", "PUT", "/api/v1/agent/match-settings", {"preferences": [must], "approved_by_owner": True})
        incoming = self.interactions.get_introduction("wren", sent["id"])
        self.assertEqual(incoming["state"], "withdrawn")
        self.assertIsNone(incoming["match"])
        self.assertIsNone(incoming["context_hash"])
        self.assertNotIn("accept", incoming["actions"])

    def test_block_and_expiry_end_the_request(self):
        sent = self.ask("rowan", "wren")["introduction"]
        self.ok("wren", "POST", "/api/v1/agent/block", {"member": self.ids["rowan"]})
        self.assertEqual(self.interactions.get_introduction("rowan", sent["id"])["state"], "unavailable")
        self.ask("rowan", "wren", status=404)
        # A fresh pair: requests still expire after 14 days.
        self.interview("ivo", {"hobbies": ["climbing"]})
        later = self.ask("rowan", "ivo")["introduction"]
        self.now += timedelta(days=14, seconds=1)
        self.assertEqual(self.interactions.get_introduction("ivo", later["id"])["state"], "expired")

    def test_the_same_request_limits_apply(self):
        for person in ("wren", "ivo", "tamsin"):
            self.interview(person, {"hobbies": ["climbing", "chess"]})
        extra = []
        with self.db.transaction() as connection:
            for index in range(3):
                person = f"extra{index}"
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                                   (person, "fictional-" + person, person + "@example.test", "Extra Example", storage_time(self.now), storage_time(self.now)))
                extra.append(person)
        for person in extra:
            self.keys[person] = self.app.state.reports.create_key(person, "Fictional agent")["key"]
            self.interview(person, {"hobbies": ["climbing"]})
            self.ids[person] = self.ok(person, "GET", "/api/v1/agent/me")["member"]["public_id"]
        for other in ("wren", "ivo", "tamsin", "extra0", "extra1"):
            self.ask("rowan", other)
        self.ask("rowan", "extra2", status=429)

    def test_opening_notifications_or_sending_mail_keeps_a_pending_request(self):
        # Regression: the notification service built its own InteractionService
        # without a taste service, so every use-time check of a match request
        # (the notifications page, the mail worker) marked it withdrawn.
        sent = self.ask("rowan", "wren")["introduction"]
        self.client.cookies.set(SESSION_COOKIE, self.sessions["wren"])
        page = self.client.get("/app/notifications")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/app/introductions/" + sent["id"], page.text)
        self.app.state.notifications.run_due()
        # The standalone mail worker and operator commands build their own services.
        from connectthebots_server.notifications import NotificationService
        NotificationService(self.db, lambda: self.now, self.app.state.settings).run_due()
        incoming = self.interactions.get_introduction("wren", sent["id"])
        self.assertEqual(incoming["state"], "requested")
        self.assertIn("accept", incoming["actions"])
        detail = self.client.get(f"/app/introductions/{sent['id']}").text
        self.assertIn('action="/app/introductions/' + sent["id"] + '/accept"', detail)
        # Before the fix (taste missing) the same check reports no match.
        from connectthebots_server.interactions import InteractionService
        bare = InteractionService(self.db, lambda: self.now, self.app.state.board)
        with self.db.connect() as connection:
            self.assertIsNotNone(bare._matched(connection, "rowan", "wren"))

    def test_bell_and_dashboard_count_a_match_request_and_its_conversation(self):
        # Regression: the bell's SQL required two posting grants, so a match
        # request (no postings) always showed 0 unread.
        sent = self.ask("rowan", "wren")["introduction"]
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.sessions["wren"])
        self.assertIn("data-notification-count>1<", self.client.get("/app").text)
        self.assertEqual(self.app.state.notifications.unread_count("wren"), 1)
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.sessions["rowan"])
        page = self.client.get("/app").text
        self.assertIn('data-pending-outgoing="1"', page)
        self.assertNotIn("data-open-conversations", page)
        incoming = self.interactions.get_introduction("wren", sent["id"])
        self.interactions.accept("wren", sent["id"], incoming["context_hash"])
        page = self.client.get("/app").text
        self.assertIn('data-open-conversations="1"', page)
        self.assertIn('href="/app/conversations"', page)
        self.assertNotIn("data-pending-outgoing", page)

    def test_dashboard_links_own_topic_posts_and_the_topic_boards(self):
        post = self.ok("rowan", "POST", "/api/v1/boards/games/posts",
                       {"body": "Chess by the river on Sunday mornings.", "approved_by_owner": True}, status=201)["post"]
        gone = self.ok("rowan", "POST", "/api/v1/boards/games/posts",
                       {"body": "A post my person withdrew later on.", "approved_by_owner": True}, status=201)["post"]
        self.ok("rowan", "DELETE", f"/api/v1/boards/posts/{gone['id']}")
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.sessions["rowan"])
        page = self.client.get("/app").text
        self.assertIn('<a href="/boards">Topic boards</a>', page)
        self.assertIn(f'<a href="/boards/posts/{post["id"]}">', page)
        self.assertIn("Chess by the river on Sunday mornings.", page)
        self.assertNotIn(f'href="/boards/posts/{gone["id"]}"', page)
        self.assertIn("withdrawn", page)

    def test_agent_guide_and_dashboard_list_name_it(self):
        sent = self.ask("rowan", "wren")["introduction"]
        self.client.cookies.set(SESSION_COOKIE, self.sessions["rowan"])
        listing = self.client.get("/app/introductions").text
        self.assertIn("Waiting for your match to decide.", listing)
        self.assertIn(sent["id"], listing)
        texts = self.ok("rowan", "GET", "/api/v1/agent/texts")
        self.assertIn("/introduction", json.dumps(texts))


if __name__ == "__main__":
    unittest.main()
