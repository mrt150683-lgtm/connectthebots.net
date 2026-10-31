"""Account tests use fictional identities and signed JWTs from a local test key."""

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
from html import unescape
from http.cookies import SimpleCookie
import json
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from connectthebots_server.config import ConfigurationError, Settings
from connectthebots_server.db import Database


AUTH_EXTRA = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "uvicorn", "jwt", "jinja2", "cryptography"))
if AUTH_EXTRA:
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from starlette.testclient import TestClient
    from starlette.requests import Request
    from connectthebots_server.app import create_app
    from connectthebots_server.auth import AuthenticationError, GoogleProvider, OAUTH_COOKIE, GOOGLE_JWKS_URL, GOOGLE_TOKEN_URL
    from connectthebots_server.sessions import PREAUTH_COOKIE, SESSION_COOKIE, Sessions, random_token, storage_time, token_hash


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def settings(path: Path):
    return Settings.from_env({
        "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://service.example.test", "DATABASE_PATH": str(path),
        "SESSION_SECRET": "fictional-session-secret-only-for-tests",
        "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        "GOOGLE_CLIENT_ID": "fictional-client.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "fictional-client-secret",
    })


@unittest.skipUnless(AUTH_EXTRA, "Install the server extra for Google account tests")
class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if AUTH_EXTRA:
            cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.settings = settings(Path(self.temp.name) / "app.db")
        self.claim_changes = {}
        self.nonce = ""
        self.kid = "fictional-key-1"
        self.signing_key = self.key
        self.calls = []
        self.cache_control = "public, max-age=3600"
        self.jwks_keys = [self.jwk(self.key, "fictional-key-1")]
        self.provider = GoogleProvider(self.settings, self.clock, transport=httpx.MockTransport(self.respond))
        self.app = create_app(self.settings, self.clock, google_provider=self.provider)

    def jwk(self, key, kid):
        result = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        result.update(kid=kid, use="sig", alg="RS256")
        return result

    def claims(self):
        return {"iss": "https://accounts.google.com", "aud": self.settings.google_client_id,
                "sub": "fictional-ama", "email": "ama@example.test", "email_verified": True,
                "name": "Ama Fictional", "nonce": self.nonce,
                "iat": int(self.clock().timestamp()), "exp": int(self.clock().timestamp()) + 3600,
                **self.claim_changes}

    def respond(self, request):
        self.calls.append(request)
        if str(request.url) == GOOGLE_JWKS_URL:
            return httpx.Response(200, json={"keys": self.jwks_keys}, headers={"Cache-Control": self.cache_control})
        if str(request.url) == GOOGLE_TOKEN_URL:
            return httpx.Response(200, json={"id_token": jwt.encode(self.claims(), self.signing_key, algorithm="RS256", headers={"kid": self.kid}), "access_token": "fictional-access-never-store", "refresh_token": "fictional-refresh-never-store"})
        raise AssertionError("Unexpected external URL")

    def client(self):
        return TestClient(self.app, base_url=self.settings.public_base_url, follow_redirects=False)

    def begin(self, client, *, invite=None):
        if invite is not None:
            page = client.get("/signin")
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            response = client.post("/auth/google/start", data={"csrf": csrf, "invite": invite})
        else:
            response = client.get("/auth/google/start")
        self.assertEqual(response.status_code, 200 if invite is not None else 303, response.text)
        location = unescape(re.search(r'class="google-btn" href="([^"]+)"', response.text).group(1)) if invite is not None else response.headers["location"]
        parameters = {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}
        self.nonce = parameters["nonce"]
        return parameters

    def finish(self, client, parameters):
        return client.get("/auth/google/callback", params={"state": parameters["state"], "code": "fictional-code"})

    def login(self, client):
        response = self.finish(client, self.begin(client))
        self.assertEqual(response.status_code, 303, response.text)
        return client.cookies[SESSION_COOKIE]

    def count(self, table):
        # Table names are fixed by this test, never input to the service.
        with self.app.state.database.connect() as connection:
            return connection.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def test_complete_code_pkce_flow_stores_only_hashes_and_approved_profile_fields(self):
        with self.client() as client:
            params = self.begin(client)
            self.assertEqual(params["scope"], "openid email profile")
            self.assertEqual(params["response_type"], "code")
            self.assertEqual(params["code_challenge_method"], "S256")
            self.assertEqual(params["redirect_uri"], self.settings.public_base_url + "/auth/google/callback")
            binding = client.cookies[OAUTH_COOKIE]
            with self.app.state.database.connect() as connection:
                attempt = dict(connection.execute("SELECT * FROM oauth_attempts").fetchone())
            self.assertEqual(attempt["state_hash"], token_hash(params["state"]))
            self.assertEqual(attempt["binding_hash"], token_hash(binding))
            self.assertEqual(attempt["nonce_hash"], token_hash(params["nonce"]))
            self.assertEqual(params["code_challenge"], base64.urlsafe_b64encode(sha256(attempt["verifier"].encode()).digest()).decode().rstrip("="))
            response = self.finish(client, params)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            token = client.cookies[SESSION_COOKIE]
            cookie_header = response.headers.get_list("set-cookie")[0]
            self.assertIn("Secure", cookie_header)
            self.assertIn("HttpOnly", cookie_header)
            self.assertIn("SameSite=lax", cookie_header)
            self.assertIn("Path=/", cookie_header)
            self.assertNotIn("Domain=", cookie_header)
            with self.app.state.database.connect() as connection:
                session = dict(connection.execute("SELECT * FROM sessions").fetchone())
                account = dict(connection.execute("SELECT * FROM users").fetchone())
                dump = "\n".join(connection.iterdump())
            self.assertEqual(session["token_hash"], token_hash(token))
            self.assertEqual(account["google_sub"], "fictional-ama")
            for secret in (token, binding, params["state"], params["nonce"], attempt["verifier"], "fictional-access-never-store", "fictional-refresh-never-store"):
                self.assertNotIn(secret, dump)
            token_request = next(request for request in self.calls if str(request.url) == GOOGLE_TOKEN_URL)
            body = parse_qs(token_request.content.decode())
            self.assertEqual(body["code_verifier"], [attempt["verifier"]])
            self.assertEqual(self.count("email_outbox"), 1)
            self.assertIn("Your account is ready", client.get("/app").text)
            self.assertEqual(client.get("/app").headers["referrer-policy"], "same-origin")

    def test_tampered_state_is_rejected_before_google_call(self):
        with self.client() as client:
            params = self.begin(client)
            params["state"] = random_token()
            self.assertEqual(self.finish(client, params).status_code, 400)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.count("users"), 0)

    def test_reused_state_is_rejected_and_does_not_duplicate_account(self):
        with self.client() as client:
            params = self.begin(client)
            self.assertEqual(self.finish(client, params).status_code, 303)
            self.assertEqual(self.finish(client, params).status_code, 400)
            self.assertEqual(self.count("users"), 1)
            self.assertEqual(sum(str(r.url) == GOOGLE_TOKEN_URL for r in self.calls), 1)

    def test_wrong_browser_cookie_cannot_consume_another_browser_attempt(self):
        with self.client() as first, self.client() as second:
            first_params = self.begin(first)
            first_nonce = self.nonce
            self.begin(second)
            self.assertEqual(self.finish(second, first_params).status_code, 400)
            self.assertEqual(self.calls, [])
            self.nonce = first_nonce
            self.assertEqual(self.finish(first, first_params).status_code, 303)

    def test_expired_state_rejected_at_exact_ten_minute_boundary(self):
        with self.client() as client:
            params = self.begin(client)
            self.clock.advance(minutes=10)
            self.assertEqual(self.finish(client, params).status_code, 400)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.count("oauth_attempts"), 0)

    def refused_claim(self, **changes):
        self.claim_changes.update(changes)
        with self.client() as client:
            self.assertEqual(self.finish(client, self.begin(client)).status_code, 400)
            self.assertEqual(self.count("users"), 0)
            self.assertEqual(self.count("sessions"), 0)
            self.assertEqual(self.count("oauth_attempts"), 0)

    def test_wrong_nonce_is_rejected(self):
        self.refused_claim(nonce="different-nonce")

    def test_expired_token_is_rejected_using_injected_clock(self):
        self.refused_claim(exp=int(self.clock().timestamp()))

    def test_wrong_audience_is_rejected(self):
        self.refused_claim(aud="another-client.apps.googleusercontent.com")

    def test_unverified_email_is_rejected(self):
        self.refused_claim(email_verified=False)

    def test_string_true_email_verification_is_not_boolean_true(self):
        self.refused_claim(email_verified="true")

    def test_wrong_issuer_is_rejected(self):
        self.refused_claim(iss="https://evil.example.test")

    def test_future_issued_token_is_rejected(self):
        self.refused_claim(iat=int(self.clock().timestamp()) + 61)

    def test_wrong_authorized_party_is_rejected(self):
        self.refused_claim(azp="another-client")

    def test_wrong_signature_is_rejected(self):
        self.signing_key = self.other_key
        self.refused_claim()

    def test_algorithm_confusion_is_rejected_without_loading_keys(self):
        self.nonce = random_token()
        token = jwt.encode(self.claims(), "fictional-hmac-secret-with-many-characters", algorithm="HS256", headers={"kid": self.kid})
        with self.assertRaises(AuthenticationError):
            asyncio.run(self.provider.verify(token, token_hash(self.nonce)))
        self.assertEqual(self.calls, [])

    def test_jwks_cache_obeys_max_age_and_refreshes_unknown_key(self):
        self.nonce = random_token()
        token = jwt.encode(self.claims(), self.key, algorithm="RS256", headers={"kid": self.kid})
        asyncio.run(self.provider.verify(token, token_hash(self.nonce)))
        asyncio.run(self.provider.verify(token, token_hash(self.nonce)))
        self.assertEqual(len(self.calls), 1)
        self.jwks_keys = [self.jwk(self.other_key, "fictional-key-2")]
        second = jwt.encode(self.claims(), self.other_key, algorithm="RS256", headers={"kid": "fictional-key-2"})
        asyncio.run(self.provider.verify(second, token_hash(self.nonce)))
        self.assertEqual(len(self.calls), 2)
        self.clock.advance(hours=1)
        second = jwt.encode(self.claims(), self.other_key, algorithm="RS256", headers={"kid": "fictional-key-2"})
        asyncio.run(self.provider.verify(second, token_hash(self.nonce)))
        self.assertEqual(len(self.calls), 3)

    def test_no_store_jwks_is_not_reused(self):
        self.cache_control = "no-store, max-age=3600"
        self.nonce = random_token()
        token = jwt.encode(self.claims(), self.key, algorithm="RS256", headers={"kid": self.kid})
        asyncio.run(self.provider.verify(token, token_hash(self.nonce)))
        asyncio.run(self.provider.verify(token, token_hash(self.nonce)))
        self.assertEqual(len(self.calls), 2)

    def test_unknown_kid_remains_rejected_after_one_refresh(self):
        self.kid = "unknown-key"
        self.refused_claim()
        self.assertEqual(sum(str(r.url) == GOOGLE_JWKS_URL for r in self.calls), 1)

    def test_google_error_response_is_not_echoed_or_logged(self):
        self.provider.transport = httpx.MockTransport(lambda request: httpx.Response(500, text="private-provider-secret"))
        with self.client() as client:
            response = self.finish(client, self.begin(client))
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("private-provider-secret", response.text)

    def test_google_redirect_is_refused_without_following(self):
        self.provider.transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://evil.example.test"}))
        with self.client() as client:
            self.assertEqual(self.finish(client, self.begin(client)).status_code, 400)

    def test_identity_uses_sub_instead_of_email(self):
        with self.client() as client:
            self.login(client)
            self.claim_changes["sub"] = "fictional-joon"
            self.login(client)
            self.assertEqual(self.count("users"), 2)

    def test_same_sub_updates_email_and_rotates_session_without_duplicate_security_event(self):
        with self.client() as client:
            old = self.login(client)
            self.claim_changes["email"] = "ama-updated@example.test"
            new = self.login(client)
            self.assertNotEqual(old, new)
            self.assertIsNone(self.app.state.auth.sessions.current(old))
            self.assertEqual(self.app.state.auth.sessions.current(new).email, "ama-updated@example.test")
            self.assertEqual(self.count("email_outbox"), 1)

    def test_logout_requires_own_csrf_and_revokes_stolen_copy(self):
        with self.client() as first, self.client() as second:
            stolen = self.login(first)
            self.claim_changes.update(sub="fictional-joon", email="joon@example.test", name="Joon Fictional")
            self.login(second)
            first_csrf = self.app.state.auth.sessions.current(stolen).csrf
            self.assertEqual(second.post("/auth/logout", data={"csrf": first_csrf}).status_code, 403)
            self.assertEqual(first.post("/auth/logout", data={"csrf": first_csrf}, headers={"Origin": "https://evil.example.test"}).status_code, 403)
            self.assertEqual(first.post("/auth/logout", data={"csrf": first_csrf}).status_code, 303)
            self.assertIsNone(self.app.state.auth.sessions.current(stolen))
            self.assertEqual(first.get("/app").status_code, 303)

    def test_session_sliding_and_absolute_expiry_boundaries(self):
        with self.client() as client:
            token = self.login(client)
            self.clock.advance(days=29)
            self.assertIsNotNone(self.app.state.auth.sessions.current(token))
            self.clock.advance(days=29)
            self.assertIsNotNone(self.app.state.auth.sessions.current(token))
            self.clock.advance(days=29)
            self.assertIsNotNone(self.app.state.auth.sessions.current(token))
            self.clock.advance(days=3)
            self.assertIsNone(self.app.state.auth.sessions.current(token))

    def test_session_inactive_for_thirty_days_expires(self):
        with self.client() as client:
            token = self.login(client)
            self.clock.advance(days=30)
            self.assertIsNone(self.app.state.auth.sessions.current(token))

    def test_suspended_user_cannot_reuse_session_or_sign_in(self):
        with self.client() as client:
            token = self.login(client)
            with self.app.state.database.transaction() as connection:
                connection.execute("UPDATE users SET suspended_at = ?", (storage_time(self.clock()),))
            self.assertIsNone(self.app.state.auth.sessions.current(token))
            self.assertEqual(self.finish(client, self.begin(client)).status_code, 400)

    def test_account_view_ignores_other_user_identifier_and_escapes_profile_name(self):
        self.claim_changes["name"] = "<script>fictional</script> [link](javascript:bad)"
        with self.client() as client:
            self.login(client)
            response = client.get("/app?user_id=another-user")
            self.assertIn("&lt;script&gt;", response.text)
            self.assertNotIn("<script>", response.text)
            self.assertNotIn('href="javascript:', response.text)

    def test_invite_is_required_and_consumed_once_in_account_transaction(self):
        self.settings = replace(self.settings, signups="invite")
        self.app = create_app(self.settings, self.clock, google_provider=self.provider)
        with self.client() as client:
            self.assertEqual(self.finish(client, self.begin(client)).status_code, 400)
            invitation = random_token()
            with self.app.state.database.transaction() as connection:
                connection.execute("INSERT INTO invites (code_hash, created_at, expires_at) VALUES (?, ?, ?)", (token_hash(invitation), storage_time(self.clock()), storage_time(self.clock() + timedelta(days=1))))
            self.assertEqual(self.finish(client, self.begin(client, invite=invitation)).status_code, 303)
            self.claim_changes["sub"] = "fictional-joon"
            self.assertEqual(self.finish(client, self.begin(client, invite=invitation)).status_code, 400)
            self.assertEqual(self.count("users"), 1)

    def test_closed_mode_refuses_new_user_but_allows_existing_user(self):
        with self.client() as client:
            self.login(client)
        self.settings = replace(self.settings, signups="closed")
        self.app = create_app(self.settings, self.clock, google_provider=self.provider)
        with self.client() as client:
            self.login(client)
            self.claim_changes["sub"] = "fictional-joon"
            self.assertEqual(self.finish(client, self.begin(client)).status_code, 400)
            self.assertEqual(self.count("users"), 1)

    def test_waitlist_requires_consent_and_removal_cannot_be_hijacked_by_duplicate(self):
        self.settings = replace(self.settings, signups="closed")
        self.app = create_app(self.settings, self.clock, google_provider=self.provider)
        with self.client() as client:
            page = client.get("/signin")
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            self.assertEqual(client.post("/auth/waitlist", data={"csrf": csrf, "email": "ama@example.test"}).status_code, 422)
            first = client.post("/auth/waitlist", data={"csrf": csrf, "email": "ama@example.test", "consent": "yes"})
            first_code = re.search(r'id="removal" value="([^"]+)"', first.text).group(1)
            second = client.post("/auth/waitlist", data={"csrf": csrf, "email": "ama@example.test", "consent": "yes"})
            second_code = re.search(r'id="removal" value="([^"]+)"', second.text).group(1)
            self.assertEqual(first.text.replace(first_code, "TOKEN"), second.text.replace(second_code, "TOKEN"))
            self.assertEqual(self.count("waitlist"), 1)
            client.post("/auth/waitlist/remove", data={"csrf": csrf, "removal": second_code})
            self.assertEqual(self.count("waitlist"), 1)
            with self.app.state.database.connect() as connection:
                self.assertEqual(connection.execute("SELECT erasure_generation FROM recovery_state").fetchone()[0], 0)
            client.post("/auth/waitlist/remove", data={"csrf": csrf, "removal": first_code})
            self.assertEqual(self.count("waitlist"), 0)
            with self.app.state.database.connect() as connection:
                self.assertEqual(connection.execute("SELECT erasure_generation FROM recovery_state").fetchone()[0], 1)

    def test_preauth_csrf_is_bound_expiring_and_rejects_duplicate_form_fields(self):
        self.settings = replace(self.settings, signups="invite")
        self.app = create_app(self.settings, self.clock, google_provider=self.provider)
        with self.client() as client:
            page = client.get("/signin")
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            self.assertEqual(client.post("/auth/google/start", content=f"csrf={csrf}&csrf=another", headers={"content-type": "application/x-www-form-urlencoded"}).status_code, 400)
            self.clock.advance(minutes=10)
            self.assertEqual(client.post("/auth/google/start", data={"csrf": csrf}).status_code, 403)

    def test_non_ascii_csrf_and_cookie_signature_are_refused_without_server_error(self):
        with self.client() as client:
            token = self.login(client)
            self.assertEqual(client.post("/auth/logout", data={"csrf": "λ" * 64}).status_code, 403)
            self.assertIsNotNone(self.app.state.auth.sessions.current(token))
            client.get("/signin")
            cookie = client.cookies[PREAUTH_COOKIE]
            bad_cookie = cookie.rsplit(".", 1)[0] + "." + "λ" * 64
            self.assertFalse(self.app.state.auth.sessions.preauth_valid(bad_cookie))
            self.assertFalse(self.app.state.auth.sessions.preauth_valid(cookie, "λ" * 64))

    def test_unknown_auth_routes_share_the_per_ip_limit(self):
        with self.client() as client:
            for _ in range(20):
                self.assertEqual(client.get("/auth/nonexistent").status_code, 404)
            self.assertEqual(client.get("/auth/nonexistent").status_code, 429)

    def test_unapproved_legal_pages_are_labelled_and_do_not_claim_full_v1_features(self):
        with self.client() as client:
            for path in ("/privacy", "/terms", "/contact"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Draft for review", response.text)
            privacy = client.get("/privacy").text
            self.assertIn("We store check-in reports you confirm", privacy)
            self.assertIn("Previewing does not send a request", privacy)
            self.assertIn("Email delivery is currently off", privacy)
            self.assertIn("Only after you approve the exact text, tags, looking-for and offering fields", privacy)
            self.assertIn("No conversation opens before both people agree.", privacy)

    def test_auth_rate_limit_is_twenty_per_minute_with_retry_after(self):
        with self.client() as client:
            for _ in range(20):
                self.assertEqual(client.get("/signin").status_code, 200)
            response = client.get("/signin")
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["Retry-After"], "3")
            self.clock.advance(seconds=3)
            self.assertEqual(client.get("/signin").status_code, 200)

    def test_feature_gate_and_missing_credentials_show_no_google_button(self):
        for changed in (replace(self.settings, account_signin_enabled=False), replace(self.settings, google_client_secret="")):
            self.app = create_app(changed, self.clock, google_provider=self.provider)
            with self.client() as client:
                response = client.get("/signin")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("Continue with Google", response.text)
                self.assertEqual(client.get("/auth/google/start").status_code, 503)

    def test_production_cannot_use_injected_test_provider(self):
        with self.assertRaises(ValueError):
            create_app(replace(self.settings, app_env="production"), self.clock, google_provider=self.provider)

    def test_production_signin_requires_explicit_legal_approval(self):
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"APP_ENV": "production", "PUBLIC_BASE_URL": "https://service.example.test", "DATABASE_PATH": str(self.settings.database_path), "SESSION_SECRET": self.settings.session_secret, "ACCOUNT_SIGNIN_ENABLED": "true"})

    def test_approved_legal_pages_require_controller_providers_and_transfer_details(self):
        base = {"APP_ENV": "production", "PUBLIC_BASE_URL": "https://service.example.test", "DATABASE_PATH": str(self.settings.database_path), "SESSION_SECRET": self.settings.session_secret, "LEGAL_PAGES_APPROVED": "true"}
        fields = {"LEGAL_CONTROLLER_NAME": "Fictional Project Ltd", "LEGAL_CONTACT_ADDRESS": "Fictional correspondence address", "CONTACT_EMAIL": "support@example.test", "VPS_PROVIDER_NAME": "Fictional hosting provider, UK", "EMAIL_PROVIDER_NAME": "Fictional email provider, UK", "LEGAL_TRANSFER_DETAILS": "Fictional test disclosure for an approved transfer review."}
        for field in fields:
            if field == "LEGAL_CONTACT_ADDRESS":
                continue
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                Settings.from_env({**base, **{key: value for key, value in fields.items() if key != field}})
        approved = Settings.from_env({**base, **fields})
        self.assertTrue(approved.legal_pages_approved)
        self.assertTrue(Settings.from_env({**base, **fields, "LEGAL_CONTACT_ADDRESS": ""}).legal_pages_approved)

    def test_draft_pages_do_not_invent_identity_or_correspondence_address(self):
        self.app = create_app(replace(self.settings, contact_email="support@example.test"), self.clock, google_provider=self.provider)
        with self.client() as client:
            for path in ("/privacy", "/terms", "/contact"):
                with self.subTest(path=path):
                    page = client.get(path)
                    self.assertEqual(page.status_code, 200)
                    self.assertNotIn("Correspondence address:", page.text)
                    self.assertIn("support@example.test", page.text)
                    if path != "/contact":
                        self.assertIn("[controller identity to be confirmed]", page.text)
                    else:
                        self.assertIn("Contact Connect the Bots", page.text)
                        self.assertNotIn("Operator:", page.text)


