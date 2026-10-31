"""A slow independent authority must not monopolize the ASGI event loop."""
import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2", "cryptography"))
if AVAILABLE:
    import httpx
    from connectthebots_server.app import create_app
    from connectthebots_server.auth import Identity
    from connectthebots_server.b2_storage import B2StorageError
    from connectthebots_server.config import Settings
    from connectthebots_server.recovery_authority import RecoveryAuthority, local_state
    from connectthebots_server.sessions import SESSION_COOKIE, random_token, storage_time, token_hash


class DelayedStorage:
    """Fictional append-only provider with a bounded, externally released write."""
    def __init__(self):
        self.files = []
        self.delay = False
        self.fail = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.write_thread = None
        self.timed_out = False

    def list_versions(self, prefix):
        return [{key: value for key, value in item.items() if key != "data"}
                for item in self.files if item["name"].startswith(prefix)]

    def read(self, file_id, max_bytes):
        data = next(item["data"] for item in self.files if item["id"] == file_id)
        if len(data) > max_bytes:
            raise B2StorageError("Storage request failed.")
        return data

    def write(self, name, data, *, metadata=None):
        if self.delay:
            self.write_thread = threading.get_ident()
            self.entered.set()
            self.timed_out = not self.release.wait(5)
        if self.fail:
            raise B2StorageError("Storage request failed.")
        file_id = str(len(self.files) + 1)
        self.files.append({"name": name, "id": file_id, "data": data, "action": "upload",
                           "length": len(data), "metadata": metadata or {}})
        return file_id


class FictionalGoogle:
    authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"

    async def exchange(self, code, verifier, nonce_hash):
        return self.identity


