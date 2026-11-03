"""Test-only loopback OIDC provider and disposable account applications.

This is never packaged as a production provider or selected by environment
configuration. Browser authorization and back-channel code/JWKS requests use
real local HTTP. The application still runs GoogleProvider.exchange/verify,
including real RSA signatures, PKCE, nonce, issuer, audience and expiry checks.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import timedelta
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sys
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
import uvicorn

from connectthebots_server.app import create_app
from connectthebots_server.auth import GoogleProvider, GOOGLE_TOKEN_URL, GOOGLE_JWKS_URL
from connectthebots_server.clock import utc_now
from connectthebots_server.config import Settings
from connectthebots_server.db import Database
from connectthebots_server.sessions import random_token, storage_time, token_hash


class LoopbackGoogleTransport(httpx.AsyncBaseTransport):
    """Map only Google's two exact back-channel URLs to the local test server."""

    def __init__(self, origin: str, *, timeout=5.0):
        self.origin = origin
        self.timeout = timeout

    async def handle_async_request(self, request):
        paths = {GOOGLE_TOKEN_URL: "/token", GOOGLE_JWKS_URL: "/jwks"}
        path = paths.get(str(request.url))
        if path is None:
            raise httpx.ConnectError("Test provider refuses non-fixture outbound requests", request=request)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=self.timeout) as client:
            response = await client.request(
                request.method, self.origin + path,
                headers={"Content-Type": request.headers.get("Content-Type", "application/json")},
                content=await request.aread(),
            )
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=response.content, request=request)