@unittest.skipUnless(AUTH_EXTRA, "Install the server extra for Google account tests")
class AuthErasureRaceTests(unittest.IsolatedAsyncioTestCase):
    """Drive real callback awaits deterministically, without timing sleeps."""
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.settings = settings(Path(self.temp.name) / "race.db")
        verifier = GoogleProvider(self.settings, self.clock)
        # Actual RSA JWT verification, with a previously fetched Google key.
        verifier._keys = {"fictional-race-key": self.key.public_key()}
        verifier._keys_until = self.clock() + timedelta(hours=1)
        test = self

        class Provider:
            authorization_url = "https://accounts.google.com/o/oauth2/v2/auth"
            pause = True
            nonce = None

            async def exchange(self, code, pkce, nonce_hash):
                if self.pause:
                    self.started.set()
                    await self.release.wait()
                token = jwt.encode({"iss": "https://accounts.google.com", "aud": test.settings.google_client_id,
                                    "sub": "fictional-race-subject", "email": "race@example.test", "email_verified": True,
                                    "name": "Fictional Person", "nonce": self.nonce,
                                    "iat": int(test.clock().timestamp()), "exp": int(test.clock().timestamp()) + 3600},
                                   test.key, algorithm="RS256", headers={"kid": "fictional-race-key"})
                return await verifier.verify(token, nonce_hash)

        self.provider = Provider()
        self.provider.started = asyncio.Event()
        self.provider.release = asyncio.Event()
        self.app = create_app(self.settings, self.clock, google_provider=self.provider)
        self.db = self.app.state.database
        self.db.migrate()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                               ("fictional-race-owner", "fictional-race-subject", "race@example.test", "Fictional Person", storage_time(self.clock()), storage_time(self.clock())))

    def request(self, path, *, query=b"", cookie=""):
        return Request({"type": "http", "method": "GET", "scheme": "https", "path": path, "query_string": query,
                        "headers": [(b"host", b"service.example.test"), (b"cookie", cookie.encode())],
                        "server": ("service.example.test", 443), "client": ("127.0.0.1", 1234)})

    async def callback(self):
        response = await self.app.state.auth.start(self.request("/auth/google/start"))
        self.assertEqual(response.status_code, 303)
        parameters = parse_qs(urlsplit(response.headers["location"]).query)
        self.provider.nonce = parameters["nonce"][0]
        cookies = SimpleCookie()
        cookies.load(response.headers["set-cookie"])
        query = ("state=" + parameters["state"][0] + "&code=fictional-code").encode()
        return self.app.state.auth.callback(self.request("/auth/google/callback", query=query,
                                                       cookie=OAUTH_COOKIE + "=" + cookies[OAUTH_COOKIE].value))

    def counts(self):
        with self.db.connect() as connection:
            return tuple(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                         for table in ("users", "sessions", "deletion_receipts", "oauth_attempts"))

    async def test_inflight_verified_callback_cannot_recreate_erased_account_but_fresh_login_can(self):
        callback = asyncio.create_task(await self.callback())
        await asyncio.wait_for(self.provider.started.wait(), 2)
        self.app.state.lifecycle.delete_account("fictional-race-owner")
        self.assertEqual(self.counts(), (0, 0, 1, 0))
        self.provider.release.set()
        response = await callback
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"no longer current", response.body)
        self.assertEqual(self.counts(), (0, 0, 1, 0))
        self.provider.pause = False
        self.assertEqual((await (await self.callback())).status_code, 303)
        self.assertEqual(self.counts(), (1, 1, 1, 0))

    async def test_erasure_invalidates_unconsumed_attempt_without_identity_tombstone(self):
        callback = await self.callback()
        self.assertEqual(self.counts(), (1, 0, 0, 1))
        self.app.state.lifecycle.delete_account("fictional-race-owner")
        with self.assertRaises(AuthenticationError):
            await callback
        self.assertEqual(self.counts(), (0, 0, 1, 0))

    async def test_final_callback_rechecks_recovery_hold_before_session_writes(self):
        callback = asyncio.create_task(await self.callback())
        await asyncio.wait_for(self.provider.started.wait(), 2)
        with self.db.transaction() as connection:
            connection.execute("UPDATE recovery_state SET recovery_hold=1 WHERE singleton=1")
        self.provider.release.set()
        response = await callback
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.counts(), (1, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