@unittest.skipUnless(AVAILABLE, "Install the server extra for concurrent recovery tests")
class RecoveryAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://async.example.test",
            "DATABASE_PATH": str(Path(temporary.name) / "app.db"),
            "SESSION_SECRET": "fictional-async-session-secret-only-for-tests",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
        })
        self.provider = FictionalGoogle()
        self.provider.identity = Identity("fictional-new", "new@example.test", "New Fictional")
        self.app = create_app(settings, clock=lambda: self.now, google_provider=self.provider)
        await self.enterAsyncContext(self.app.router.lifespan_context(self.app.app))
        self.db = self.app.state.database
        self.auth = self.app.state.auth
        self.tokens = {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon"):
                connection.execute(
                    "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "fictional-" + owner, owner + "@example.test", owner, storage_time(self.now), storage_time(self.now)),
                )
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        self.csrf = {owner: self.auth.sessions.current(token).csrf for owner, token in self.tokens.items()}
        self.storage = DelayedStorage()
        self.authority = RecoveryAuthority(self.storage, b"f" * 32, "ctb-" + "a" * 32 + "/")
        self.authority.enroll(self.db)
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=settings.public_base_url, follow_redirects=False))
        self.probe = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=settings.public_base_url, follow_redirects=False))
        self.client.cookies.set(SESSION_COOKIE, self.tokens["ama"])
        self.probe.cookies.set(SESSION_COOKIE, self.tokens["joon"])

    async def assert_responsive(self, request, expected=303):
        """All three requests share one loop; no mocked scheduler or DB guard."""
        self.storage.delay = True
        task = asyncio.create_task(request)
        try:
            entered = await asyncio.wait_for(asyncio.to_thread(self.storage.entered.wait, 2), 3)
            self.assertTrue(entered, "Protected request never reached the independent authority")
            self.assertNotEqual(self.storage.write_thread, threading.get_ident())
            self.assertFalse(self.storage.timed_out)
            self.assertFalse(task.done(), "Protected request must await its authority write")
            # A different signed-in user attempts a normal SQLite write while
            # the protected transaction owns BEGIN IMMEDIATE. Fail promptly,
            # then prove that health still runs before the remote write ends.
            began = time.monotonic()
            busy = await asyncio.wait_for(self.probe.post("/auth/logout", data={"csrf": self.csrf["joon"]}), 1)
            self.assertEqual(busy.status_code, 503, busy.text)
            self.assertEqual(busy.headers["Retry-After"], "2")
            # A browser gets a readable page with the same fixed message.
            self.assertIn("The service is saving another change. Please try again shortly.", busy.text)
            self.assertIn("text/html", busy.headers["content-type"])
            health = await asyncio.wait_for(self.probe.get("/healthz"), 1)
            self.assertEqual(health.status_code, 200, health.text)
            self.assertLess(time.monotonic() - began, 1)
            self.assertFalse(task.done())
        finally:
            self.storage.release.set()
            response = await asyncio.wait_for(task, 3)
        self.assertEqual(response.status_code, expected, response.text)
        self.assertFalse(self.storage.timed_out)
        return response

    async def callback_request(self):
        response = await self.client.get("/auth/google/start")
        self.assertEqual(response.status_code, 303)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        return self.client.get("/auth/google/callback", params={"state": state, "code": "fictional-code"})

    def state(self):
        with self.db.connect() as connection:
            return local_state(connection)

    async def test_signup_commit_preserves_health_and_refuses_contending_session_write(self):
        await self.assert_responsive(await self.callback_request())
        with self.db.connect() as connection:
            user = connection.execute("SELECT id FROM users WHERE google_sub='fictional-new'").fetchone()
            self.assertIsNotNone(user)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user[0],)).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notification_events WHERE user_id=?", (user[0],)).fetchone()[0], 1)
        self.assertEqual(self.state()["generation"], 1)

    async def test_profile_update_commit_preserves_health(self):
        self.provider.identity = Identity("fictional-ama", "changed@example.test", "Changed Fictional")
        await self.assert_responsive(await self.callback_request())
        with self.db.connect() as connection:
            self.assertEqual(tuple(connection.execute("SELECT email,display_name FROM users WHERE id='ama'").fetchone()),
                             ("changed@example.test", "Changed Fictional"))
        self.assertEqual(self.state()["generation"], 1)

    async def test_signup_authority_failure_rolls_back_account_session_and_notification(self):
        self.storage.fail = True
        with self.assertLogs("connectthebots_server", level="ERROR") as logs:
            response = await self.assert_responsive(await self.callback_request(), expected=503)
        # A person on a phone sees a readable page, not raw JSON, and the
        # operator log names the fixed cause without any identity or request data.
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("nothing was saved", response.text)
        self.assertNotIn('{"error"', response.text)
        self.assertTrue(any("Protected change refused by recovery authority" in line for line in logs.output), logs.output)
        self.assertFalse(any("fictional" in line for line in logs.output), logs.output)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notification_events").fetchone()[0], 0)
        self.assertEqual(self.state()["generation"], 0)

    async def test_account_deletion_commit_preserves_health(self):
        await self.assert_responsive(self.client.post("/app/account/delete", data={"csrf": self.csrf["ama"], "confirmation": "DELETE"}), expected=200)
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT id FROM users WHERE id='ama'").fetchone())
        self.assertEqual(self.state()["generation"], 1)

    async def test_waitlist_removal_commit_preserves_health(self):
        removal = random_token()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO waitlist(id,email,consent_text,created_at,removal_hash) VALUES(?,?,?,?,?)",
                               (random_token(), "waiting@example.test", "Fictional consent", storage_time(self.now), token_hash(removal)))
        page = await self.client.get("/auth/waitlist/remove")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        await self.assert_responsive(self.client.post("/auth/waitlist/remove", data={"csrf": csrf, "removal": removal}), expected=200)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0], 0)
        self.assertEqual(self.state()["generation"], 1)

    def introduction(self):
        board = self.app.state.board
        postings = {}
        for owner in ("ama", "joon"):
            payload = {"text": "This is a fictional project. We offer a small prototype. We seek thoughtful testing.", "tags": ["prototypes"],
                       "looking_for": "Thoughtful testing", "offering": "A small prototype"}
            draft = board.save_draft(owner, payload)
            postings[owner] = board.publish(owner, draft["revision"], draft["payload_hash"])
        interactions = self.app.state.interactions
        preview = interactions.prepare_request("ama", postings["joon"]["slug"], "Could we test?", "Our prototypes overlap.")
        return interactions.request("ama", preview, random_token())

    async def test_introduction_block_commit_preserves_health(self):
        introduction = self.introduction()
        await self.assert_responsive(self.client.post("/app/introductions/" + introduction["id"] + "/block", data={"csrf": self.csrf["ama"]}))
        self.assertEqual(self.state()["generation"], 1)

    async def test_conversation_block_commit_preserves_health(self):
        introduction = self.introduction()
        conversation = self.app.state.interactions.accept("joon", introduction["id"], introduction["context_hash"])["conversation"]
        await self.assert_responsive(self.client.post("/app/conversations/" + conversation["id"] + "/block", data={"csrf": self.csrf["ama"]}))
        self.assertEqual(self.state()["generation"], 1)
