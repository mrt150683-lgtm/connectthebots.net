"""Configuration, schema integrity and the public HTTP security boundary."""

import asyncio
import base64
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from connectthebots_server.clock import timestamp
from connectthebots_server.config import ConfigurationError, Settings
from connectthebots_server.db import Database, Migration, MigrationError, load_migrations


NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
OPTIONAL_SERVER = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "uvicorn", "jwt", "jinja2"))
if OPTIONAL_SERVER:
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app


def environment(path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://service.example.test",
        "DATABASE_PATH": str(path), "SESSION_SECRET": "fictional-session-secret-32-characters",
    }


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.env = environment(Path.cwd() / "fictional.db")

    def test_closed_outbox_defaults_and_secret_repr(self):
        self.env.update(GOOGLE_CLIENT_SECRET="fictional-google-secret", SMTP_PASSWORD="fictional-smtp-password")
        settings = Settings.from_env(self.env)
        self.assertEqual((settings.signups, settings.email_mode), ("closed", "outbox"))
        for secret in (self.env["SESSION_SECRET"], self.env["GOOGLE_CLIENT_SECRET"], self.env["SMTP_PASSWORD"]):
            self.assertNotIn(secret, repr(settings))

    def test_environment_is_only_runtime_source(self):
        with patch.dict("os.environ", self.env, clear=True):
            self.assertEqual(Settings.from_env(), Settings.from_env(self.env))

    def test_backup_configuration_redacts_key_and_rejects_ambiguous_values(self):
        key = base64.urlsafe_b64encode(b"f" * 32).decode()
        settings = Settings.from_env({**self.env, "BACKUP_ENCRYPTION_KEY": key, "OPERATOR_EMAIL": "operator@example.test"})
        self.assertNotIn(key, repr(settings))
        self.assertEqual(settings.backup_directory, settings.database_path.parent / "backups")
        for name, value in (("BACKUP_ENCRYPTION_KEY", "fictional-invalid-secret"),
                            ("BACKUP_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"x" * 31).decode()),
                            ("BACKUP_DIRECTORY", "relative/backups"),
                            ("OPERATOR_EMAIL", "Name <operator@example.test>"),
                            ("OPERATOR_EMAIL", "operator@example.test,other@example.test")):
            with self.subTest(name=name), self.assertRaisesRegex(ConfigurationError, name) as caught:
                Settings.from_env({**self.env, name: value})
            self.assertNotIn(value, str(caught.exception))

    def test_missing_and_invalid_values_do_not_echo_secrets(self):
        cases = [
            ("SESSION_SECRET", "short-secret"), ("SESSION_SECRET", ""),
            ("SESSION_SECRET", "secret-with-newline\n" + "x" * 40),
            ("APP_ENV", "typo"), ("SIGNUPS", "yes"), ("EMAIL_MODE", "console"),
            ("DATABASE_PATH", "relative.db"), ("SMTP_PORT", "-1"), ("SMTP_PORT", "65536"),
            ("SMTP_PORT", "twenty"), ("SMTP_STARTTLS", "yes"),
            ("EMAIL_PROCESSING_NOTICE", "two\nlines"),
            ("EMAIL_PROCESSING_NOTICE", "x" * 4097),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ConfigurationError) as error:
                    Settings.from_env({**self.env, name: value})
                self.assertIn(name, str(error.exception))
                if "secret" in value:
                    self.assertNotIn(value, str(error.exception))

    def test_approved_offsite_configuration_requires_reviewed_backup_notice(self):
        offsite = {"BACKUP_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"f" * 32).decode(),
                   "OFFSITE_B2_KEY_ID": "fictional-key", "OFFSITE_B2_APPLICATION_KEY": "fictional-private-value",
                   "OFFSITE_B2_BUCKET_ID": "fictional-bucket", "OFFSITE_B2_PREFIX": "ctb-" + "a" * 32 + "/"}
        legal = {"LEGAL_PAGES_APPROVED": "true", "LEGAL_CONTROLLER_NAME": "Fictional Cooperative",
                 "CONTACT_EMAIL": "privacy@example.test", "VPS_PROVIDER_NAME": "Fictional host",
                 "EMAIL_PROVIDER_NAME": "Fictional sender", "LEGAL_TRANSFER_DETAILS": "Fictional reviewed details"}
        self.assertEqual(Settings.from_env(self.env | offsite).offsite_backup_notice, "")
        self.assertTrue(Settings.from_env(self.env | legal).legal_pages_approved)
        for notice in ("", " "):
            with self.subTest(notice=notice), self.assertRaisesRegex(ConfigurationError, "OFFSITE_BACKUP_NOTICE"):
                Settings.from_env(self.env | offsite | legal | {"OFFSITE_BACKUP_NOTICE": notice})
        notice = "Fictional reviewed provider, region, retention and transfer details."
        self.assertEqual(Settings.from_env(self.env | offsite | legal | {"OFFSITE_BACKUP_NOTICE": notice}).offsite_backup_notice, notice)

    def test_production_requires_https_and_staging_allows_local_proxy_http(self):
        with self.assertRaises(ConfigurationError):
            Settings.from_env({**self.env, "APP_ENV": "production", "PUBLIC_BASE_URL": "http://staging.example.test"})
        self.assertEqual(Settings.from_env({**self.env, "PUBLIC_BASE_URL": "http://staging.example.test"}).app_env, "staging")

    def test_origin_rejects_credentials_paths_fragments_and_ambiguous_hosts(self):
        for origin in ("//service.example.test", "javascript:alert(1)", "https://name:secret@service.example.test", "https://service.example.test/path", "https://service.example.test?code=secret", "https://service.example.test/#token", "https://bad host", "https://service.example.test:invalid", "https://service.example.test\\@evil.test", "https://service.example.test:0"):
            with self.subTest(origin=origin), self.assertRaises(ConfigurationError):
                Settings.from_env({**self.env, "PUBLIC_BASE_URL": origin})

    def test_development_and_staging_cannot_send_email(self):
        for mode in ("development", "staging"):
            with self.subTest(mode=mode), self.assertRaises(ConfigurationError):
                Settings.from_env({**self.env, "APP_ENV": mode, "EMAIL_MODE": "smtp"})

    def test_missing_smtp_launch_names_never_values(self):
        with self.assertRaises(ConfigurationError) as error:
            Settings.from_env({**self.env, "APP_ENV": "production", "EMAIL_MODE": "smtp"})
        self.assertIn("SMTP_PASSWORD", str(error.exception))
        self.assertIn("GOOGLE_CLIENT_ID", Settings.from_env(self.env).missing_launch_configuration())

    def test_utc_serialization_rejects_naive_dates(self):
        self.assertEqual(timestamp(datetime(2026, 10, 30, 13, tzinfo=timezone(timedelta(hours=1)))), "2026-10-30T12:00:00Z")
        with self.assertRaises(ValueError):
            timestamp(datetime(2026, 10, 30))

    def test_smtp_requires_verified_tls_and_single_sender(self):
        smtp = {**self.env, "APP_ENV": "production", "EMAIL_MODE": "smtp",
                "SMTP_HOST": "mail.example.test", "SMTP_USERNAME": "fictional-user",
                "SMTP_PASSWORD": "fictional-password", "SMTP_FROM_ADDRESS": "hello@example.test"}
        self.assertTrue(Settings.from_env(smtp).smtp_starttls)
        for name, value in (("SMTP_STARTTLS", "false"), ("SMTP_HOST", "https://mail.example.test"),
                            ("SMTP_FROM_ADDRESS", "Name <hello@example.test>"),
                            ("SMTP_FROM_ADDRESS", "hello@example.test,other@example.test"),
                            ("SMTP_FROM_ADDRESS", "invalid")):
            with self.subTest(name=name, value=value), self.assertRaisesRegex(ConfigurationError, name):
                Settings.from_env({**smtp, name: value})


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "app.db"
        self.db = Database(self.path, lambda: NOW)

    def test_migration_is_atomic_repeatable_and_uses_injected_utc(self):
        self.db.migrate()
        self.db.migrate()
        self.assertTrue(self.db.is_current())
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            rows = connection.execute("SELECT * FROM schema_migrations").fetchall()
            self.assertEqual(len(rows), len(load_migrations()))
            self.assertTrue(all(row["applied_at"] == "2026-10-30T12:00:00Z" for row in rows))

    def test_health_does_not_create_missing_database(self):
        self.assertFalse(self.db.is_current())
        self.assertFalse(self.path.exists())

    def test_changed_checksum_refuses_startup_and_health(self):
        self.db.migrate()
        with self.db.connect() as connection:
            connection.execute("UPDATE schema_migrations SET sha256 = ?", ("0" * 64,))
        self.assertFalse(self.db.is_current())
        with self.assertRaises(MigrationError):
            self.db.migrate()

    def test_future_schema_is_not_downgraded(self):
        self.db.migrate()
        with self.db.connect() as connection:
            connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", ("9999_future.sql", "0" * 64, timestamp(NOW)))
        self.assertFalse(self.db.is_current())
        with self.assertRaises(MigrationError):
            self.db.migrate()

    def test_missing_middle_or_renamed_migration_refuses_startup(self):
        self.db.migrate()
        with self.db.connect() as connection:
            connection.execute("UPDATE schema_migrations SET name = ? WHERE name = ?", ("0002_wrong.sql", load_migrations()[0].name))
        with self.assertRaises(MigrationError):
            self.db.migrate()

    def test_later_migration_failure_rolls_back_changes_and_history(self):
        self.db.migrate()
        broken = Migration(f"{len(load_migrations()) + 1:04d}_broken.sql", "CREATE TABLE fictional (value TEXT); INSERT INTO missing_table VALUES ('test');")
        changed = Database(self.path, lambda: NOW, migrations=(*load_migrations(), broken))
        with self.assertRaises(sqlite3.Error):
            changed.migrate()
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'fictional'").fetchone())
        self.assertTrue(self.db.is_current())

    def test_first_start_failure_leaves_no_partial_schema(self):
        broken = Migration(f"{len(load_migrations()) + 1:04d}_broken.sql", "CREATE TABLE fictional (value TEXT); INVALID SQL;")
        with self.assertRaises(sqlite3.Error):
            Database(self.path, lambda: NOW, migrations=(*load_migrations(), broken)).migrate()
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(), [])

    def test_migration_cannot_commit_early_or_attach_another_database(self):
        for statement in ("COMMIT;", "-- disguised\nEND;", "SAVEPOINT early;", "ATTACH DATABASE ':memory:' AS other;"):
            with self.subTest(statement=statement):
                bad = Migration(f"{len(load_migrations()) + 1:04d}_broken.sql", "CREATE TABLE fictional (value TEXT); " + statement)
                with self.assertRaises(sqlite3.Error):
                    Database(self.path, lambda: NOW, migrations=(*load_migrations(), bad)).migrate()
                with self.db.connect() as connection:
                    self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'fictional'").fetchone())

    def test_quoted_semicolons_work_without_partial_statement_acceptance(self):
        extra = Migration(f"{len(load_migrations()) + 1:04d}_example.sql", "CREATE TABLE fictional (value TEXT); INSERT INTO fictional VALUES ('one;two'); -- end comment\n")
        updated = Database(self.path, lambda: NOW, migrations=(*load_migrations(), extra))
        updated.migrate()
        with updated.connect() as connection:
            self.assertEqual(connection.execute("SELECT value FROM fictional").fetchone()[0], "one;two")
        with self.assertRaises(MigrationError):
            Database(self.path, lambda: NOW, migrations=(*load_migrations(), extra, Migration(f"{len(load_migrations()) + 2:04d}_bad.sql", "CREATE TABLE bad (value TEXT)"))).migrate()

    def test_unknown_database_refused_without_overwriting_tables(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE existing (value TEXT)")
            connection.execute("INSERT INTO existing VALUES (?)", ("fictional existing content",))
            connection.commit()
        with self.assertRaises(MigrationError):
            self.db.migrate()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM existing").fetchone()[0], "fictional existing content")

    def test_missing_or_out_of_sequence_packaged_migrations_refused(self):
        for migrations in ((), (Migration("0002_gap.sql", "SELECT 1;"),), (Migration("0001_weird-name.sql", "SELECT 1;"),)):
            with self.subTest(migrations=migrations), self.assertRaises(MigrationError):
                Database(self.path, lambda: NOW, migrations=migrations)

    def test_transaction_rolls_back_failed_effect(self):
        self.db.migrate()
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as connection:
                connection.execute("CREATE TABLE uncommitted (value TEXT)")
                raise RuntimeError("fictional failure")
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'uncommitted'").fetchone())


@unittest.skipUnless(OPTIONAL_SERVER, "Install the optional server extra for HTTP tests")
class HttpFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings.from_env(environment(Path(self.temp.name) / "app.db"))
        self.app = create_app(self.settings, lambda: NOW)

    def client(self, **kwargs):
        return TestClient(self.app, base_url=self.settings.public_base_url, **kwargs)

    def test_factory_has_no_database_side_effect_and_lifespan_migrates(self):
        self.assertFalse(self.settings.database_path.exists())
        with self.client() as client:
            response = client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertNotIn(self.settings.session_secret, response.text)

    def test_health_checks_live_schema_and_does_not_repair_it(self):
        with self.client() as client:
            with self.app.state.database.connect() as connection:
                connection.execute("DELETE FROM schema_migrations")
            response = client.get("/healthz")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"status": "unavailable"})
            with self.app.state.database.connect() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 0)

    def test_health_never_returns_database_errors(self):
        with self.client() as client, patch.object(self.app.state.database, "connect", side_effect=sqlite3.OperationalError("secret database path and content")):
            response = client.get("/healthz")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret", response.text)

    def test_no_admin_api_docs_or_fake_login_routes(self):
        with self.client() as client:
            for path in ("/admin", "/docs", "/openapi.json", "/api/v1/unknown"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.get("/auth/google/start").status_code, 503)
            for path in ("/signin", "/app"):
                response = client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertIn("Accounts are not open yet", response.text)
                self.assertNotIn("Continue with Google", response.text)
            self.assertEqual(client.get("/app/account").status_code, 503)
            self.assertEqual(client.get("/api/v1/me").status_code, 503)
            self.assertEqual(client.post("/api/v1/reports", json={}).status_code, 503)
            for path in ("/board", "/board.json", "/board/" + "a" * 32, "/board/" + "a" * 32 + ".json"):
                self.assertEqual(client.get(path).status_code, 503)

    def test_security_headers_cover_success_errors_and_unavailable_pages(self):
        self.app = create_app(replace(self.settings, app_env="production"), lambda: NOW)
        with self.client() as client:
            for path in ("/healthz", "/missing", "/app"):
                with self.subTest(path=path):
                    headers = client.get(path).headers
                    self.assertEqual(headers["cache-control"], "no-store")
                    self.assertEqual(headers["x-content-type-options"], "nosniff")
                    self.assertIn("default-src 'self'", headers["content-security-policy"])
                    self.assertIn("form-action 'self'", headers["content-security-policy"])
                    self.assertIn("max-age=31536000", headers["strict-transport-security"])

    def test_cross_site_writes_and_null_origin_refused(self):
        with self.client() as client:
            for origin in ("https://evil.example.test", "null", self.settings.public_base_url + ".evil.test"):
                with self.subTest(origin=origin):
                    self.assertEqual(client.post("/api/v1/reports", headers={"Origin": origin}, json={}).status_code, 403)
            self.assertEqual(client.post("/api/v1/reports", headers={"Origin": self.settings.public_base_url}, json={}).status_code, 503)

    def test_untrusted_host_refused_but_local_health_probe_works(self):
        with self.client() as client:
            self.assertEqual(client.get("/healthz", headers={"Host": "evil.example.test"}).status_code, 400)
            self.assertEqual(client.get("/healthz", headers={"Host": "127.0.0.1:8100"}).status_code, 200)

    def test_declared_body_limits_and_unsupported_encodings(self):
        with self.client() as client:
            self.assertEqual(client.post("/api/v1/reports", content=b"a" * (32 * 1024 + 1)).status_code, 413)
            self.assertEqual(client.post("/app", content=b"a" * (64 * 1024 + 1)).status_code, 413)
            self.assertEqual(client.post("/api/v1/reports", content=b"a", headers={"Content-Encoding": "gzip"}).status_code, 415)
            self.assertEqual(client.post("/api/v1/reports", content=b"a", headers={"Content-Length": "bad"}).status_code, 400)
            self.assertEqual(client.post("/api/v1/reports", content=b"a", headers={"Content-Length": "9" * 5000}).status_code, 413)

    def test_chunked_body_bound_checked_before_any_route_effect(self):
        effects = []

        async def effect(request):
            effects.append(True)
            return JSONResponse({"saved": True})

        self.app.router.routes.append(Route("/api/probe", effect, methods=["POST"]))

        async def run(chunks):
            messages = [{"type": "http.request", "body": chunk, "more_body": index != len(chunks) - 1} for index, chunk in enumerate(chunks)]
            sent = []

            async def receive():
                return messages.pop(0) if messages else {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            await self.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "https", "method": "POST", "path": "/api/probe", "raw_path": b"/api/probe", "query_string": b"", "root_path": "", "headers": [(b"host", b"service.example.test")], "client": ("127.0.0.1", 1234), "server": ("service.example.test", 443)}, receive, send)
            return sent[0]["status"]

        self.assertEqual(asyncio.run(run([b"a" * 16384, b"b" * 16385])), 413)
        self.assertEqual(effects, [])
        self.assertEqual(asyncio.run(run([b"a" * 16384, b"b" * 16384])), 200)
        self.assertEqual(effects, [True])

    def test_unhandled_exception_returns_generic_error_and_logs_no_content(self):
        async def fail(request):
            raise RuntimeError("fictional-private-message-and-token")

        self.app.router.routes.append(Route("/failure", fail))
        with self.client() as client, self.assertLogs("connectthebots_server", level="ERROR") as logs:
            response = client.get("/failure", headers={"Accept": "application/json"})
            page = client.get("/failure")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "Service temporarily unavailable"})
        # People in a browser see a page with a fixed message, never exception content.
        self.assertEqual(page.status_code, 500)
        self.assertIn("Something went wrong on our side", page.text)
        self.assertNotIn("fictional-private", page.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("fictional-private", response.text + " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