class SyntheticOIDC:
    def __init__(self, clock=utc_now):
        self.clock = clock
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        self.jwk.update({"kid": "fictional-browser-key", "use": "sig", "alg": "RS256"})
        self.attempts = {}
        self.codes = {}
        self.allowed_redirects = set()
        self.exchanges = 0
        self.jwks_reads = 0
        self.pkce_verified = 0
        self.token_release = threading.Event()
        self.token_release.set()
        self.token_response_pending = threading.Event()

    async def authorize(self, request):
        query = dict(request.query_params)
        if (query.get("redirect_uri") not in self.allowed_redirects
                or query.get("client_id") != "fictional-browser-client"
                or query.get("response_type") != "code"
                or query.get("scope") != "openid email profile"
                or query.get("code_challenge_method") != "S256"
                or not all(query.get(key) for key in ("state", "nonce", "code_challenge"))):
            return JSONResponse({"error": "invalid_authorization_request"}, status_code=400)
        identifier = secrets.token_urlsafe(24)
        self.attempts[identifier] = query
        return HTMLResponse(
            '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Fictional test identity provider</title><h1>Local test provider</h1>'
            '<p>Fictional accounts only. This is not Google.</p><form method="post" action="/approve">'
            f'<input type="hidden" name="attempt" value="{html.escape(identifier, quote=True)}">'
            '<label for="person">Fictional person</label><select name="person" id="person">'
            '<option value="ama">Ama Test</option><option value="joon">Joon Test</option>'
            '<option value="priya">Priya Test</option></select>'
            '<label for="scenario">Token scenario</label><select name="scenario" id="scenario">'
            '<option value="valid">Valid</option><option value="tampered-state">Tampered state</option>'
            '<option value="wrong-nonce">Wrong nonce</option><option value="expired-token">Expired token</option>'
            '<option value="wrong-audience">Wrong audience</option><option value="unverified-email">Unverified email</option>'
            '<option value="escaped-name">Name containing HTML</option>'
            '</select><button type="submit">Use fictional account</button></form></html>'
        )

    async def approve(self, request):
        fields = {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}
        attempt = self.attempts.pop(fields.get("attempt"), None)
        if attempt is None or fields.get("person") not in {"ama", "joon", "priya"}:
            return JSONResponse({"error": "invalid_test_attempt"}, status_code=400)
        code = secrets.token_urlsafe(32)
        self.codes[code] = {**attempt, "person": fields["person"], "scenario": fields.get("scenario", "valid")}
        state = random_token() if fields.get("scenario") == "tampered-state" else attempt["state"]
        return RedirectResponse(attempt["redirect_uri"] + "?" + urlencode({"code": code, "state": state}), status_code=303)

    async def token(self, request):
        fields = {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}
        attempt = self.codes.pop(fields.get("code"), None)
        if attempt is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(fields.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
        if (not secrets.compare_digest(challenge, attempt["code_challenge"])
                or fields.get("redirect_uri") != attempt["redirect_uri"]
                or fields.get("client_id") != "fictional-browser-client"
                or fields.get("grant_type") != "authorization_code"):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        self.pkce_verified += 1
        self.exchanges += 1
        person = attempt["person"]
        now = int(self.clock().timestamp())
        claims = {"iss": "https://accounts.google.com", "aud": "fictional-browser-client",
                  "sub": "fixture-" + person, "email": person + "@example.invalid",
                  "name": person.title() + " Test", "email_verified": True,
                  "iat": now, "exp": now + 300, "nonce": attempt["nonce"]}
        if attempt["scenario"] == "wrong-nonce":
            claims["nonce"] = random_token()
        elif attempt["scenario"] == "expired-token":
            claims.update(iat=now - 600, exp=now - 1)
        elif attempt["scenario"] == "wrong-audience":
            claims["aud"] = "different-fictional-client"
        elif attempt["scenario"] == "unverified-email":
            claims["email_verified"] = False
        elif attempt["scenario"] == "escaped-name":
            claims["name"] = person.title() + ' <script>alert("fixture")</script> Test'
        signed = jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.jwk["kid"]})
        if not self.token_release.is_set():
            self.token_response_pending.set()
            try:
                if not await asyncio.to_thread(self.token_release.wait, 30):
                    return JSONResponse({"error": "fictional_token_pause_expired"}, status_code=500)
            finally:
                self.token_response_pending.clear()
        return JSONResponse({"id_token": signed, "access_token": "fictional-unused-token", "token_type": "Bearer"})

    async def jwks(self, request):
        self.jwks_reads += 1
        return JSONResponse({"keys": [self.jwk]}, headers={"Cache-Control": "public, max-age=3600"})

    def app(self):
        return Starlette(routes=[Route("/authorize", self.authorize), Route("/approve", self.approve, methods=["POST"]),
                                 Route("/token", self.token, methods=["POST"]), Route("/jwks", self.jwks)])


def listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


def serve(app, sock, servers):
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    servers.append((server, thread, sock))
    for _ in range(100):
        if server.started:
            return
        if not thread.is_alive():
            raise RuntimeError("Fixture server failed to start")
        time.sleep(0.05)
    raise RuntimeError("Fixture server did not become ready")


class FixtureClock:
    """A test-process clock; advancing it never adds an application endpoint."""

    def __init__(self, *, frozen=False):
        self.offset = timedelta()
        self.fixed = utc_now() if frozen else None

    def __call__(self):
        return (self.fixed or utc_now()) + self.offset

    def advance(self, seconds):
        if type(seconds) is not int or not 0 <= seconds <= 7 * 24 * 60 * 60:
            raise ValueError("Invalid fictional clock advance")
        self.offset += timedelta(seconds=seconds)


def seed_board(database, clock, count):
    """Publish fictional pagination records through the same domain service."""
    from connectthebots_server.board import BoardService

    if type(count) is not int or not 1 <= count <= 40:
        raise ValueError("Invalid fictional board seed count")
    db = Database(database, clock)
    service = BoardService(db, clock)
    for index in range(count):
        owner_id = f"fictional-board-seed-{index:02d}"
        now = storage_time(clock())
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                (owner_id, f"fixture-board-seed-{index:02d}", f"fictional-seed-{index:02d}@example.invalid",
                 f"Fictional seed owner {index:02d}", now, now),
            )
        draft = service.save_draft(owner_id, {
            "text": f"I build fictional orchard sensors for plot {index:02d}. I can offer lab measurements. I am looking for repair documentation.",
            "tags": ["orchard", "pagination"], "looking_for": "Repair documentation", "offering": "Fictional lab measurements",
        })
        service.publish(owner_id, draft["revision"], draft["payload_hash"])


