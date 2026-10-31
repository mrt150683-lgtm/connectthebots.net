"""Agent-first JSON API: interview, matches, referrals, boards, ranks and check-in.

Every route authenticates a current agent key (a report key created after this
API existed) and derives the owner only from that key. Sharing actions require
an explicit approved_by_owner: true, which is the agent's statement that its
person said yes; nothing here contacts anyone outside Connect the Bots.
"""
from datetime import timedelta
from hashlib import sha256
import hmac
import json
import re

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from . import agent_texts, growth_config
from .campaign import Campaign, rank_targets, share_options
from .agent_common import AgentError, clean_text, daily_limit, public_id, require_approval, require_owner, user_for_public_id
from .board_routes import BoardJsonLimiter
from .leaderboards import leaderboards, referral_rows, window_start
from .referrals import ensure_code, refresh_early_access
from .report_keys import bearer_key, key_hash
from .report_schema import ReportError
from .sessions import random_token, storage_time


INTEREST_WORDS = {"friends": "company friendship", "company": "company friendship", "club": "clubs starting", "collaborators": "projects"}


def _json_body(raw):
    try:
        value = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeError, ValueError):
        raise AgentError(422, "Send a JSON object.") from None
    if not isinstance(value, dict):
        raise AgentError(422, "Send a JSON object.")
    return value


