"""Google OpenID Connect and account routes with explicit owner/browser binding.

Only this module talks to Google. The injected provider is a Python test seam,
never an environment-selectable URL or a production authentication bypass.
"""

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
import math
import re
from urllib.parse import parse_qs, urlencode

import httpx
import jwt
from jinja2 import Environment, PackageLoader, select_autoescape
from .presentation import readable_time
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .clock import Clock
from .config import Settings
from .db import Database
from .sessions import (
    PREAUTH_COOKIE, SESSION_COOKIE, PREAUTH_SECONDS, Sessions, clear_session_cookie,
    random_token, set_session_cookie, storage_time, token_hash, valid_token,
)


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
OAUTH_COOKIE = "__Host-ctb_oauth"
OAUTH_LIFETIME = timedelta(minutes=10)
WAITLIST_CONSENT = "Keep my email address to tell me when Connect the Bots opens. I can remove it at any time."


class AuthenticationError(ValueError):
    """Safe fixed messages only; never wrap provider responses or JWT contents."""


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str
    display_name: str


def _plain(value, maximum: int, *, optional: bool = False) -> str:
    if not isinstance(value, str) or (not optional and not value) or len(value) > maximum:
        raise AuthenticationError("Google returned incomplete account information. Please try again.")
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise AuthenticationError("Google returned invalid account information. Please try again.")
    return value


def valid_email(value: str) -> bool:
    return isinstance(value, str) and len(value) <= 254 and re.fullmatch(r"[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+\.[^\s@\x00-\x1f]+", value) is not None


class GoogleProvider:
    """Fixed HTTPS endpoints, bounded responses, RS256-only ID-token verification."""

    authorization_url = GOOGLE_AUTH_URL

    def __init__(self, settings: Settings, clock: Clock, *, transport=None):
        self.settings = settings
        self.clock = clock
        self.transport = transport
        self._keys = {}
        self._keys_until = None
        self._key_lock = asyncio.Lock()

    async def _request(self, method: str, url: str, **kwargs):
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                async with client.stream(method, url, **kwargs) as response:
                    if response.status_code != 200:
                        raise AuthenticationError("Google sign-in could not be completed. Please try again.")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 64 * 1024:
                            raise AuthenticationError("Google sign-in returned an invalid response. Please try again.")
                        raw.extend(chunk)
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError
                    return data, response.headers
        except AuthenticationError:
            raise
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise AuthenticationError("Google sign-in is temporarily unavailable. Please try again.") from None

    def _cache_seconds(self, headers) -> int:
        control = headers.get("cache-control", "").lower()
        if "no-store" in control or "no-cache" in control:
            return 0
        match = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d{1,10})"?(?:\s*,|\s*$)', control)
        if not match:
            return 0
        age = headers.get("age", "0")
        age_seconds = int(age) if age.isascii() and age.isdigit() and len(age) <= 10 else 0
        return max(0, min(86400, int(match.group(1)) - age_seconds))

    async def _load_keys(self):
        data, headers = await self._request("GET", GOOGLE_JWKS_URL)
        keys = data.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
            raise AuthenticationError("Google signing keys are unavailable. Please try again.")
        parsed = {}
        try:
            for key in keys:
                if not isinstance(key, dict) or key.get("kty") != "RSA" or key.get("use", "sig") != "sig" or key.get("alg", "RS256") != "RS256":
                    continue
                kid = _plain(key.get("kid"), 256)
                if kid in parsed or ("key_ops" in key and "verify" not in key["key_ops"]):
                    raise ValueError
                parsed_key = jwt.PyJWK.from_dict(key, algorithm="RS256").key
                if parsed_key.key_size < 2048:
                    raise ValueError
                parsed[kid] = parsed_key
        except (ValueError, TypeError, jwt.PyJWTError):
            raise AuthenticationError("Google signing keys are unavailable. Please try again.") from None
        if not parsed:
            raise AuthenticationError("Google signing keys are unavailable. Please try again.")
        self._keys = parsed
        self._keys_until = self.clock() + timedelta(seconds=self._cache_seconds(headers))

    async def _signing_key(self, kid: str):
        async with self._key_lock:
            fresh = self._keys_until is not None and self.clock() < self._keys_until
            if not fresh or kid not in self._keys:
                await self._load_keys()
            if kid not in self._keys:
                raise AuthenticationError("Google sign-in could not be verified. Please try again.")
            return self._keys[kid]

    async def verify(self, id_token: str, nonce_hash: str) -> Identity:
        if not isinstance(id_token, str) or not 1 <= len(id_token) <= 16 * 1024:
            raise AuthenticationError("Google sign-in could not be verified. Please try again.")
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") != "RS256" or header.get("crit"):
                raise ValueError
            kid = _plain(header.get("kid"), 256)
            key = await self._signing_key(kid)
            claims = jwt.decode(
                id_token, key=key, algorithms=["RS256"],
                audience=self.settings.google_client_id, issuer=GOOGLE_ISSUERS,
                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False,
                         "require": ["iss", "aud", "exp", "iat", "sub", "email", "email_verified", "nonce"]},
            )
            now = self.clock().timestamp()
            for field in ("exp", "iat"):
                if type(claims[field]) not in {int, float} or not math.isfinite(claims[field]):
                    raise ValueError
            if claims["exp"] <= now or claims["iat"] > now + 60 or claims["exp"] <= claims["iat"]:
                raise ValueError
            if "nbf" in claims and (type(claims["nbf"]) not in {int, float} or not math.isfinite(claims["nbf"]) or claims["nbf"] > now):
                raise ValueError
            if "azp" in claims and claims["azp"] != self.settings.google_client_id:
                raise ValueError
            if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != self.settings.google_client_id:
                raise ValueError
            if claims["email_verified"] is not True:
                raise ValueError
            nonce = _plain(claims["nonce"], 256)
            if not hmac.compare_digest(token_hash(nonce), nonce_hash):
                raise ValueError
            sub = _plain(claims["sub"], 255)
            if not sub.isascii():
                raise ValueError
            email = _plain(claims["email"], 254)
            if not valid_email(email):
                raise ValueError
            return Identity(sub, email, _plain(claims.get("name", "Account holder"), 200))
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise AuthenticationError("Google sign-in could not be verified. Please try again.") from None

    async def exchange(self, code: str, verifier: str, nonce_hash: str) -> Identity:
        data, _ = await self._request("POST", GOOGLE_TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "redirect_uri": self.settings.public_base_url + "/auth/google/callback",
            "code_verifier": verifier,
        })
        # Access and refresh tokens are intentionally never read or persisted.
        return await self.verify(data.get("id_token"), nonce_hash)