def interaction_evidence(database, clock):
    """Aggregate test evidence only: never export messages, tokens or identities."""
    with Database(database, clock).connect() as connection:
        counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("introductions", "intro_receipts", "conversations", "messages", "message_receipts", "user_blocks", "email_outbox")}
        counts["outbox_kinds"] = dict(connection.execute("SELECT kind,count(*) FROM email_outbox GROUP BY kind"))
        counts["open_conversations"] = connection.execute("SELECT count(*) FROM conversations WHERE closed_at IS NULL").fetchone()[0]
        counts["introduction_states"] = dict(connection.execute("SELECT status,count(*) FROM introductions GROUP BY status"))
        counts["deleted_messages"] = connection.execute("SELECT count(*) FROM messages WHERE sender_id IS NULL AND body='[deleted]'").fetchone()[0]
        counts["deleted_author_text_remains"] = bool(connection.execute("SELECT 1 FROM messages WHERE sender_id IS NULL AND body!='[deleted]' LIMIT 1").fetchone())
        counts["deleted_participant_context_remains"] = bool(connection.execute(
            "SELECT 1 FROM introductions WHERE (requester_id IS NULL OR recipient_id IS NULL) AND "
            "(requester_snapshot_json IS NOT NULL OR recipient_snapshot_json IS NOT NULL OR note IS NOT NULL "
            "OR reason IS NOT NULL OR context_hash IS NOT NULL OR requester_grant_id IS NOT NULL OR recipient_grant_id IS NOT NULL) LIMIT 1"
        ).fetchone())
        return counts


def fill_message_quota(database, clock, conversation_id):
    """Seed the boundary through the service, only for the fictional Ama owner."""
    from connectthebots_server.board import BoardService
    from connectthebots_server.interactions import InteractionService

    db = Database(database, clock)
    service = InteractionService(db, clock, BoardService(db, clock))
    with db.connect() as connection:
        owner = connection.execute("SELECT id FROM users WHERE google_sub='fixture-ama'").fetchone()
        if owner is None:
            raise ValueError("Fictional Ama account must exist")
        count = connection.execute("SELECT count(*) FROM messages WHERE sender_id=? AND created_at>?",
                                   (owner[0], storage_time(clock() - timedelta(days=1)))).fetchone()[0]
    for index in range(count, 99):
        service.send_message(owner[0], conversation_id, f"Fictional quota boundary message {index + 1}", random_token())


class RefuseSMTP:
    """Outbox-mode verification: a transport must never be called."""

    def __init__(self):
        self.attempts = 0

    def send(self, envelope):
        self.attempts += 1
        raise AssertionError("The fictional outbox must not attempt email delivery")


