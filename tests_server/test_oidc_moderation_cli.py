"""Real browser-fixture CLI round trip for valid IDs beginning with a hyphen."""
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SERVER = all(importlib.util.find_spec(name) for name in
             ("starlette", "httpx", "uvicorn", "jwt", "jinja2", "cryptography"))
if SERVER:
    from connectthebots_server.app import create_app
    from connectthebots_server.clock import utc_now
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import storage_time, valid_token
    from tests_server.oidc_fixture import moderation_cli


@unittest.skipUnless(SERVER, "optional server extra required for browser-fixture CLI")
class ModerationCliHyphenIdTests(unittest.TestCase):
    def test_show_suspend_unsuspend_delete_target_hyphen_id_without_leaking_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings.from_env({
                "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://fictional.example.test",
                "DATABASE_PATH": str(Path(temporary) / "moderation.db"),
                "SESSION_SECRET": "fictional-moderation-cli-" + "x" * 40,
                "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open", "EMAIL_MODE": "outbox",
                "GOOGLE_CLIENT_ID": "fictional-browser-client", "GOOGLE_CLIENT_SECRET": "fictional-browser-secret",
            })
            app = create_app(settings, clock=utc_now)
            database = app.state.database
            database.migrate()
            owner, other = "-" + "a" * 42, "b" * 43
            self.assertTrue(valid_token(owner))
            now = utc_now()
            with database.transaction() as connection:
                for user, person in ((owner, "ama"), (other, "joon")):
                    connection.execute(
                        "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                        (user, "fixture-" + person, person + "@fictional.example.test", "Private fictional " + person,
                         storage_time(now - timedelta(days=30)), storage_time(now)))
                session = app.state.auth.sessions.create(connection, owner)
                app.state.auth.sessions.create(connection, other)
            key = app.state.reports.create_key(owner, "Fictional target key")["key"]
            app.state.reports.create_key(other, "Fictional unaffected key")

            def invoke(operation):
                response = moderation_cli(app, {"operation": operation, "person": "ama"})
                self.assertEqual(response["exit_code"], 0, response["error"])
                self.assertEqual(response["error"], "")
                encoded = json.dumps(response["result"])
                for private in (session, key, settings.session_secret, "fixture-ama", "key_hash", "token_hash"):
                    self.assertNotIn(private, encoded)
                return response["result"]

            shown = invoke("show")
            self.assertEqual(shown["id"], owner)
            self.assertIsNone(shown["suspended_at"])
            self.assertEqual((shown["active_sessions"], shown["active_keys"]), (1, 1))
            self.assertEqual(invoke("suspend"), {"id": owner, "suspended": True})
            with database.connect() as connection:
                self.assertIsNotNone(connection.execute("SELECT suspended_at FROM users WHERE id=?", (owner,)).fetchone()[0])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (owner,)).fetchone()[0], 0)
                self.assertIsNotNone(connection.execute("SELECT revoked_at FROM report_keys WHERE user_id=?", (owner,)).fetchone()[0])
                self.assertEqual(connection.execute("SELECT paused FROM report_settings WHERE user_id=?", (owner,)).fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (other,)).fetchone()[0], 1)
                self.assertIsNone(connection.execute("SELECT revoked_at FROM report_keys WHERE user_id=?", (other,)).fetchone()[0])
            self.assertEqual(invoke("unsuspend"), {"id": owner, "suspended": False})
            shown = invoke("show")
            self.assertIsNone(shown["suspended_at"])
            self.assertEqual((shown["active_sessions"], shown["active_keys"]), (0, 0))
            self.assertEqual(invoke("delete"), {"deleted": True})
            with database.connect() as connection:
                self.assertIsNone(connection.execute("SELECT id FROM users WHERE id=?", (owner,)).fetchone())
                for table in ("sessions", "report_keys", "report_settings", "notification_preferences"):
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM " + table + " WHERE user_id=?", (owner,)).fetchone()[0], 0)
                receipt = dict(connection.execute("SELECT * FROM deletion_receipts").fetchone())
                self.assertEqual(set(receipt), {"deleted_on"})
                self.assertEqual(connection.execute("SELECT erasure_generation FROM recovery_state").fetchone()[0], 1)
                self.assertIsNotNone(connection.execute("SELECT id FROM users WHERE id=?", (other,)).fetchone())
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (other,)).fetchone()[0], 1)