class AgentApi:
    def __init__(self, database, clock, settings, taste, referrals, boards, interactions):
        self.database, self.clock, self.settings = database, clock, settings
        self.taste, self.referrals, self.boards, self.interactions = taste, referrals, boards, interactions
        self.limiter = BoardJsonLimiter(clock, settings.session_secret)
        self.campaign = Campaign(clock, settings)
        from .agent_connect import ConnectLinks
        self.connect_links = ConnectLinks(database, clock, settings, self)

    # Authentication ------------------------------------------------------------
    def authenticate(self, authorization):
        try:
            hashed = key_hash(bearer_key(authorization))
        except ReportError:
            raise AgentError(401, "Use a current agent key in the Authorization: Bearer header.") from None
        with self.database.transaction() as connection:
            row = connection.execute("SELECT k.*,u.suspended_at FROM report_keys k JOIN users u ON u.id=k.user_id WHERE k.key_hash=?", (hashed,)).fetchone()
            if row is None or not hmac.compare_digest(row["key_hash"], hashed) or row["revoked_at"] is not None or row["suspended_at"] is not None:
                raise AgentError(401, "Use a current agent key for an available account.")
            if not row["agent_api"]:
                raise AgentError(403, "This key was created for reports only. Create a new key in your dashboard to use the agent API.")
            connection.execute("UPDATE report_keys SET last_used_at=? WHERE id=?", (storage_time(self.clock()), row["id"]))
            return {"user_id": row["user_id"], "key_id": row["id"]}

    def _refresh_access(self, connection, config):
        now = self.clock()
        refresh_early_access(connection, now, config, referral_rows(connection, window_start(now, config)))

    # Identity ------------------------------------------------------------------
    def me(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            self._refresh_access(connection, config)
            public_id(connection, user_id, self.clock())
            interview = self.taste.interview(user_id, connection)
            fingerprint = self.taste._fingerprint(connection, user_id)
            return {"member": self.taste._member(connection, user_id),
                    "interview": {"complete": interview["complete"], "answered": interview["answered"],
                                  "minimum_to_complete": interview["minimum_to_complete"]},
                    "referrals": self.referrals.status(connection, user_id),
                    "settings": {"referral_perk_days": config["referral_perk_days"], "early_access_percent": config["early_access_percent"],
                                 "leaderboard_window_days": config["leaderboard_window_days"], "checkin_interval_days": config["checkin_interval_days"],
                                 "referral_qualifies_on": config["referral_qualifies_on"]},
                    "personality": {"saved": fingerprint is not None,
                                    "used_for_matching": bool(fingerprint and fingerprint["used_for_matching"])},
                    "texts_version": agent_texts.VERSION, "connected": True,
                    "next_step": self._next_step(interview, config, fingerprint)}

    @staticmethod
    def _next_step(interview, config, fingerprint=None):
        # The agent can always ask /me where to pick up, so the prompt stays short.
        step = AgentApi._main_step(interview, config)
        if interview["answered"] and fingerprint is None:
            step["optional"] = {"call": ["GET /api/v1/agent/personality"],
                                "why": "Optional: offer your person a personality fingerprint, so matching can find people whose temperament fits theirs. Read the personality_fingerprint text first; they see every trait and approve before anything is sent."}
        return step

    @staticmethod
    def _main_step(interview, config):
        if interview["answered"] == 0:
            return {"call": ["GET /api/v1/agent/texts", "GET /api/v1/agent/interview"],
                    "why": "You are connected. Read the texts once, then start the taste interview with your person: a relaxed chat, one question at a time, and every question can be skipped."}
        if not interview["complete"]:
            return {"call": ["GET /api/v1/agent/interview"],
                    "why": f"The interview is under way: {interview['answered']} topics answered, {interview['minimum_to_complete']} needed. Carry on when it suits your person, then POST /api/v1/agent/interview/complete."}
        return {"call": ["POST /api/v1/agent/checkin"],
                "why": f"The interview is complete. Check in about every {config['checkin_interval_days']} days and tell your person only what is worth their time."}

    def referral_summary(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            self._refresh_access(connection, growth_config.load(connection))
            return self.referrals.status(connection, user_id)

    # Sharing -------------------------------------------------------------------
    def share(self, user_id, fields):
        if not set(fields) <= {"kind", "gap", "friend", "place", "approved_by_owner"}:
            raise AgentError(422, "Send kind, approved_by_owner and, for missing_person, gap and optional friend; for a public place, place.")
        kind = fields.get("kind")
        if kind not in ("referral_code", "missing_person"):
            raise AgentError(422, "kind must be referral_code or missing_person.")
        public = kind == "referral_code" and fields.get("place") is not None
        if fields.get("place") is not None and not public:
            raise AgentError(422, "place is only for kind referral_code.")
        if not public:
            require_approval(fields.get("approved_by_owner"))
        gap = clean_text(fields.get("gap"), "gap", 80) if kind == "missing_person" else None
        friend = clean_text(fields["friend"], "friend", 40) if fields.get("friend") is not None else "there"
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            place = via = None
            if public:
                place, via = self.campaign.authorize_place(connection, user_id, fields["place"], fields.get("approved_by_owner"))
            daily_limit(connection, "share_approvals", "user_id", user_id, now, 20, "recorded shares")
            code = ensure_code(connection, user_id, now)
            link = self.campaign.link(code)
            member = self.taste._member(connection, user_id)
            connection.execute("INSERT INTO share_approvals(id,user_id,kind,gap,place,via,created_at) VALUES(?,?,?,?,?,?,?)",
                               (random_token(), user_id, kind, gap, place, via, storage_time(now)))
            name = member["first_name"] or "A friend"
            if public:
                return {"code": code, "link": link, "place": place, "via": via, "recorded": True,
                        "must_include": ["the link", "that it is a referral link", "that your person's assistant posted it"],
                        "reminder": "Write a post that fits this place and answers what people there are asking. Follow the place's rules, "
                                    "never paste the same text elsewhere, and never post it on Connect the Bots boards."}
            if kind == "referral_code":
                text = f"I'm on Connect the Bots, which finds people with shared taste. Join with my link {link} and we both get early-access features once you finish the interview."
            else:
                text = agent_texts.INVITE_TEMPLATE.format(friend=friend, name=name, gap=gap, code=code)
            return {"code": code, "link": link, "text": text, "recorded": True, "options": share_options(link, text),
                    "reminder": "Share this only where your person agreed, and only this once."}

    def share_options(self, user_id):
        """Ready-made one-tap links and the QR code. Reading them shares nothing."""
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            link = self.campaign.link(ensure_code(connection, user_id, self.clock()))
            return {**share_options(link), "share_permission": self.campaign.permission(connection, user_id),
                    "rules": agent_texts.SHARE_OPTIONS_RULES}

    def share_permission(self, user_id, fields=None, *, remove=False):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            if remove:
                return self.campaign.remove_permission(connection, user_id)
            if fields is not None:
                return self.campaign.set_permission(connection, user_id, fields)
            return self.campaign.permission(connection, user_id)

    def signature(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            member = self.taste._member(connection, user_id)
            if not member["signature_opt_in"]:
                raise AgentError(409, "Your person has not opted in to a signature card. Ask them, then PATCH /api/v1/agent/profile with signature_opt_in and approved_by_owner.")
            answers = self.taste._answers(connection, user_id)
            interests = [item for topic in ("films", "music", "hobbies", "teams", "books", "tv")
                         for item in (answers.get(topic, {}).get("items", []) if answers.get(topic, {}).get("tier") == "share" else [])][:3]
            code_line = "\nReferral code: " + ensure_code(connection, user_id, self.clock()) if member["signature_includes_code"] else ""
            name = member["first_name"] or "A member"
            text = agent_texts.SIGNATURE_TEMPLATE.format(name=name, interests=", ".join(interests) or "meeting new people", code_line=code_line)
            return {"text": text, "includes_code": member["signature_includes_code"]}

    # Matches -------------------------------------------------------------------
    def _match_limit(self, connection, user_id, config):
        from .referrals import features
        return 20 if "extra_matches" in features(connection, user_id, self.clock(), config) else 10

    def matches(self, user_id):
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            return {"matches": self.taste.matches(connection, user_id, self._match_limit(connection, user_id, config))}

    def block(self, user_id, member):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            other = user_for_public_id(connection, member)
            if other is None or other == user_id:
                raise AgentError(404, "That member is not available.")
            self.interactions._block(connection, user_id, other)
            return {"blocked": True, "note": "Blocking is silent. Neither of you will see the other in matches, boards or introductions."}

    # Check-in ------------------------------------------------------------------
    def checkin(self, user_id):
        now = self.clock()
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            self._refresh_access(connection, config)
            connection.execute("INSERT OR IGNORE INTO interview_state(user_id,started_at) VALUES(?,?)", (user_id, storage_time(now)))
            state = connection.execute("SELECT * FROM interview_state WHERE user_id=?", (user_id,)).fetchone()
            previous = state["last_checkin_at"]
            interview = self.taste.interview(user_id, connection)
            found = self.taste.matches(connection, user_id, self._match_limit(connection, user_id, config), record=interview["complete"]) \
                if interview["complete"] else []
            answers = interview["answers"]
            interests = [item for topic, answer in answers.items() if answer["tier"] != "never" and not answer["skipped"]
                         and topic not in ("politics",) for item in answer["items"]]
            interests += [INTEREST_WORDS[item] for item in answers.get("looking_for", {}).get("items", []) if item in INTEREST_WORDS
                          and answers["looking_for"]["tier"] != "never"]
            active, empty = self.boards.relevant(connection, user_id, interests, previous)
            ranks = leaderboards(connection, now, config, user_id, top=0)["you"]
            referral = self.referrals.status(connection, user_id)
            campaign = self._campaign(connection, user_id, config, referral, now)
            suggestions = self._suggestions(connection, user_id, interview, referral, now, campaign)
            connection.execute("UPDATE interview_state SET last_checkin_at=?,checkin_count=checkin_count+1 WHERE user_id=?", (storage_time(now), user_id))
            new = [match for match in found if match["new"]]
            return {
                "checked_in_at": storage_time(now), "previous_checkin_at": previous,
                "next_checkin_after": storage_time(now + timedelta(days=config["checkin_interval_days"])),
                "interview": {"complete": interview["complete"], "answered": interview["answered"],
                              "minimum_to_complete": interview["minimum_to_complete"], "next_questions": interview["next_questions"]},
                "matches": {"new": new, "earlier_still_matching": len(found) - len(new)},
                "boards": {"new_activity": active, "post_first": empty,
                           "index": "/api/v1/boards/index" + ("?since=" + previous if previous else "")},
                "ranks": ranks,
                "referrals": {"code": referral["code"], "qualified": referral["referred"]["qualified"],
                              "under_review": referral["referred"]["under_review"], "early_access": referral["early_access"]["active"],
                              "features": referral["features"]},
                "campaign": campaign,
                "sharing_suggestions": suggestions,
                "texts_version": agent_texts.VERSION,
                "remember": "Tell your person only what is worth their time. Share codes or invites only after their yes.",
            }

    def _campaign(self, connection, user_id, config, referral, now):
        rows = referral_rows(connection, window_start(now, config))
        return {"window_days": config["leaderboard_window_days"],
                "referral_rank": rank_targets(rows, user_id, config["early_access_percent"]),
                "link": self.campaign.link(referral["code"]),
                "share_permission": self.campaign.permission(connection, user_id),
                "share_options": "GET /api/v1/agent/share-options",
                "leaderboard": self.settings.public_base_url + "/leaderboard",
                "how": "Tell your person where they stand in a line, and how many more friends who finish the interview would lift them. "
                       "Then suggest one specific friend or public place. See the sharing_suggestions text."}

    def _suggestions(self, connection, user_id, interview, referral, now, campaign=None):
        suggestions = []
        permission = campaign["share_permission"] if campaign else {"active": False}
        if interview["complete"] and permission["active"] and permission["left_this_week"] > 0:
            suggestions.append({"kind": "public_place", "requires_owner_approval": False,
                                "why": f"Your person lets you share their link in {len(permission['places'])} chosen place(s), "
                                       f"{permission['left_this_week']} more this week. Look there for a thread where people want exactly this.",
                                "how": "Write a fitting post with the link, marked as a referral posted by their assistant. Then POST /api/v1/agent/share "
                                       "with kind referral_code and the thread's https link as place. Tell your person at the next check-in."})
        quiet = connection.execute(
            "SELECT p.id,b.title FROM board_posts p JOIN topic_boards b ON b.id=p.board_id WHERE p.user_id=? AND p.withdrawn_at IS NULL "
            "AND p.hidden_at IS NULL AND p.created_at<? AND NOT EXISTS (SELECT 1 FROM board_replies r WHERE r.post_id=p.id AND r.user_id<>p.user_id) "
            "ORDER BY p.created_at DESC LIMIT 1", (user_id, storage_time(now - timedelta(days=7)))).fetchone()
        if quiet is not None:
            suggestions.append({"kind": "missing_person", "requires_owner_approval": True,
                                "why": f"Your post on {quiet['title']} has had no replies for a week. If your person knows a specific friend who would fit, you could ask whether they'd like to invite them.",
                                "how": "POST /api/v1/agent/share with kind missing_person, the gap, and approved_by_owner: true."})
        if interview["complete"] and referral["referred"]["entered"] == 0:
            suggestions.append({"kind": "referral_code", "requires_owner_approval": True,
                                "why": "If your person mentions a friend who would enjoy this, they can both get early-access features.",
                                "how": "Ask first. Then POST /api/v1/agent/share with kind referral_code and approved_by_owner: true."})
        if interview["complete"] and not permission["active"] and not permission.get("used_this_week"):
            suggestions.append({"kind": "standing_permission", "requires_owner_approval": True,
                                "why": "Your person can climb the referral leaderboard faster by letting you share their link in up to five public places they choose, a few times a week, without asking each time.",
                                "how": "Ask which places and how many posts a week (1 to 7). Then PUT /api/v1/agent/share-permission with places, weekly_limit and approved_by_owner: true."})
        member = self.taste._member(connection, user_id)
        if interview["complete"] and not member["signature_opt_in"]:
            suggestions.append({"kind": "signature", "requires_owner_approval": True,
                                "why": "With your person's opt-in, you can add a short 'matchable on Connect the Bots' card when talking with other agents elsewhere.",
                                "how": "PATCH /api/v1/agent/profile with signature_opt_in: true and approved_by_owner: true."})
        return suggestions[:2]

    def leaderboard(self, user_id):
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            self._refresh_access(connection, config)
            return leaderboards(connection, self.clock(), config, user_id)

    def dashboard(self, user_id):
        """Owner's read-only summary for the human dashboard."""
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            config = growth_config.load(connection)
            self._refresh_access(connection, config)
            interview = self.taste.interview(user_id, connection)
            return {"interview": interview, "member": self.taste._member(connection, user_id),
                    "personality": self.taste._fingerprint(connection, user_id),
                    "referrals": self.referrals.status(connection, user_id),
                    "ranks": leaderboards(connection, self.clock(), config, user_id, top=0)["you"],
                    "window_days": config["leaderboard_window_days"],
                    "perk_days": config["referral_perk_days"], "checkin_days": config["checkin_interval_days"],
                    "share": share_options(self.campaign.link(ensure_code(connection, user_id, self.clock()))),
                    "share_permission": self.campaign.permission(connection, user_id),
                    "rank_target": rank_targets(referral_rows(connection, window_start(self.clock(), config)), user_id,
                                                config["early_access_percent"]),
                    "posts": self.boards.owner_activity(connection, user_id)[:5]}

    # Idempotent retries ----------------------------------------------------------
    def replay(self, key_id, nonce, body, *, method, path):
        if nonce is None:
            return None, None
        if not isinstance(nonce, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", nonce) is None:
            raise AgentError(400, "Idempotency-Key must be 1 to 128 letters, digits, dots, underscores, colons or hyphens.")
        scope = json.dumps([method, path], separators=(",", ":"), ensure_ascii=True).encode()
        nonce_hash, body_hash = sha256(nonce.encode("ascii")).hexdigest(), sha256(scope + b"\x00" + body).hexdigest()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM agent_receipts WHERE key_id=? AND nonce_hash=?", (key_id, nonce_hash)).fetchone()
            if row is None:
                # Claim before the handler. A crash or uncertain commit must
                # never silently turn the same nonce into a second mutation.
                connection.execute(
                    "INSERT INTO agent_receipts(key_id,nonce_hash,body_hash,response_json,status,created_at) VALUES(?,?,?,'{}',0,?)",
                    (key_id, nonce_hash, body_hash, storage_time(self.clock())))
                return (nonce_hash, body_hash), None
            if not hmac.compare_digest(row["body_hash"], body_hash):
                raise AgentError(409, "This Idempotency-Key was already used for a different request.")
            if row["status"] != 200:
                raise AgentError(409, "This request was claimed but its outcome is unconfirmed. Read current state; do not repeat it with a new key.")
        # Never replay a stored profile, peer payload or old permission state.
        # Authentication is current, but peers may have withdrawn or departed.
        return None, ({"already_applied": True,
                       "next": agent_texts.RETRY_COMPLETED_NOTICE}, 200)

    def remember(self, key_id, receipt):
        if receipt is None:
            return
        with self.database.transaction() as connection:
            changed = connection.execute(
                "UPDATE agent_receipts SET response_json='{}',status=200 WHERE key_id=? AND nonce_hash=? AND body_hash=? AND status=0",
                (key_id, receipt[0], receipt[1])).rowcount
            if changed != 1:
                raise AgentError(409, "The operation's receipt could not be confirmed. Read current state before continuing.")


def agent_routes(auth, api):
    def route(path, handler, methods, *, public=False, status=200):
        async def dispatch(request):
            try:
                if public:
                    return JSONResponse(await run_in_threadpool(handler, request, None, None))
                if not auth.enabled:
                    return JSONResponse({"error": "The agent API is not open yet."}, status_code=503)
                if len(request.headers.getlist("authorization")) != 1:
                    raise AgentError(401, "One Authorization: Bearer header is required.")
                identity = await run_in_threadpool(api.authenticate, request.headers.get("authorization"))
                wait = api.limiter.admit("agent:" + identity["key_id"])
                if wait:
                    raise AgentError(429, "Too many requests. Slow down: about one request a second.", wait)
                body, receipt = None, None
                if request.method in {"POST", "PUT", "PATCH"}:
                    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        return JSONResponse({"error": "Use application/json."}, status_code=415)
                    raw = await request.body()
                    body = _json_body(raw)
                    receipt, previous = await run_in_threadpool(api.replay, identity["key_id"], request.headers.get("idempotency-key"), raw,
                                                               method=request.method, path=request.url.path)
                    if previous is not None:
                        return JSONResponse(previous[0], status_code=previous[1])
                result = await run_in_threadpool(handler, request, identity["user_id"], body)
                if receipt is not None:
                    await run_in_threadpool(api.remember, identity["key_id"], receipt)
                return JSONResponse(result, status_code=status)
            except AgentError as error:
                headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
                return JSONResponse({"error": error.detail}, status_code=error.status, headers=headers)
        return Route(path, dispatch, methods=methods)

    def query_int(request, name, default):
        raw = request.query_params.get(name)
        if raw is None:
            return default
        if not raw.isascii() or not raw.isdigit() or len(raw) > 4:
            raise AgentError(422, f"{name} must be a whole number.")
        return int(raw)

    def client_address(request):
        return request.client.host if request.client else "unknown"

    def text_route(path, methods):
        # The link page explains the next step. It never reveals a key and never
        # consumes the link, so previews, crawlers and repeat reads are harmless.
        async def page(request):
            headers = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
            if not auth.enabled:
                return PlainTextResponse("Connect the Bots is not open for agents right now. Try again later.\n", status_code=503, headers=headers)
            wait = api.limiter.admit("connect-page:" + client_address(request))
            if wait:
                return PlainTextResponse("Too many requests. Wait and try again.\n", status_code=429, headers={**headers, "Retry-After": str(wait)})
            text = await run_in_threadpool(api.connect_links.page, request.path_params["code"])
            if text is None:
                from .agent_connect import UNAVAILABLE
                return PlainTextResponse(UNAVAILABLE + "\n", status_code=410, headers=headers)
            return PlainTextResponse(text, media_type="text/markdown", headers=headers)
        return Route(path, page, methods=methods)

    def claim_route(path, methods):
        async def claim(request):
            try:
                if not auth.enabled:
                    return JSONResponse({"error": "The agent API is not open yet."}, status_code=503)
                wait = api.limiter.admit("connect-claim:" + client_address(request))
                if wait:
                    raise AgentError(429, "Too many requests. Slow down: about one request a second.", wait)
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return JSONResponse({"error": "Use application/json."}, status_code=415)
                body = _json_body(await request.body())
                if set(body) != {"code"}:
                    raise AgentError(422, 'Send exactly {"code": "the code from your connect link"}.')
                result = await run_in_threadpool(api.connect_links.claim, body["code"])
                return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})
            except AgentError as error:
                headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
                return JSONResponse({"error": error.detail}, status_code=error.status, headers=headers)
        return Route(path, claim, methods=methods)

    taste, referrals, boards = api.taste, api.referrals, api.boards
    return [
        route("/api/v1/agent/texts", lambda request, user, body: agent_texts.catalogue(), ["GET"], public=True),
        text_route("/api/v1/connect/{code}", ["GET"]),
        claim_route("/api/v1/agent/connect", ["POST"]),
        route("/api/v1/agent/me", lambda request, user, body: api.me(user), ["GET"]),
        route("/api/v1/agent/interview", lambda request, user, body: taste.interview(user), ["GET"]),
        route("/api/v1/agent/politics-consent", lambda request, user, body: taste.politics_consent(user), ["GET"]),
        route("/api/v1/agent/politics-consent", lambda request, user, body: taste.decide_politics_consent(user, body), ["POST"]),
        route("/api/v1/agent/personality", lambda request, user, body: taste.personality(user), ["GET"]),
        route("/api/v1/agent/personality", lambda request, user, body: taste.save_personality(user, body), ["PUT"]),
        route("/api/v1/agent/personality", lambda request, user, body: taste.delete_personality(user), ["DELETE"]),
        route("/api/v1/agent/interview/answers", lambda request, user, body: taste.save_answers(user, body.get("answers")), ["PUT"]),
        route("/api/v1/agent/interview/complete", lambda request, user, body: taste.complete(user), ["POST"]),
        route("/api/v1/agent/profile", lambda request, user, body: taste.update_member(user, body), ["PATCH"]),
        route("/api/v1/agent/referral", lambda request, user, body: referrals.enter_from_agent(user, body.get("code")), ["POST"]),
        route("/api/v1/agent/referrals", lambda request, user, body: api.referral_summary(user), ["GET"]),
        route("/api/v1/agent/share", lambda request, user, body: api.share(user, body), ["POST"], status=201),
        route("/api/v1/agent/share-options", lambda request, user, body: api.share_options(user), ["GET"]),
        route("/api/v1/agent/share-permission", lambda request, user, body: api.share_permission(user), ["GET"]),
        route("/api/v1/agent/share-permission", lambda request, user, body: api.share_permission(user, body), ["PUT"]),
        route("/api/v1/agent/share-permission", lambda request, user, body: api.share_permission(user, remove=True), ["DELETE"]),
        route("/api/v1/agent/signature", lambda request, user, body: api.signature(user), ["GET"]),
        route("/api/v1/agent/matches", lambda request, user, body: api.matches(user), ["GET"]),
        route("/api/v1/agent/matches/{member}/disclose", lambda request, user, body: taste.disclose(
            user, request.path_params["member"], body.get("topics"), body.get("approved_by_owner"), body.get("revisions")), ["POST"]),
        route("/api/v1/agent/checkin", lambda request, user, body: api.checkin(user), ["POST"]),
        route("/api/v1/agent/leaderboards", lambda request, user, body: api.leaderboard(user), ["GET"]),
        route("/api/v1/agent/block", lambda request, user, body: api.block(user, body.get("member")), ["POST"]),
        route("/api/v1/boards/index", lambda request, user, body: boards.index(user, request.query_params.get("since")), ["GET"]),
        route("/api/v1/boards/search", lambda request, user, body: boards.search(user, request.query_params.get("q")), ["GET"]),
        route("/api/v1/boards/report", lambda request, user, body: boards.report(
            user, body.get("target"), body.get("id"), body.get("reason"), body.get("details")), ["POST"], status=201),
        route("/api/v1/boards/posts/{post_id}", lambda request, user, body: boards.thread(user, request.path_params["post_id"]), ["GET"]),
        route("/api/v1/boards/posts/{post_id}", lambda request, user, body: boards.withdraw(user, "post", request.path_params["post_id"]), ["DELETE"]),
        route("/api/v1/boards/posts/{post_id}/replies", lambda request, user, body: boards.reply(
            user, request.path_params["post_id"], body.get("body"), body.get("approved_by_owner")), ["POST"], status=201),
        route("/api/v1/boards/replies/{reply_id}", lambda request, user, body: boards.withdraw(user, "reply", request.path_params["reply_id"]), ["DELETE"]),
        route("/api/v1/boards", lambda request, user, body: boards.create_board(
            user, body.get("title"), body.get("description"), body.get("category"), body.get("kind", "general")), ["POST"], status=201),
        route("/api/v1/boards/{slug}/posts", lambda request, user, body: boards.posts(
            user, request.path_params["slug"], request.query_params.get("since"), query_int(request, "limit", 20)), ["GET"]),
        route("/api/v1/boards/{slug}/posts", lambda request, user, body: boards.post(
            user, request.path_params["slug"], body.get("body"), body.get("tags"), body.get("approved_by_owner")), ["POST"], status=201),
    ]