class AuthRateLimiter:
    """A bounded per-IP bucket for this service's single Uvicorn process."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.buckets = {}

    def admit(self, address: str) -> int:
        now = self.clock().timestamp()
        hashed = token_hash(address)
        if len(self.buckets) >= 4096:
            self.buckets = {key: value for key, value in self.buckets.items() if now - value[1] < 60}
            if len(self.buckets) >= 4096 and hashed not in self.buckets:
                return 60
        tokens, last = self.buckets.get(hashed, (20.0, now))
        tokens = min(20.0, tokens + max(0.0, now - last) / 3.0)
        if tokens < 1.0:
            self.buckets[hashed] = (tokens, max(last, now))
            return max(1, math.ceil((1.0 - tokens) * 3.0))
        self.buckets[hashed] = (tokens - 1.0, max(last, now))
        return 0


async def form_values(request, *, fields: set[str]) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise AuthenticationError("This form could not be read. Please try again.")
    try:
        values = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=16)
        if not set(values) <= fields or any(len(value) != 1 for value in values.values()):
            raise ValueError
        # Native HTML forms encode textarea line breaks as CRLF. Convert only
        # that transport representation; lone CR remains invalid content.
        return {name: value[0].replace("\r\n", "\n") for name, value in values.items()}
    except (ValueError, UnicodeError):
        raise AuthenticationError("This form could not be read. Please try again.") from None


class AuthService:
    def __init__(self, settings: Settings, database: Database, clock: Clock, *, provider=None, notifications=None):
        if provider is not None and settings.app_env == "production":
            raise ValueError("A test identity provider cannot be used in production")
        self.settings = settings
        self.database = database
        self.clock = clock
        self.notifications = notifications
        self.referrals = None
        self.provider = GoogleProvider(settings, clock) if provider is None else provider
        self.sessions = Sessions(database, clock, settings.session_secret)
        self.limiter = AuthRateLimiter(clock)
        self.templates = Environment(
            loader=PackageLoader("connectthebots_server", "templates"),
            autoescape=select_autoescape(("html",)),
        )
        self.templates.filters["readable_time"] = readable_time

    @property
    def enabled(self) -> bool:
        from .recovery import recovery_held
        return bool(
            self.settings.account_signin_enabled and self.settings.google_client_id and self.settings.google_client_secret
            and (self.settings.app_env != "production" or self.settings.legal_pages_approved)
            and not recovery_held(self.database, self.settings)
        )

    def render(self, template: str, *, status: int = 200, **context):
        session = context.get("session")
        if session is not None and self.notifications is not None:
            context["notification_unread_count"] = self.notifications.unread_count(session.user_id)
        return HTMLResponse(self.templates.get_template(template).render(**context), status_code=status)

    def error(self, message: str, status: int = 400):
        return self.render("auth_error.html", message=message, status=status)

    def routes(self):
        def route(path, endpoint, methods):
            async def dispatch(request):
                retry = self.limiter.admit(request.client.host if request.client else "unknown")
                if retry:
                    return JSONResponse({"error": "Please wait before trying again."}, status_code=429, headers={"Retry-After": str(retry)})
                try:
                    return await endpoint(request)
                except AuthenticationError as error:
                    return self.error(str(error))
            return Route(path, dispatch, methods=methods)

        return [
            route("/signin", self.signin, ["GET"]),
            route("/auth/google/start", self.start, ["GET", "POST"]),
            route("/auth/google/callback", self.callback, ["GET"]),
            route("/auth/logout", self.logout, ["POST"]),
            route("/auth/waitlist", self.waitlist, ["POST"]),
            route("/auth/waitlist/remove", self.remove_waitlist, ["GET", "POST"]),
            Route("/privacy", self.privacy, methods=["GET"]),
            Route("/terms", self.terms, methods=["GET"]),
            Route("/contact", self.contact, methods=["GET"]),
            route("/auth/{path:path}", self.unknown, ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
        ]

    async def unknown(self, request):
        return JSONResponse({"error": "Not found"}, status_code=404)

    def origin_valid(self, request) -> bool:
        return request.headers.get("origin") in {None, self.settings.public_base_url}

    async def privacy(self, request):
        from .agent_texts import GOAL_PRIVACY_NOTICE
        return self.render("privacy.html", settings=self.settings, goal_privacy_notice=GOAL_PRIVACY_NOTICE)

    async def terms(self, request):
        from .agent_texts import GOAL_TERMS_NOTICE
        return self.render("terms.html", settings=self.settings, goal_terms_notice=GOAL_TERMS_NOTICE)

    async def contact(self, request):
        return self.render("contact.html", settings=self.settings)

    async def signin(self, request):
        if not self.enabled:
            return self.render("signin.html", enabled=False, mode=self.settings.signups, status=503)
        cookie, csrf = self.sessions.preauth(request.cookies.get(PREAUTH_COOKIE))
        from .referrals import normalize_code
        referral = normalize_code(request.query_params.get("ref", "")) or ""
        response = self.render("signin.html", enabled=True, mode=self.settings.signups, csrf=csrf, consent=WAITLIST_CONSENT, referral=referral)
        response.set_cookie(PREAUTH_COOKIE, cookie, max_age=PREAUTH_SECONDS, secure=True, httponly=True, samesite="lax", path="/")
        return response

    async def start(self, request):
        if not self.enabled:
            return self.error("Sign-in is not open yet.", 503)
        invite_hash = referral_code = None
        if request.method == "POST":
            fields = await form_values(request, fields={"csrf", "invite", "referral"})
            if not self.origin_valid(request) or not fields.get("csrf") or not self.sessions.preauth_valid(request.cookies.get(PREAUTH_COOKIE), fields.get("csrf")):
                return self.error("This form expired. Please open sign-in again.", 403)
            invite = fields.get("invite", "").strip()
            if invite:
                if not valid_token(invite):
                    return self.error("That invite code is not valid.")
                invite_hash = token_hash(invite)
            referral = fields.get("referral", "").strip()
            if referral:
                from .referrals import normalize_code
                referral_code = normalize_code(referral)
                if referral_code is None:
                    return self.error("That referral code is not valid. Codes look like CTB-7K3QXM. You can also leave it empty.")
        elif request.query_params:
            return self.error("Open sign-in again to continue.")
        state, binding, nonce, verifier = (random_token() for _ in range(4))
        now = self.clock()
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM oauth_attempts WHERE expires_at <= ?", (storage_time(now),))
            connection.execute(
                "INSERT INTO oauth_attempts (state_hash, binding_hash, nonce_hash, verifier, invite_hash, expires_at, referral_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (token_hash(state), token_hash(binding), token_hash(nonce), verifier, invite_hash, storage_time(now + OAUTH_LIFETIME), referral_code),
            )
        query = urlencode({
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.public_base_url + "/auth/google/callback",
            "response_type": "code", "scope": "openid email profile",
            "state": state, "nonce": nonce,
            "code_challenge": base64.urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("="),
            "code_challenge_method": "S256", "access_type": "online",
        })
        target = self.provider.authorization_url + "?" + query
        # A cross-origin redirect following a POST can be blocked by browsers'
        # form-action 'self' CSP. Invite forms therefore land on an ordinary
        # same-origin page whose link starts a new navigation. Open sign-in is
        # already a link to this GET route and can redirect directly to Google.
        response = self.render("google_continue.html", target=target) if request.method == "POST" else RedirectResponse(target, status_code=303)
        response.set_cookie(OAUTH_COOKIE, binding, max_age=600, secure=True, httponly=True, samesite="lax", path="/")
        return response

    def consume_attempt(self, state: str | None, binding: str | None):
        if not valid_token(state) or not valid_token(binding):
            raise AuthenticationError("This sign-in attempt is invalid or expired. Please start again.")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM oauth_attempts WHERE state_hash = ?", (token_hash(state),)).fetchone()
            if row is None or not hmac.compare_digest(row["state_hash"], token_hash(state)) or not hmac.compare_digest(row["binding_hash"], token_hash(binding)):
                raise AuthenticationError("This sign-in attempt is invalid or expired. Please start again.")
            generation = connection.execute("SELECT erasure_generation FROM recovery_state WHERE singleton=1").fetchone()
            if generation is None:
                raise AuthenticationError("Sign-in is temporarily unavailable. Please try again.")
            connection.execute("DELETE FROM oauth_attempts WHERE state_hash = ?", (token_hash(state),))
        if row["expires_at"] <= storage_time(self.clock()):
            raise AuthenticationError("This sign-in attempt is invalid or expired. Please start again.")
        return {**dict(row), "erasure_generation": generation[0]}

    def complete_login(self, identity, attempt, previous_token):
        # Keep the SQLite connection and its independent recovery check on the
        # same worker thread. The remote marker must finish before this commits.
        with self.database.transaction() as connection:
            from .recovery import connection_held
            generation = connection.execute("SELECT erasure_generation FROM recovery_state WHERE singleton=1").fetchone()
            # Google may finish after an erasure or recovery intervention.
            # Never reconstruct an erased identity from an older sign-in.
            if (connection_held(connection, self.settings) or generation is None
                    or generation[0] != attempt["erasure_generation"]):
                raise AuthenticationError("This sign-in attempt is no longer current. Please start again.")
            now = storage_time(self.clock())
            user = connection.execute("SELECT * FROM users WHERE google_sub = ?", (identity.sub,)).fetchone()
            if user is not None and user["suspended_at"] is not None:
                raise AuthenticationError("This account is not available. Please contact the service operator.")
            if user is None:
                if self.settings.signups == "closed":
                    raise AuthenticationError("New accounts are not open yet. You can join the waitlist from sign-in.")
                if self.settings.signups == "invite":
                    invitation = connection.execute("SELECT * FROM invites WHERE code_hash = ?", (attempt["invite_hash"],)).fetchone()
                    if invitation is None or invitation["used_at"] is not None or invitation["revoked_at"] is not None or invitation["expires_at"] <= now:
                        raise AuthenticationError("A current, unused invite code is needed to create an account.")
                    connection.execute("UPDATE invites SET used_at = ? WHERE code_hash = ?", (now, attempt["invite_hash"]))
                user_id = random_token()
                connection.execute("INSERT INTO users (id, google_sub, email, display_name, created_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?)", (user_id, identity.sub, identity.email, identity.display_name, now, now))
                from .referrals import ensure_code
                ensure_code(connection, user_id, self.clock())
                if attempt.get("referral_code") and self.referrals is not None:
                    # A referral is a perk, never a condition of sign-up. Invalid,
                    # self-referred or rate-limited codes never block an account.
                    from . import growth_config
                    from .agent_common import AgentError
                    try:
                        self.referrals.enter(connection, user_id, attempt["referral_code"], "signup", growth_config.load(connection))
                    except AgentError:
                        pass
                if self.notifications is not None:
                    self.notifications.record_event(connection, user_id, "security_first_login")
                else:
                    connection.execute("INSERT INTO email_outbox (id, user_id, kind, created_at, payload_json) VALUES (?, ?, ?, ?, ?)", (random_token(), user_id, "security_first_login", now, json.dumps({"event": "first_login"})))
            else:
                user_id = user["id"]
                connection.execute("UPDATE users SET email = ?, display_name = ?, last_login_at = ? WHERE id = ?", (identity.email, identity.display_name, now, user_id))
            token = self.sessions.create(connection, user_id, previous_token)
        return token

    async def callback(self, request):
        if not self.enabled:
            return self.error("Sign-in is not open yet.", 503)
        parameters = request.query_params
        if any(len(parameters.getlist(key)) != 1 for key in ("state", "code", "error") if key in parameters):
            return self.error("This sign-in attempt is invalid. Please start again.")
        attempt = await run_in_threadpool(self.consume_attempt, parameters.get("state"), request.cookies.get(OAUTH_COOKIE))
        response = None
        try:
            if "error" in parameters:
                raise AuthenticationError("Google sign-in was cancelled. You can start again when you are ready.")
            code = parameters.get("code")
            if not isinstance(code, str) or not 1 <= len(code) <= 2048:
                raise AuthenticationError("This sign-in attempt is invalid. Please start again.")
            identity = await self.provider.exchange(code, attempt["verifier"], attempt["nonce_hash"])
            token = await run_in_threadpool(self.complete_login, identity, attempt, request.cookies.get(SESSION_COOKIE))
            response = RedirectResponse("/app", status_code=303)
            set_session_cookie(response, token)
        except AuthenticationError as error:
            response = self.error(str(error))
        finally:
            if response is not None:
                response.delete_cookie(OAUTH_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    async def logout(self, request):
        session = self.sessions.current(request.cookies.get(SESSION_COOKIE), refresh=False)
        if session is None:
            return self.error("Please sign in again.", 401)
        fields = await form_values(request, fields={"csrf"})
        if not self.origin_valid(request) or not self.sessions.csrf_valid(session, fields.get("csrf")):
            return self.error("This form expired. Please reload the page.", 403)
        self.sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/signin", status_code=303)
        clear_session_cookie(response)
        return response

    async def waitlist(self, request):
        if not self.enabled or self.settings.signups != "closed":
            return self.error("The waitlist is not available.", 404)
        fields = await form_values(request, fields={"csrf", "email", "consent"})
        if not self.origin_valid(request) or not fields.get("csrf") or not self.sessions.preauth_valid(request.cookies.get(PREAUTH_COOKIE), fields.get("csrf")):
            return self.error("This form expired. Please open sign-in again.", 403)
        email = fields.get("email", "").strip().lower()
        if not valid_email(email) or fields.get("consent") != "yes":
            return self.error("Enter a valid email and agree to the waitlist wording.", 422)
        removal = random_token()
        with self.database.transaction() as connection:
            # A repeated email receives the same generic UI and an inert random
            # code. It cannot replace someone else's removal authority.
            connection.execute("INSERT OR IGNORE INTO waitlist (id, email, consent_text, created_at, removal_hash) VALUES (?, ?, ?, ?, ?)", (random_token(), email, WAITLIST_CONSENT, storage_time(self.clock()), token_hash(removal)))
        return self.render("waitlist_saved.html", removal=removal)

    def erase_waitlist(self, removal):
        with self.database.transaction() as connection:
            removed = connection.execute("DELETE FROM waitlist WHERE removal_hash = ?", (token_hash(removal),)).rowcount
            if removed:
                from .recovery import advance_erasure
                advance_erasure(connection)

    async def remove_waitlist(self, request):
        if request.method == "GET":
            cookie, csrf = self.sessions.preauth(request.cookies.get(PREAUTH_COOKIE))
            response = self.render("waitlist_remove.html", csrf=csrf)
            response.set_cookie(PREAUTH_COOKIE, cookie, max_age=PREAUTH_SECONDS, secure=True, httponly=True, samesite="lax", path="/")
            return response
        fields = await form_values(request, fields={"csrf", "removal"})
        if not self.origin_valid(request) or not fields.get("csrf") or not self.sessions.preauth_valid(request.cookies.get(PREAUTH_COOKIE), fields.get("csrf")):
            return self.error("This form expired. Please open the removal page again.", 403)
        removal = fields.get("removal", "").strip()
        if not valid_token(removal):
            return self.error("Enter the removal code you saved.", 422)
        await run_in_threadpool(self.erase_waitlist, removal)
        return self.render("waitlist_removed.html")