def notification_evidence(service, known_owners, smtp):
    """Keep capabilities and rendered email content out of saved evidence."""
    with service.database.connect() as connection:
        owners = {row["google_sub"].removeprefix("fixture-"): row["id"] for row in connection.execute(
            "SELECT id,google_sub FROM users WHERE google_sub IN ('fixture-ama','fixture-joon','fixture-priya')")}
    known_owners.update(owners)
    result = {"users": {}, "deleted_owner_rows": 0, "smtp_attempts": smtp.attempts}
    for person, owner in owners.items():
        listed = service.list_items(owner, limit=50)
        with service.database.connect() as connection:
            events = [dict(row) for row in connection.execute(
                "SELECT id,kind,created_at,read_at,cancelled_at FROM notification_events WHERE user_id=? ORDER BY rowid", (owner,))]
            mail = []
            for row in connection.execute("SELECT id,kind,state,payload_json FROM email_outbox WHERE user_id=? ORDER BY rowid", (owner,)):
                payload = json.loads(row["payload_json"])
                headers = payload.get("headers", {})
                unsubscribe = headers.get("List-Unsubscribe", "")
                mail.append({"id": row["id"], "kind": row["kind"], "state": row["state"],
                             "one_click": headers.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click",
                             "same_origin_unsubscribe": unsubscribe.startswith("<" + service.settings.public_base_url + "/email/unsubscribe/") and unsubscribe.endswith(">"),
                             "has_message_id": bool(headers.get("Message-ID")),
                             "contains_private_words": any(word in payload.get("text", "").casefold() for word in (
                                 "fictional private note", "fictional private message", "fictional message in", "fictional message starts",
                                 "fictional repair notes could explain", "i build fictional soil sensors", "i repair fictional workshop tools"))})
        result["users"][person] = {"preferences": service.preferences(owner), "events": events,
                                    "unread_total": listed["unread_total"], "visible_total": listed["total"], "mail": mail}
    with service.database.connect() as connection:
        for owner in set(known_owners.values()) - set(owners.values()):
            for table in ("notification_preferences", "notification_subscriptions", "notification_events", "notification_mail_windows", "email_outbox"):
                result["deleted_owner_rows"] += connection.execute(f"SELECT count(*) FROM {table} WHERE user_id=?", (owner,)).fetchone()[0]
    return result


