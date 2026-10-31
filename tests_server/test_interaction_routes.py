"""Real two-person web choices, exact previews and private message boundaries."""
from datetime import datetime, timedelta, timezone
from html import unescape
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time, token_hash


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class InteractionWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://interactions.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-interaction-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(self.settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=self.settings.public_base_url))
        self.auth, self.db = self.app.state.auth, self.app.state.database
        self.board, self.interactions = self.app.state.board, self.app.state.interactions
        self.tokens, self.postings = {}, {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon", "sora"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "private-google-sub-" + owner, owner + "@private.example.test", "PrivateGoogleName" + owner,
                     storage_time(self.now), storage_time(self.now)))
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        for owner in self.tokens:
            draft = self.board.save_draft(owner, self.payload(owner))
            self.postings[owner] = self.board.publish(owner, draft["revision"], draft["payload_hash"])
        self.use("ama")

    def payload(self, owner):
        return {"text": f"I build tools for {owner} gardens. I seek a thoughtful collaborator. I offer a small first experiment.",
                "tags": ["Research", "Open data"], "looking_for": "A small shared trial", "offering": "Careful prototyping"}

    def use(self, owner):
        self.owner = owner
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=False)

    def field(self, response, name):
        self.assertEqual(response.status_code, 200, response.text)
        match = re.search(r'name="' + name + r'" value="([^"]+)"', response.text)
        self.assertIsNotNone(match, response.text)
        return unescape(match.group(1))

    def preview(self, *, target="joon", note="Could we try one small experiment?", reason="Your field work, my careful prototyping"):
        response = self.post("/app/introductions/preview", target_slug=self.postings[target]["slug"], note=note, reason=reason)
        return self.field(response, "request_token"), response

    def request(self, **kwargs):
        token, preview = self.preview(**kwargs)
        response = self.post("/app/introductions", request_token=token)
        self.assertEqual(response.status_code, 303, response.text)
        return response.headers["location"].rsplit("/", 1)[-1], token, preview

    def accept(self, intro_id):
        self.use("joon")
        token = self.field(self.client.get("/app/introductions/" + intro_id), "accept_token")
        response = self.post("/app/introductions/" + intro_id + "/accept", accept_token=token)
        self.assertEqual(response.status_code, 303, response.text)
        return response.headers["location"].rsplit("/", 1)[-1], token

    def conversation(self):
        intro_id, _, _ = self.request()
        conversation_id, _ = self.accept(intro_id)
        return intro_id, conversation_id

    def count(self, table):
        self.assertIn(table, {"introductions", "conversations", "messages", "user_blocks"})
        with self.db.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_exact_preview_is_read_only_then_request_waits_for_recipient(self):
        page = self.client.get("/app/introductions/new?slug=" + self.postings["joon"]["slug"])
        self.assertIn("Preview request", page.text)
        token, preview = self.preview()
        self.assertEqual((self.count("introductions"), self.count("conversations")), (0, 0))
        for owner in ("ama", "joon"):
            self.assertIn(self.postings[owner]["text"], preview.text)
        self.assertIn("Could we try one small experiment?", preview.text)
        self.assertIn("Your field work, my careful prototyping", preview.text)
        response = self.post("/app/introductions", request_token=token)
        self.assertEqual(response.status_code, 303)
        detail = self.client.get(response.headers["location"])
        self.assertIn("Waiting for the posting’s author to decide.", detail.text)
        self.assertNotIn(">Yes</button>", detail.text)
        self.assertEqual(self.count("conversations"), 0)
        self.use("joon")
        incoming = self.client.get(response.headers["location"])
        self.assertIn("Someone would like an introduction.", incoming.text)
        self.assertIn(">Yes</button>", incoming.text)
        self.assertIn(">Not now</button>", incoming.text)
        self.assertNotIn("PrivateGoogleNameama", incoming.text)
        self.assertNotIn("ama@private.example.test", incoming.text)

    def test_two_people_accept_send_escaped_messages_and_replay_is_idempotent(self):
        intro_id, request_token, _ = self.request()
        self.assertEqual(self.post("/app/introductions", request_token=request_token).status_code, 303)
        self.assertEqual(self.count("introductions"), 1)
        conversation_id, accept_token = self.accept(intro_id)
        self.assertEqual(self.post(f"/app/introductions/{intro_id}/accept", accept_token=accept_token).status_code, 303)
        self.assertEqual(self.count("conversations"), 1)
        path = "/app/conversations/" + conversation_id
        message_token = self.field(self.client.get(path), "message_token")
        text = "  Hello <script>alert('x')</script>\n**Plain words**, not Markdown.  "
        for _ in range(2):
            self.assertEqual(self.post(path + "/messages", message_token=message_token, text=text).status_code, 303)
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.post(path + "/messages", message_token=message_token, text="Different words").status_code, 409)
        self.use("ama")
        response = self.client.get(path)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertIn("**Plain words**", response.text)
        self.assertIn("The other person", response.text)
        self.assertNotIn("PrivateGoogleNamejoon", response.text)
        self.assertNotIn("joon@private.example.test", response.text)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT body FROM messages").fetchone()[0], text)
        reply = self.field(response, "message_token")
        self.assertEqual(self.post(path + "/messages", message_token=reply, text="Yes, a small trial sounds good.").status_code, 303)
        self.assertEqual(self.count("messages"), 2)

    def test_request_token_tamper_expiry_and_other_owner_never_create(self):
        token, _ = self.preview()
        changed = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertEqual(self.post("/app/introductions", request_token=changed).status_code, 400)
        self.use("sora")
        self.assertEqual(self.post("/app/introductions", request_token=token).status_code, 400)
        self.use("ama")
        self.now += timedelta(minutes=10)
        self.assertEqual(self.post("/app/introductions", request_token=token).status_code, 400)
        self.assertEqual(self.count("introductions"), 0)

    def test_republished_posting_invalidates_request_and_recipient_acceptance(self):
        token, _ = self.preview()
        draft = self.board.save_draft("joon", self.payload("new project"))
        self.board.publish("joon", draft["revision"], draft["payload_hash"])
        self.assertEqual(self.post("/app/introductions", request_token=token).status_code, 409)
        intro_id, _, _ = self.request()
        self.use("joon")
        accept_token = self.field(self.client.get("/app/introductions/" + intro_id), "accept_token")
        self.board.unpublish("ama")
        self.assertIn(self.post(f"/app/introductions/{intro_id}/accept", accept_token=accept_token).status_code, (409, 410))
        self.assertEqual(self.count("conversations"), 0)

    def test_every_mutation_requires_csrf_origin_and_rejects_owner_selectors(self):
        intro_id, request_token, _ = self.request()
        conversation_id, accept_token = self.accept(intro_id)
        message_token = self.field(self.client.get("/app/conversations/" + conversation_id), "message_token")
        cases = [("/app/introductions/preview", {"target_slug": self.postings["ama"]["slug"], "note": "Hello", "reason": "Shared project"}),
                 ("/app/introductions", {"request_token": request_token}),
                 (f"/app/introductions/{intro_id}/accept", {"accept_token": accept_token}),
                 (f"/app/conversations/{conversation_id}/messages", {"message_token": message_token, "text": "Hello"})]
        cases += [(f"/app/introductions/{intro_id}/{action}", {}) for action in ("decline", "withdraw", "block")]
        cases += [(f"/app/conversations/{conversation_id}/{action}", {}) for action in ("close", "block")]
        for path, values in cases:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data={"csrf": "0" * 64, **values}).status_code, 403)
                self.assertEqual(self.client.post(path, data={"csrf": self.csrf, **values}, headers={"Origin": "https://foreign.example"}).status_code, 403)
                self.assertEqual(self.post(path, owner_id="ama", **values).status_code, 400)
                self.assertEqual(self.post(path + "?owner=ama", **values).status_code, 400)
        self.assertEqual((self.count("messages"), self.count("user_blocks")), (0, 0))

    def test_nonparticipant_cannot_read_decide_message_leave_or_block(self):
        intro_id, conversation_id = self.conversation()
        message_token = self.field(self.client.get("/app/conversations/" + conversation_id), "message_token")
        self.use("sora")
        self.assertEqual(self.client.get("/app/introductions/" + intro_id).status_code, 404)
        self.assertEqual(self.client.get("/app/conversations/" + conversation_id).status_code, 404)
        for action in ("decline", "withdraw", "block"):
            self.assertEqual(self.post(f"/app/introductions/{intro_id}/{action}").status_code, 404)
        for action in ("close", "block"):
            self.assertEqual(self.post(f"/app/conversations/{conversation_id}/{action}").status_code, 404)
        self.assertEqual(self.post(f"/app/conversations/{conversation_id}/messages", message_token=message_token, text="Intrusion").status_code, 400)
        self.assertNotIn(conversation_id, self.client.get("/app/conversations").text)
        self.assertNotIn(intro_id, self.client.get("/app/introductions").text)
        self.assertEqual(self.count("messages"), 0)

    def test_decline_withdraw_and_expiry_do_not_open_conversations(self):
        intro_id, _, _ = self.request()
        self.use("joon")
        self.assertEqual(self.post(f"/app/introductions/{intro_id}/decline").status_code, 303)
        self.assertIn("This introduction was declined.", self.client.get("/app/introductions/" + intro_id).text)
        self.use("ama")
        intro_id, _, _ = self.request()
        self.assertEqual(self.post(f"/app/introductions/{intro_id}/withdraw").status_code, 303)
        self.assertIn("This request was withdrawn.", self.client.get("/app/introductions/" + intro_id).text)
        intro_id, _, _ = self.request()
        self.now += timedelta(days=14)
        self.use("joon")
        response = self.client.get("/app/introductions/" + intro_id)
        self.assertIn("This request expired.", response.text)
        self.assertNotIn('name="accept_token"', response.text)
        self.assertEqual(self.count("conversations"), 0)

    def test_block_is_silent_and_board_filter_uses_only_current_cookie(self):
        intro_id, _, _ = self.request()
        self.use("joon")
        self.assertEqual(self.post(f"/app/introductions/{intro_id}/block").status_code, 303)
        for owner, peer in (("ama", "joon"), ("joon", "ama")):
            self.use(owner)
            response = self.client.get("/board.json")
            self.assertNotIn(self.postings[peer]["slug"], response.text)
            self.assertEqual(self.client.get("/board/" + self.postings[peer]["slug"]).status_code, 404)
            self.assertEqual(self.client.get("/board.json?viewer_id=sora").status_code, 400)
        self.use("ama")
        response = self.client.get("/app/introductions/" + intro_id)
        self.assertIn("This introduction is no longer available.", response.text)
        self.assertNotIn('data-introduction-status="blocked"', response.text)
        self.assertNotIn("blocked you", response.text)
        self.client.cookies.clear()
        self.assertIn(self.postings["joon"]["slug"], self.client.get("/board.json").text)

    def test_leave_closes_for_both_and_old_message_form_cannot_send(self):
        _, conversation_id = self.conversation()
        path = "/app/conversations/" + conversation_id
        old_token = self.field(self.client.get(path), "message_token")
        self.assertEqual(self.post(path + "/close").status_code, 303)
        for owner in ("ama", "joon"):
            self.use(owner)
            response = self.client.get(path)
            self.assertIn("This conversation is closed.", response.text)
            self.assertNotIn('name="message_token"', response.text)
        self.assertEqual(self.post(path + "/messages", message_token=old_token, text="Too late").status_code, 409)
        self.assertEqual(self.count("messages"), 0)

    def test_message_form_is_conversation_bound_and_expires(self):
        _, conversation_id = self.conversation()
        path = "/app/conversations/" + conversation_id
        token = self.field(self.client.get(path), "message_token")
        self.assertEqual(self.post("/app/conversations/" + "a" * 43 + "/messages", message_token=token, text="Wrong thread").status_code, 400)
        self.now += timedelta(minutes=10)
        self.assertEqual(self.post(path + "/messages", message_token=token, text="Expired").status_code, 400)
        self.assertEqual(self.count("messages"), 0)

    def test_native_form_crlf_is_normalized_but_bare_carriage_return_is_rejected(self):
        _, conversation_id = self.conversation()
        path = "/app/conversations/" + conversation_id
        token = self.field(self.client.get(path), "message_token")
        self.assertEqual(self.post(path + "/messages", message_token=token, text="  First line\r\nSecond line  ").status_code, 303)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT body FROM messages").fetchone()[0], "  First line\nSecond line  ")
        fresh = self.field(self.client.get(path), "message_token")
        self.assertEqual(self.post(path + "/messages", message_token=fresh, text="First\rSecond").status_code, 422)
        self.assertEqual(self.count("messages"), 1)

    def test_recipient_context_token_expires_and_other_purposes_cannot_accept(self):
        intro_id, request_token, _ = self.request()
        self.use("joon")
        path = "/app/introductions/" + intro_id
        accept_token = self.field(self.client.get(path), "accept_token")
        self.assertEqual(self.post(path + "/accept", accept_token=request_token).status_code, 400)
        self.now += timedelta(minutes=10)
        self.assertEqual(self.post(path + "/accept", accept_token=accept_token).status_code, 400)
        self.assertEqual(self.count("conversations"), 0)
        refreshed = self.field(self.client.get(path), "accept_token")
        self.assertEqual(self.post(path + "/accept", accept_token=refreshed).status_code, 303)

    def test_conversation_block_closes_both_sides_and_refuses_new_contact(self):
        _, conversation_id = self.conversation()
        path = "/app/conversations/" + conversation_id
        old_token = self.field(self.client.get(path), "message_token")
        self.assertEqual(self.post(path + "/block").status_code, 303)
        self.assertEqual(self.post(path + "/messages", message_token=old_token, text="After blocking").status_code, 409)
        self.use("ama")
        response = self.client.get(path)
        self.assertIn("This conversation is closed.", response.text)
        self.assertNotIn("blocked you", response.text)
        self.assertNotIn('name="message_token"', response.text)
        preview = self.post("/app/introductions/preview", target_slug=self.postings["joon"]["slug"], note="Another try", reason="The same project")
        self.assertEqual(preview.status_code, 404)

    def test_preview_escapes_note_and_reason_and_rejects_scores_before_storage(self):
        token, response = self.preview(note="<script>alert('x')</script> **Hello**", reason="<b>Our work</b>")
        self.assertIn("&lt;script&gt;", response.text)
        self.assertIn("&lt;b&gt;Our work&lt;/b&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertIn("**Hello**", response.text)
        self.assertEqual(self.count("introductions"), 0)
        self.assertEqual(self.post("/app/introductions/preview", target_slug=self.postings["joon"]["slug"], note="A 90% match", reason="Our work").status_code, 422)
        intro_id, _, _ = self.request()
        conversation_id, _ = self.accept(intro_id)
        path = "/app/conversations/" + conversation_id
        message_token = self.field(self.client.get(path), "message_token")
        self.assertEqual(self.post(path + "/messages", message_token=message_token, text="A 90% match").status_code, 422)
        self.assertEqual(self.count("messages"), 0)

    def test_older_messages_are_reachable_without_cross_conversation_cursor(self):
        _, conversation_id = self.conversation()
        for index in range(51):
            self.interactions.send_message("joon", conversation_id, f"Message {index:02d}", f"message-nonce-{index:08d}")
        path = "/app/conversations/" + conversation_id
        response = self.client.get(path)
        self.assertNotIn("Message 00", response.text)
        self.assertIn("Message 50", response.text)
        match = re.search(r'href="([^"]+\?before=[^"]+)"', response.text)
        self.assertIsNotNone(match)
        older = self.client.get(unescape(match.group(1)))
        self.assertIn("Message 00", older.text)
        self.assertIn("Latest messages", older.text)
        self.assertNotIn("Message 50", older.text)
        self.assertEqual(self.client.get(path + "?before=" + "a" * 43).status_code, 404)

    def test_history_pagination_reaches_older_requests_and_conversations(self):
        first_intro = first_conversation = None
        for index in range(21):
            self.now += timedelta(days=1)
            preview = self.interactions.prepare_request("ama", self.postings["joon"]["slug"], "One small trial", "A shared project")
            intro = self.interactions.request("ama", preview, f"intro-nonce-{index:08d}")
            conversation = self.interactions.accept("joon", intro["id"], intro["context_hash"])["conversation"]
            self.interactions.close_conversation("ama", conversation["id"])
            if index == 0:
                first_intro, first_conversation = intro["id"], conversation["id"]
        for path, first in (("/app/introductions", first_intro), ("/app/conversations", first_conversation)):
            newest = self.client.get(path)
            self.assertIn("Next page", newest.text)
            self.assertNotIn(first, newest.text)
            older = self.client.get(path + "?page=2")
            self.assertIn(first, older.text)
            self.assertIn("Previous page", older.text)
            for suffix in ("?page=0", "?page=10001", "?page=1&page=2", "?owner=joon"):
                self.assertEqual(self.client.get(path + suffix).status_code, 400)

    def test_invalid_activity_does_not_refresh_and_valid_activity_has_absolute_limit(self):
        def expiry():
            with self.db.connect() as connection:
                return connection.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash(self.tokens["ama"]),)).fetchone()[0]
        original = expiry()
        self.now += timedelta(days=29)
        self.assertEqual(self.post("/app/introductions", csrf="0" * 64, request_token="invalid").status_code, 403)
        self.assertEqual(expiry(), original)
        self.assertEqual(self.client.get("/app/introductions?page=0").status_code, 400)
        self.assertEqual(expiry(), original)
        self.assertEqual(self.client.get("/app/introductions").status_code, 200)
        for _ in range(2):
            self.now += timedelta(days=29)
            self.assertEqual(self.client.get("/app/conversations").status_code, 200)
        self.assertEqual(expiry(), storage_time(datetime(2026, 10, 30, 12, tzinfo=timezone.utc) + timedelta(days=90)))
        self.now += timedelta(days=3)
        self.assertEqual(self.client.get("/app/introductions", follow_redirects=False).status_code, 303)

    def test_bearer_key_never_authorizes_human_choices_and_get_cannot_mutate(self):
        intro_id, _, _ = self.request()
        key = self.app.state.reports.create_key("ama", "Fictional assistant")["key"]
        self.client.cookies.clear()
        response = self.client.post(f"/app/introductions/{intro_id}/withdraw", headers={"Authorization": "Bearer " + key}, data={"csrf": self.csrf}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/signin")
        self.assertEqual(self.interactions.get_introduction("ama", intro_id)["state"], "requested")
        self.use("ama")
        self.assertEqual(self.client.get(f"/app/introductions/{intro_id}/withdraw").status_code, 405)
        self.assertEqual(self.client.get("/board/" + self.postings["joon"]["slug"] + "?viewer_id=ama").status_code, 400)


if __name__ == "__main__":
    unittest.main()
