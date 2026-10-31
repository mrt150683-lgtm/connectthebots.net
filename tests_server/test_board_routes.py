"""Publication approvals, public projections and authenticated board controls."""
from datetime import datetime, timedelta, timezone
from html import unescape
from http.cookies import SimpleCookie
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.board_routes import BoardJsonLimiter
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time, token_hash


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class BoardWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://board.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-board-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.auth, self.db, self.board = self.app.state.auth, self.app.state.database, self.app.state.board
        self.tokens = {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon"):
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "private-google-sub-" + owner, owner + "@private.example.test", "PrivateGoogleName" + owner,
                     storage_time(self.now), storage_time(self.now)))
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        self.use("ama")

    def use(self, owner):
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def payload(self, topic="gardens"):
        return {"text": f"I build tools for {topic}. I am looking for a thoughtful collaborator. I can offer a small first experiment.",
                "tags": ["Research", "Open data"], "looking_for": "A collaborator for a small trial", "offering": "Careful prototyping"}

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf": self.csrf, **fields}, follow_redirects=False)

    def save(self, payload=None):
        payload = self.payload() if payload is None else payload
        response = self.post("/app/posting", **{**payload, "tags": ", ".join(payload["tags"])})
        self.assertEqual(response.status_code, 303, response.text)

    def preview(self):
        response = self.post("/app/posting/preview")
        self.assertEqual(response.status_code, 200, response.text)
        match = re.search(r'name="publication_token" value="([^"]+)"', response.text)
        self.assertIsNotNone(match)
        return unescape(match.group(1)), response

    def publish(self):
        token, _ = self.preview()
        response = self.post("/app/posting/publish", publication_token=token)
        self.assertEqual(response.status_code, 303, response.text)
        return response.headers["location"].rsplit("/", 1)[-1], token

    def expiry(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash(self.tokens["ama"]),)).fetchone()[0]

    def test_draft_and_preview_never_publish_and_preview_shows_every_field(self):
        payload = self.payload()
        self.save(payload)
        _, preview = self.preview()
        for value in (payload["text"], payload["looking_for"], payload["offering"], *payload["tags"]):
            self.assertIn(value, preview.text)
        self.assertIn("exact words, tags and fields", preview.text)
        self.assertEqual(self.client.get("/board.json").json()["total"], 0)
        self.assertNotIn(payload["text"], self.client.get("/board").text)

    def test_public_projection_excludes_identity_and_requires_no_signin(self):
        self.save()
        slug, token = self.publish()
        self.client.cookies.clear()
        for path in ("/board", "/board.json", "/board/" + slug, "/board/" + slug + ".json"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            for private in ("PrivateGoogleName", "@private.example.test", "private-google-sub", self.tokens["ama"], token):
                self.assertNotIn(private, response.text)
        public = self.client.get("/board/" + slug + ".json").json()
        self.assertEqual(set(public), {"slug", "text", "tags", "looking_for", "offering", "published_at", "updated_at"})
        self.assertEqual(self.client.get("/app/posting", follow_redirects=False).status_code, 303)

    def test_draft_edits_keep_approved_payload_until_new_exact_approval(self):
        first = self.payload("gardens")
        self.save(first)
        slug, old_token = self.publish()
        second = self.payload("libraries")
        second["tags"] = ["Libraries"]
        self.save(second)
        public = self.client.get("/board/" + slug + ".json").json()
        self.assertEqual((public["text"], public["tags"]), (first["text"], first["tags"]))
        self.assertEqual(self.post("/app/posting/publish", publication_token=old_token).status_code, 409)
        same_slug, _ = self.publish()
        self.assertEqual(same_slug, slug)
        self.assertEqual(self.client.get("/board/" + slug + ".json").json()["text"], second["text"])

    def test_unpublish_is_immediate_410_and_old_approval_cannot_resurrect_it(self):
        self.save()
        slug, token = self.publish()
        self.assertEqual(self.post("/app/posting/unpublish").status_code, 303)
        self.assertEqual(self.client.get("/board.json").json()["items"], [])
        for suffix in ("", ".json"):
            response = self.client.get("/board/" + slug + suffix)
            self.assertEqual(response.status_code, 410)
            self.assertNotIn(self.payload()["text"], response.text)
        self.assertEqual(self.post("/app/posting/publish", publication_token=token).status_code, 409)
        self.assertEqual(self.client.get("/board/" + "a" * 32).status_code, 404)

    def test_repeated_publish_has_one_stable_result(self):
        self.save()
        slug, token = self.publish()
        before = self.client.get("/board/" + slug + ".json").json()
        self.now += timedelta(seconds=10)
        response = self.post("/app/posting/publish", publication_token=token)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/board/" + slug + ".json").json(), before)
        self.assertEqual(self.client.get("/board.json").json()["total"], 1)

    def test_expired_tampered_and_other_owner_approvals_fail_without_publication(self):
        self.save()
        token, _ = self.preview()
        changed = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertEqual(self.post("/app/posting/publish", publication_token=changed).status_code, 400)
        self.use("joon")
        self.save(self.payload("other work"))
        self.assertEqual(self.post("/app/posting/publish", publication_token=token).status_code, 400)
        self.use("ama")
        self.now += timedelta(minutes=10)
        self.assertEqual(self.post("/app/posting/publish", publication_token=token).status_code, 400)
        self.assertEqual(self.client.get("/board.json").json()["total"], 0)

    def test_all_owner_forms_require_csrf_origin_and_exact_fields(self):
        self.save()
        token, _ = self.preview()
        payload = self.payload()
        cases = [("/app/posting", {**payload, "tags": "Research"}), ("/app/posting/preview", {}),
                 ("/app/posting/publish", {"publication_token": token}), ("/app/posting/unpublish", {})]
        for path, fields in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data={"csrf": "a" * 64, **fields})
                self.assertEqual(response.status_code, 403)
                response = self.client.post(path, data={"csrf": self.csrf, **fields}, headers={"Origin": "https://foreign.example"})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.post(path, owner_id="joon", **fields).status_code, 400)
        self.assertEqual(self.client.get("/app/posting/publish").status_code, 405)
        self.assertEqual(self.client.get("/board.json").json()["total"], 0)

    def test_public_and_preview_escape_text_and_do_not_render_markdown(self):
        payload = self.payload()
        payload["text"] = "I test <img src=x onerror=alert(1)>. I seek **careful** review. I can offer a small trial."
        payload["looking_for"] = "<script>alert(1)</script>"
        self.save(payload)
        _, preview = self.preview()
        slug, _ = self.publish()
        for response in (preview, self.client.get("/board"), self.client.get("/board/" + slug)):
            self.assertNotIn("<img src=x", response.text)
            self.assertNotIn("<script>alert(1)</script>", response.text)
            self.assertIn("&lt;img", response.text)
            self.assertIn("**careful**", response.text)

    def test_search_and_pagination_agree_between_html_and_json(self):
        with self.db.transaction() as connection:
            for number in range(21):
                user = "extra" + str(number)
                connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (user, "fictional-" + user, user + "@example.test", user, storage_time(self.now), storage_time(self.now)))
        for number in range(21):
            user = "extra" + str(number)
            draft = self.board.save_draft(user, self.payload("orchards"))
            self.board.publish(user, draft["revision"], draft["payload_hash"])
            self.now += timedelta(seconds=1)
        first = self.client.get("/board.json?q=orchards").json()
        second = self.client.get("/board.json?q=orchards&page=2").json()
        self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (21, 20, 1))
        html = self.client.get("/board?q=orchards&page=2")
        self.assertEqual(html.status_code, 200)
        self.assertIn(second["items"][0]["slug"], html.text)
        self.assertNotIn(first["items"][0]["slug"], html.text)
        self.assertIn("Previous page", html.text)
        for query in ("page=0", "page=10001", "page=" + "9" * 1000, "page=1&page=2", "q=x&q=y", "owner=ama"):
            self.assertEqual(self.client.get("/board.json?" + query).status_code, 400)

    def test_unauthenticated_json_limit_is_shared_and_retry_after_refills(self):
        self.client.cookies.clear()
        for _ in range(120):
            self.assertEqual(self.client.get("/board.json").status_code, 200)
        response = self.client.get("/board/" + "a" * 32 + ".json")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertEqual(self.client.get("/board").status_code, 200)
        self.now += timedelta(seconds=1)
        self.assertEqual(self.client.get("/board.json").status_code, 200)
        self.assertEqual(self.client.get("/board.json").status_code, 200)
        self.assertEqual(self.client.get("/board.json").status_code, 429)
        self.use("ama")
        self.assertEqual(self.client.get("/board.json").status_code, 200)

    def test_json_limiter_is_bounded_and_does_not_retain_raw_addresses(self):
        limiter = BoardJsonLimiter(lambda: self.now, "fictional-limit-secret")
        for number in range(4096):
            self.assertEqual(limiter.admit("fictional-address-" + str(number)), 0)
        self.assertEqual(len(limiter.buckets), 4096)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", key) for key in limiter.buckets))
        self.assertEqual(limiter.admit("another-fictional-address"), 60)
        self.now += timedelta(minutes=10)
        self.assertEqual(limiter.admit("another-fictional-address"), 0)
        self.assertEqual(len(limiter.buckets), 1)

    def test_owner_pages_and_valid_public_activity_slide_session_with_absolute_cap(self):
        created, original = self.now, self.expiry()
        self.now += timedelta(days=29)
        self.client.post("/app/posting/preview", data={"csrf": "x" * 64})
        self.assertEqual(self.expiry(), original)
        self.post("/app/posting", text="invalid", tags="", looking_for="a", offering="b")
        self.assertEqual(self.expiry(), original)
        self.client.get("/board?page=0")
        self.assertEqual(self.expiry(), original)
        response = self.client.get("/app/posting")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.expiry(), storage_time(self.now + timedelta(days=30)))
        for day, path in ((58, "/board.json"), (87, "/board")):
            self.now = created + timedelta(days=day)
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.expiry(), storage_time(created + timedelta(days=90)))
        self.now = created + timedelta(days=90)
        self.assertEqual(self.client.get("/app/posting", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/board").status_code, 200)

    def test_suspended_owner_cannot_publish_and_previous_public_post_is_gone(self):
        self.save()
        slug, token = self.publish()
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET suspended_at=? WHERE id='ama'", (storage_time(self.now),))
        self.assertEqual(self.post("/app/posting/publish", publication_token=token).status_code, 303)
        self.assertEqual(self.client.get("/board/" + slug).status_code, 410)
        self.assertEqual(self.client.get("/board.json").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