def moderation_cli(app, command):
    """Run the real CLI with narrowly selected fictional targets, never a shell."""
    operation = command.get("operation")
    if operation in {"show", "suspend", "unsuspend", "delete"}:
        if command.get("person") not in {"ama", "joon", "priya"}:
            raise ValueError("Unknown fictional operator target")
        with app.state.database.connect() as connection:
            owner = connection.execute("SELECT id FROM users WHERE google_sub=?", ("fixture-" + command["person"],)).fetchone()
        if owner is None:
            raise ValueError("Fictional operator target does not exist")
        arguments = ["user", operation, "--", owner[0]]
        if operation == "delete":
            arguments = ["user", operation, "--confirm=" + owner[0], "--", owner[0]]
    elif operation in {"hide", "unhide"}:
        slug = command.get("slug")
        if not isinstance(slug, str) or re.fullmatch(r"[0-9a-f]{32}", slug) is None:
            raise ValueError("Invalid fictional posting selector")
        arguments = ["posting", operation, slug]
    elif operation == "reports":
        arguments = ["reports", "list", "--page", "1", "--limit", "20"]
    elif operation == "stats":
        arguments = ["stats"]
    else:
        raise ValueError("Unknown fictional operator action")
    settings = app.state.settings
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "APP_ENV": "staging", "PUBLIC_BASE_URL": settings.public_base_url,
                   "DATABASE_PATH": str(settings.database_path), "SESSION_SECRET": settings.session_secret,
                   "SIGNUPS": "open", "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "true",
                   "GOOGLE_CLIENT_ID": "fictional-browser-client", "GOOGLE_CLIENT_SECRET": "fictional-browser-secret",
                   "OPERATOR_EMAIL": "operator@fictional.example.test"}
    completed = subprocess.run([sys.executable, "-m", "connectthebots_server.admin", *arguments], cwd=REPOSITORY,
                               env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
    return {"exit_code": completed.returncode, "result": json.loads(completed.stdout) if completed.returncode == 0 else None,
            "error": completed.stderr.strip()}


def moderation_evidence(app):
    with app.state.database.connect() as connection:
        result = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("abuse_reports", "moderation_receipts", "operator_alerts")}
        result["operator_email_records"] = connection.execute("SELECT count(*) FROM email_outbox WHERE operator_alert_id IS NOT NULL").fetchone()[0]
        result["users"] = {}
        for row in connection.execute("SELECT id,google_sub,suspended_at FROM users WHERE google_sub IN ('fixture-ama','fixture-joon','fixture-priya')").fetchall():
            result["users"][row["google_sub"].removeprefix("fixture-")] = {
                "suspended": row["suspended_at"] is not None,
                "sessions": connection.execute("SELECT count(*) FROM sessions WHERE user_id=?", (row["id"],)).fetchone()[0],
                "active_keys": connection.execute("SELECT count(*) FROM report_keys WHERE user_id=? AND revoked_at IS NULL", (row["id"],)).fetchone()[0],
            }
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    modes_parser = parser.add_mutually_exclusive_group()
    modes_parser.add_argument("--reports", action="store_true", help="Run the report journey with a controllable test clock")
    modes_parser.add_argument("--board", action="store_true", help="Run the public board journey with fictional pagination records")
    modes_parser.add_argument("--interactions", action="store_true", help="Run fictional introductions with a controllable clock and aggregate evidence")
    modes_parser.add_argument("--notifications", action="store_true", help="Run fictional notifications with a frozen test clock and local outbox")
    modes_parser.add_argument("--moderation", action="store_true", help="Run fictional abuse reports and actual local operator commands")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    servers = []
    clock = FixtureClock(frozen=args.notifications)
    provider = SyntheticOIDC(clock)
    databases = {}
    applications = {}
    known_notification_owners = {}
    smtp = RefuseSMTP()
    # Interaction quota checks commit many small transactions. On Linux, keep
    # these disposable databases on the native temporary filesystem rather than
    # a Windows-mounted evidence directory, whose fsync is much slower.
    scratch_parent = None if (args.interactions or args.notifications or args.moderation) and sys.platform != "win32" else args.out
    with tempfile.TemporaryDirectory(prefix="fictional-accounts-", dir=scratch_parent) as scratch:
        try:
            oidc_socket = listener()
            oidc_origin = "http://127.0.0.1:" + str(oidc_socket.getsockname()[1])
            serve(provider.app(), oidc_socket, servers)
            modes = {}
            for mode in (("moderation",) if args.moderation else ("notifications",) if args.notifications else ("interactions",) if args.interactions else ("board",) if args.board else ("reports",) if args.reports else ("open", "invite", "closed", "data")):
                sock = listener()
                origin = "http://localhost:" + str(sock.getsockname()[1])
                database = Path(scratch) / (mode + ".db")
                settings = Settings.from_env({
                    "APP_ENV": "staging", "PUBLIC_BASE_URL": origin,
                    "DATABASE_PATH": str(database), "SESSION_SECRET": secrets.token_urlsafe(48),
                    "SIGNUPS": "open" if mode in {"data", "reports", "board", "interactions", "notifications", "moderation"} else mode, "EMAIL_MODE": "outbox", "ACCOUNT_SIGNIN_ENABLED": "true",
                    "GOOGLE_CLIENT_ID": "fictional-browser-client", "GOOGLE_CLIENT_SECRET": "fictional-browser-secret",
                    **({"OPERATOR_EMAIL": "operator@fictional.example.test"} if args.moderation else {}),
                })
                google = GoogleProvider(settings, clock, transport=LoopbackGoogleTransport(oidc_origin, timeout=40.0 if args.moderation else 5.0))
                google.authorization_url = oidc_origin + "/authorize"
                provider.allowed_redirects.add(origin + "/auth/google/callback")
                app = create_app(settings, clock, google_provider=google)
                applications[mode] = app
                app.router.routes.extend([
                    Mount("/assets", app=StaticFiles(directory=REPOSITORY / "site/assets")),
                    Route("/", lambda request: FileResponse(REPOSITORY / "site/index.html")),
                    Route("/example", lambda request: FileResponse(REPOSITORY / "site/dashboard.html")),
                ])
                serve(app, sock, servers)
                databases[mode] = database
                modes[mode] = {"url": origin}
                if mode == "invite":
                    invite = random_token()
                    db = Database(database, utc_now)
                    with db.transaction() as connection:
                        connection.execute("INSERT INTO invites (code_hash,created_at,expires_at) VALUES (?,?,?)",
                                           (token_hash(invite), storage_time(utc_now()), storage_time(utc_now() + timedelta(days=1))))
                    modes[mode]["invite"] = invite
                if mode == "data":
                    # Anonymous waitlist authority comes from its removal code,
                    # not from an account that later uses the same email.
                    with Database(database, utc_now).transaction() as connection:
                        connection.execute(
                            "INSERT INTO waitlist (id,email,consent_text,created_at,removal_hash) VALUES (?,?,?,?,?)",
                            ("independent-fictional-request", "ama@example.invalid", "Independent anonymous fixture consent",
                             storage_time(utc_now()), token_hash(random_token())),
                        )
            (args.out / "ready.json").write_text(json.dumps({"modes": modes, "provider_url": oidc_origin,
                                                          "clock": storage_time(clock())}) + "\n")
            print("Fictional OIDC browser fixture ready", flush=True)
            command_path = args.out / "clock-command.json"
            board_seed_path = args.out / "board-seed-command.json"
            interactions_path = args.out / "interactions-command.json"
            notifications_path = args.out / "notifications-command.json"
            moderation_path = args.out / "moderation-command.json"
            while not stopped.wait(0.1) and not (args.out / "stop").exists():
                if (args.reports or args.interactions or args.notifications) and command_path.exists():
                    try:
                        command = json.loads(command_path.read_text(encoding="utf-8"))
                        command_path.unlink()
                    except (FileNotFoundError, PermissionError):
                        continue
                    clock.advance(command["seconds"])
                    (args.out / "clock-ack.json").write_text(json.dumps({"id": command["id"], "clock": storage_time(clock())}) + "\n")
                if args.board and board_seed_path.exists():
                    try:
                        command = json.loads(board_seed_path.read_text(encoding="utf-8"))
                        board_seed_path.unlink()
                    except (FileNotFoundError, PermissionError):
                        continue
                    seed_board(databases["board"], clock, command["count"])
                    (args.out / "board-seed-ack.json").write_text(json.dumps({"id": command["id"], "count": command["count"]}) + "\n")
                if args.interactions and interactions_path.exists():
                    # Windows may briefly retain a sharing/delete lock after
                    # the browser process atomically renames its command file.
                    # Retry before performing any side effect.
                    try:
                        command = json.loads(interactions_path.read_text(encoding="utf-8"))
                        interactions_path.unlink()
                    except (FileNotFoundError, PermissionError):
                        continue
                    if command.get("action") == "fill_message_quota":
                        fill_message_quota(databases["interactions"], clock, command.get("conversation_id"))
                    elif command.get("action") != "snapshot":
                        raise ValueError("Unknown fictional interaction command")
                    evidence = interaction_evidence(databases["interactions"], clock)
                    (args.out / "interactions-ack.json").write_text(json.dumps({"id": command["id"], **evidence}) + "\n")
                if args.notifications and notifications_path.exists():
                    try:
                        command = json.loads(notifications_path.read_text(encoding="utf-8"))
                        notifications_path.unlink()
                    except (FileNotFoundError, PermissionError):
                        continue
                    service = applications["notifications"].state.notifications
                    extra = {}
                    if command.get("action") == "tick":
                        extra["tick"] = service.run_due(transport=smtp)
                    elif command.get("action") == "mail_url":
                        from connectthebots_server.notifications import KINDS
                        if command.get("person") not in {"ama", "joon", "priya"} or command.get("kind") not in KINDS:
                            raise ValueError("Invalid fictional mail selector")
                        with service.database.connect() as connection:
                            row = connection.execute(
                                "SELECT o.payload_json FROM email_outbox o JOIN users u ON u.id=o.user_id WHERE u.google_sub=? AND o.kind=? ORDER BY o.rowid DESC LIMIT 1",
                                ("fixture-" + command["person"], command["kind"]),
                            ).fetchone()
                        if row is None:
                            raise ValueError("Fictional email must exist before reading its unsubscribe link")
                        extra["url"] = json.loads(row[0])["headers"]["List-Unsubscribe"][1:-1]
                    elif command.get("action") != "snapshot":
                        raise ValueError("Unknown fictional notification command")
                    evidence = notification_evidence(service, known_notification_owners, smtp)
                    (args.out / "notifications-ack.json").write_text(json.dumps({"id": command["id"], **evidence, **extra}) + "\n")
                if args.moderation and moderation_path.exists():
                    try:
                        command = json.loads(moderation_path.read_text(encoding="utf-8"))
                        moderation_path.unlink()
                    except (FileNotFoundError, PermissionError):
                        continue
                    extra = {}
                    if command.get("action") == "operator":
                        extra["cli"] = moderation_cli(applications["moderation"], command)
                    elif command.get("action") == "pause_token":
                        provider.token_response_pending.clear()
                        provider.token_release.clear()
                    elif command.get("action") == "resume_token":
                        provider.token_release.set()
                    elif command.get("action") != "snapshot":
                        raise ValueError("Unknown fictional moderation command")
                    evidence = moderation_evidence(applications["moderation"])
                    evidence["token_response_pending"] = provider.token_response_pending.is_set()
                    (args.out / "moderation-ack.json").write_text(json.dumps({"id": command["id"], **evidence, **extra}) + "\n")
            record = {"fixture_only": True, "real_google_tested": False, "code_exchanges": provider.exchanges,
                      "pkce_verified": provider.pkce_verified, "jwks_reads": provider.jwks_reads, "modes": {}}
            for mode, path in databases.items():
                with Database(path, utc_now).connect() as connection:
                    record["modes"][mode] = {
                        "users": connection.execute("SELECT count(*) FROM users").fetchone()[0],
                        "sessions": connection.execute("SELECT count(*) FROM sessions").fetchone()[0],
                        "waitlist": connection.execute("SELECT count(*) FROM waitlist").fetchone()[0],
                        "email_outbox": connection.execute("SELECT count(*) FROM email_outbox").fetchone()[0],
                        "deletion_receipts": connection.execute("SELECT count(*) FROM deletion_receipts").fetchone()[0],
                    }
                    if args.reports:
                        # Aggregate evidence only. Do not export private keys,
                        # sessions, report bodies or database snapshots.
                        record["modes"][mode].update({
                            "reports": connection.execute("SELECT count(*) FROM reports").fetchone()[0],
                            "report_receipts": connection.execute("SELECT count(*) FROM report_receipts").fetchone()[0],
                            "report_keys": connection.execute("SELECT count(*) FROM report_keys").fetchone()[0],
                            "revoked_report_keys": connection.execute("SELECT count(*) FROM report_keys WHERE revoked_at IS NOT NULL").fetchone()[0],
                            "cleartext_report_key_found": any(
                                re.search(r"ctb_[A-Z2-7]{52}", statement) is not None
                                for statement in connection.iterdump()
                            ),
                        })
                    if args.board:
                        record["modes"][mode].update({
                            "postings": connection.execute("SELECT count(*) FROM board_postings").fetchone()[0],
                            "revisions": connection.execute("SELECT count(*) FROM board_revisions").fetchone()[0],
                            "publication_grants": connection.execute("SELECT count(*) FROM board_publication_grants").fetchone()[0],
                            "revoked_grants": connection.execute("SELECT count(*) FROM board_publication_grants WHERE revoked_at IS NOT NULL").fetchone()[0],
                        })
                    if args.interactions:
                        record["modes"][mode].update(interaction_evidence(path, clock))
                    if args.notifications:
                        record["modes"][mode].update(notification_evidence(applications[mode].state.notifications, known_notification_owners, smtp))
                    if args.moderation:
                        record["modes"][mode].update(moderation_evidence(applications[mode]))
            (args.out / "fixture-evidence.json").write_text(json.dumps(record, indent=2) + "\n")
        finally:
            provider.token_release.set()
            for server, _, _ in servers:
                server.should_exit = True
            for _, thread, sock in servers:
                thread.join(timeout=5)
                sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
