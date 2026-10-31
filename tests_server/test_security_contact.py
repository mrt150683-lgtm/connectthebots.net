"""Public vulnerability contact: explicit review, safe URI and fixed expiry."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from connectthebots_server.config import ConfigurationError, Settings


NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
EXPIRES = "2027-04-30T12:00:00Z"
ORIGIN = "https://service.example.test"
PATH = "/.well-known/security.txt"
OPTIONAL_SERVER = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "uvicorn", "jwt", "jinja2"))
if OPTIONAL_SERVER:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app


def environment(database):
    return {"APP_ENV": "staging", "PUBLIC_BASE_URL": ORIGIN,
            "DATABASE_PATH": str(database), "SESSION_SECRET": "fictional-" + "session-" + "s" * 32,
            "CONTACT_EMAIL": "security@example.test", "SECURITY_CONTACT_EXPIRES": EXPIRES}


class SecurityContactConfigurationTests(unittest.TestCase):
    def test_empty_is_disabled_and_explicit_utc_value_is_parsed_without_wall_clock(self):
        env = environment(Path.cwd() / "fictional.db")
        self.assertIsNone(Settings.from_env(env | {"SECURITY_CONTACT_EXPIRES": ""}).security_contact_expires)
        env.pop("SECURITY_CONTACT_EXPIRES")
        self.assertIsNone(Settings.from_env(env).security_contact_expires)
        self.assertEqual(Settings.from_env(env | {"SECURITY_CONTACT_EXPIRES": EXPIRES}).security_contact_expires,
                         datetime(2027, 4, 30, 12, tzinfo=timezone.utc))
        # Stale contact metadata must not prevent unrelated application startup.
        self.assertEqual(Settings.from_env(env | {"SECURITY_CONTACT_EXPIRES": "2020-01-01T00:00:00Z"}).security_contact_expires.year, 2020)

    def test_invalid_or_ambiguous_expiry_is_refused_without_echo(self):
        env = environment(Path.cwd() / "fictional.db")
        for value in ("2027-04-30", "2027-04-30T12:00:00", "2027-04-30T12:00:00+00:00",
                      "2027-02-30T12:00:00Z", "2027-04-30T24:00:00Z", "0000-04-30T12:00:00Z",
                      "2027-4-30T12:00:00Z", "2027-04-30T12:00:00Z\nContact: attacker", "fictional-sensitive-value"):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigurationError, "SECURITY_CONTACT_EXPIRES") as caught:
                Settings.from_env(env | {"SECURITY_CONTACT_EXPIRES": value})
            self.assertNotIn(value, str(caught.exception))


@unittest.skipUnless(OPTIONAL_SERVER, "optional server dependencies are not installed")
class SecurityContactHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "uncreated" / "fictional.db"
        self.settings = Settings.from_env(environment(self.path))

    def client(self, settings=None, now=NOW, origin=ORIGIN):
        # Do not enter lifespan: this public metadata must not need a database.
        client = TestClient(create_app(settings or self.settings, clock=lambda: now), base_url=origin)
        self.addCleanup(client.close)
        return client

    def test_exact_unsigned_plain_text_is_public_without_database_cookie_or_secret(self):
        response = self.client().get(PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(response.text, "Contact: mailto:security@example.test\n"
                         "Expires: 2027-04-30T12:00:00Z\n"
                         "Canonical: https://service.example.test/.well-known/security.txt\n"
                         "Preferred-Languages: en\n")
        self.assertNotIn("set-cookie", response.headers)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn(self.settings.session_secret, response.text)
        self.assertFalse(self.path.parent.exists())

    def test_contact_mailto_encodes_reserved_characters_without_query_or_fragment(self):
        mailbox = "security+test?subject=injection#fragment%&tag@example.test"
        response = self.client(replace(self.settings, contact_email=mailbox)).get(PATH)
        self.assertEqual(response.status_code, 200)
        contact = urlsplit(response.text.splitlines()[0].removeprefix("Contact: "))
        self.assertEqual(contact.scheme, "mailto")
        self.assertEqual(unquote(contact.path), mailbox)
        self.assertEqual((contact.query, contact.fragment), ("", ""))

    def test_missing_or_ambiguous_contact_is_not_published_or_echoed(self):
        for mailbox in ("", "Name <security@example.test>", "a@example.test,b@example.test",
                        "security@example.test\nContact: mailto:other@example.test",
                        "security@example.test?subject=attack", ".security@example.test",
                        "security..team@example.test", "sécurité@example.test", "x" * 255 + "@example.test"):
            with self.subTest(mailbox=mailbox):
                response = self.client(replace(self.settings, contact_email=mailbox)).get(PATH)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.text, "Security contact information is not currently available.\n")
                self.assertNotIn("Contact:", response.text)
                self.assertIn("no-store", response.headers["cache-control"])

    def test_expiry_is_fixed_across_requests_and_restarts_and_stale_at_exact_boundary(self):
        initial = self.client().get(PATH)
        later = self.client(now=NOW + timedelta(days=2)).get(PATH)
        self.assertEqual(initial.content, later.content)
        expiry = self.settings.security_contact_expires
        self.assertEqual(self.client(now=expiry - timedelta(seconds=1)).get(PATH).status_code, 200)
        self.assertEqual(self.client(now=expiry).get(PATH).status_code, 503)
        self.assertEqual(self.client(now=expiry + timedelta(days=1)).get(PATH).status_code, 503)
        renewed = replace(self.settings, security_contact_expires=expiry + timedelta(days=180))
        response = self.client(renewed, now=expiry).get(PATH)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(initial.content, response.content)

    def test_missing_naive_or_more_than_one_year_expiry_is_unavailable(self):
        for expiry in (None, NOW.replace(tzinfo=None), NOW + timedelta(days=366)):
            with self.subTest(expiry=expiry):
                self.assertEqual(self.client(replace(self.settings, security_contact_expires=expiry)).get(PATH).status_code, 503)
        self.assertEqual(self.client(replace(self.settings, security_contact_expires=NOW + timedelta(days=365))).get(PATH).status_code, 200)

    def test_only_https_can_publish_and_forwarded_headers_cannot_change_canonical(self):
        insecure = replace(self.settings, public_base_url="http://service.example.test")
        self.assertEqual(self.client(insecure, origin=insecure.public_base_url).get(PATH).status_code, 503)
        self.assertEqual(self.client(origin="http://service.example.test").get(PATH, headers={"X-Forwarded-Proto": "https"}).status_code, 503)
        response = self.client().get(PATH, headers={"Forwarded": "host=attacker.example;proto=http",
                                                   "X-Forwarded-Host": "attacker.example",
                                                   "X-Forwarded-Proto": "http"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"Canonical: {ORIGIN}{PATH}\n", response.text)
        self.assertNotIn("attacker", response.text)
        self.assertEqual(self.client().get(PATH, headers={"Host": "attacker.example"}).status_code, 400)

    def test_head_is_bodyless_writes_refused_and_unrelated_paths_not_routed(self):
        client = self.client()
        get = client.get(PATH)
        head = client.head(PATH)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["content-length"], get.headers["content-length"])
        for method in ("post", "put", "delete"):
            self.assertEqual(getattr(client, method)(PATH, headers={"Origin": ORIGIN}).status_code, 405)
        for path in ("/security.txt", "/.well-known/other.txt", "/.well-known/security.txt/more"):
            self.assertEqual(client.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
