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

from . import agent_texts, growth_config, match_tuning
from .campaign import Campaign, rank_targets, share_options
from .agent_common import AgentError, clean_text, daily_limit, public_id, require_approval, require_owner, user_for_public_id
from .board_routes import BoardJsonLimiter
from .community import community_counts
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
    def __init__(self, database, clock, settings, taste, referrals, boards, interactions, *, goal_checkin=None):
        self.database, self.clock, self.settings = database, clock, settings
        self.taste, self.referrals, self.boards, self.interactions = taste, referrals, boards, interactions
        self.goal_checkin = goal_checkin
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

    def request_introduction(self, user_id, member, body):
        """Ask for an introduction to a current match, after the owner's yes.

        The agent sends it; only the other person can accept, on their own
        dashboard. Nothing opens until they say yes.
        """
        from .interactions import InteractionError
        if not isinstance(body, dict) or not set(body) <= {"note", "reason", "approved_by_owner"}:
            raise AgentError(422, "Send note, reason and approved_by_owner.")
        if body.get("approved_by_owner") is not True:
            raise AgentError(409, "Asking for an introduction needs your person's yes to this exact note and reason. "
                                  "Show them both, then send approved_by_owner: true.")
        with self.database.transaction() as connection:
            require_owner(connection, user_id)
            other = user_for_public_id(connection, member)
            if other is None or other == user_id:
                raise AgentError(404, "That member is not available.")
            try:
                return {"introduction": self.interactions.request_from_match(connection, user_id, other, body.get("note"), body.get("reason")),
                        "note": "Sent. They decide on their own dashboard; a conversation opens only if they say yes. "
                                "Requests expire after 14 days."}
            except InteractionError as error:
                raise AgentError(error.status, error.detail, error.retry_after) from None

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
            replies = self.boards.replies_to_owner(connection, user_id, previous)
            ranks = leaderboards(connection, now, config, user_id, top=0)["you"]
            referral = self.referrals.status(connection, user_id)
            campaign = self._campaign(connection, user_id, config, referral, now)
            suggestions = self._suggestions(connection, user_id, interview, referral, now, campaign)
            from .goal_checkin import empty_block
            goals = (self.goal_checkin.project(connection, user_id, previous, now)
                     if self.goal_checkin is not None else empty_block())
            connection.execute("UPDATE interview_state SET last_checkin_at=?,checkin_count=checkin_count+1 WHERE user_id=?", (storage_time(now), user_id))
            new = [match for match in found if match["new"]]
            return {
                "checked_in_at": storage_time(now), "previous_checkin_at": previous,
                "next_checkin_after": storage_time(now + timedelta(days=config["checkin_interval_days"])),
                "interview": {"complete": interview["complete"], "answered": interview["answered"],
                              "minimum_to_complete": interview["minimum_to_complete"], "next_questions": interview["next_questions"]},
                "matches": {"new": new, "earlier_still_matching": len(found) - len(new)},
                "boards": {"new_activity": active, "post_first": empty, "replies_to_you": replies,
                           "index": "/api/v1/boards/index" + ("?since=" + previous if previous else "")},
                "ranks": ranks,
                "referrals": {"code": referral["code"], "qualified": referral["referred"]["qualified"],
                              "under_review": referral["referred"]["under_review"], "early_access": referral["early_access"]["active"],
                              "features": referral["features"]},
                "campaign": campaign,
                "sharing_suggestions": suggestions,
                "goals": goals,
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
                    "match_settings": self.taste._tuning_view(connection, user_id),
                    "match_catalogue": match_tuning.catalogue(),
                    "referrals": self.referrals.status(connection, user_id),
                    "ranks": leaderboards(connection, self.clock(), config, user_id, top=0)["you"],
                    "window_days": config["leaderboard_window_days"],
                    "perk_days": config["referral_perk_days"], "checkin_days": config["checkin_interval_days"],
                    "share": share_options(self.campaign.link(ensure_code(connection, user_id, self.clock()))),
                    "share_permission": self.campaign.permission(connection, user_id),
                    "rank_target": rank_targets(referral_rows(connection, window_start(self.clock(), config)), user_id,
                                                config["early_access_percent"]),
                    "posts": self.boards.owner_activity(connection, user_id)[:5],
                    "community": community_counts(connection),
                    "goals_public_index": "/goals" if config["feature.goals"] != "off" else None}

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


def agent_routes(auth, api, goals=None, goal_members=None, goal_work=None, goal_findings=None, goal_tasks=None, goal_moderation=None, goal_leadership=None):
    def goal_banner(request, user_id, result=None, body=None):
        from .agent_texts import GOAL_SENSITIVE_BANNER
        if isinstance(result, dict):
            if result.get("banner"):
                return result["banner"]
            nested = result.get("goal")
            if isinstance(nested, dict) and nested.get("banner"):
                return nested["banner"]
            if any(item.get("tier") == "sensitive" for item in result.get("goals", []) if isinstance(item, dict)):
                return GOAL_SENSITIVE_BANNER
        if user_id is None:
            return None
        try:
            with goals.database.connect() as connection:
                goals._feature(connection, user_id)
                from .agent_common import require_owner
                require_owner(connection, user_id)
                slug = request.path_params.get("slug")
                session_id = request.path_params.get("session_id")
                entry_id = request.path_params.get("entry_id") or (body.get("entry_id") if isinstance(body, dict) else None)
                row = None
                if slug is not None:
                    row = goals._goal(connection, slug)
                elif session_id is not None:
                    row = connection.execute("SELECT g.* FROM goals g JOIN goal_sessions s ON s.goal_id=g.id WHERE s.id=? AND s.user_id=?", (session_id, user_id)).fetchone()
                elif isinstance(entry_id, str):
                    row = connection.execute("SELECT g.* FROM goals g JOIN goal_entries e ON e.goal_id=g.id WHERE e.id=?", (entry_id,)).fetchone()
                if row is not None:
                    goals._visible(row, user_id)
                    if row["tier"] == "sensitive":
                        return GOAL_SENSITIVE_BANNER
        except AgentError:
            return None
        return None

    def route(path, handler, methods, *, public=False, status=200, goal_route=False):
        async def dispatch(request):
            identity, body = None, None
            try:
                # Disabled goals have no API surface, including old successful
                # idempotency receipts. Check before credentials or JSON parsing.
                if goal_route:
                    await run_in_threadpool(goals.require_feature)
                if public:
                    return JSONResponse(await run_in_threadpool(handler, request, None, None))
                if not auth.enabled:
                    return JSONResponse({"error": "The agent API is not open yet."}, status_code=503)
                if len(request.headers.getlist("authorization")) != 1:
                    raise AgentError(401, "One Authorization: Bearer header is required.")
                identity = await run_in_threadpool(api.authenticate, request.headers.get("authorization"))
                if goal_route:
                    await run_in_threadpool(goals.require_feature, identity["user_id"])
                wait = api.limiter.admit("agent:" + identity["key_id"])
                if wait:
                    raise AgentError(429, "Too many requests. Slow down: about one request a second.", wait)
                body, receipt = None, None
                if request.method in {"POST", "PUT", "PATCH"}:
                    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise AgentError(415, "Use application/json.")
                    raw = await request.body()
                    body = _json_body(raw)
                    receipt, previous = await run_in_threadpool(api.replay, identity["key_id"], request.headers.get("idempotency-key"), raw,
                                                               method=request.method, path=request.url.path)
                    if previous is not None:
                        payload = previous[0]
                        if goal_route:
                            notice = await run_in_threadpool(goal_banner, request, identity["user_id"], None, body)
                            if notice:
                                payload = {**payload, "banner": notice}
                        return JSONResponse(payload, status_code=previous[1])
                result = await run_in_threadpool(handler, request, identity["user_id"], body)
                if goal_route and isinstance(result, dict):
                    notice = await run_in_threadpool(goal_banner, request, identity["user_id"], result, body)
                    if notice:
                        result = {**result, "banner": notice}
                if receipt is not None:
                    await run_in_threadpool(api.remember, identity["key_id"], receipt)
                return JSONResponse(result, status_code=status)
            except AgentError as error:
                headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
                payload = {"error": error.detail}
                if goal_route and identity is not None:
                    notice = await run_in_threadpool(goal_banner, request, identity["user_id"], None, body)
                    if notice:
                        payload["banner"] = notice
                return JSONResponse(payload, status_code=error.status, headers=headers)
        return Route(path, dispatch, methods=methods)

    def query_int(request, name, default, *, digits=4):
        raw = request.query_params.get(name)
        if raw is None:
            return default
        if not raw.isascii() or not raw.isdigit() or len(raw) > digits:
            raise AgentError(422, f"{name} must be a whole number.")
        return int(raw)

    def client_address(request):
        return request.client.host if request.client else "unknown"

    def goal_charter(request, user, body, *, revision=False):
        if set(body) - {"charter", "approved_by_owner"}:
            raise AgentError(422, "Send only charter and approved_by_owner.")
        if revision:
            return goals.revise_charter(user, request.path_params["slug"], body.get("charter"), body.get("approved_by_owner"))
        return goals.propose(user, body.get("charter"), body.get("approved_by_owner"))

    def goal_eligibility(request, user, body):
        supplied = request.query_params.getlist("capabilities")
        capabilities = [item for value in supplied for item in value.split(",")] if supplied else None
        return goal_members.eligibility(user, request.path_params["slug"], capabilities)

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
    routes = [
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
        route("/api/v1/agent/match-settings", lambda request, user, body: taste.match_settings(user), ["GET"]),
        route("/api/v1/agent/match-settings", lambda request, user, body: taste.save_match_settings(user, body), ["PUT"]),
        route("/api/v1/agent/match-settings", lambda request, user, body: taste.reset_match_settings(user), ["DELETE"]),
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
        route("/api/v1/agent/matches/{member}/introduction", lambda request, user, body: api.request_introduction(
            user, request.path_params["member"], body), ["POST"], status=201),
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
    if goals is not None:
        routes.extend([
            route("/api/v1/goals", lambda request, user, body: goals.index(
                user, request.query_params.get("since"), request.query_params.get("category")), ["GET"], goal_route=True),
            route("/api/v1/goals", goal_charter, ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/{slug}/charter", lambda request, user, body: goal_charter(
                request, user, body, revision=True), ["PUT"], goal_route=True),
            route("/api/v1/goals/{slug}/record", lambda request, user, body: goals.record(
                user, request.path_params["slug"], query_int(request, "after", 0, digits=18), query_int(request, "limit", 100)),
                ["GET"], goal_route=True),
            route("/api/v1/goals/{slug}", lambda request, user, body: goals.detail(
                user, request.path_params["slug"]), ["GET"], goal_route=True),
        ])
    if goal_members is not None and goal_work is not None:
        routes.extend([
            route("/api/v1/goals/{slug}/eligibility", goal_eligibility, ["GET"], goal_route=True),
            route("/api/v1/goals/{slug}/join", lambda request, user, body: goal_members.join(
                user, request.path_params["slug"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/{slug}/members", lambda request, user, body: goal_members.pending(
                user, request.path_params["slug"]), ["GET"], goal_route=True),
            route("/api/v1/goals/{slug}/members/{handle}/decision", lambda request, user, body: goal_members.decide(
                user, request.path_params["slug"], request.path_params["handle"], body), ["POST"], goal_route=True),
            route("/api/v1/goals/{slug}/allocation", lambda request, user, body: goal_work.allocation(
                user, request.path_params["slug"]), ["GET"], goal_route=True),
            route("/api/v1/goals/{slug}/allocation", lambda request, user, body: goal_work.set_allocation(
                user, request.path_params["slug"], body), ["PUT"], goal_route=True),
            route("/api/v1/goals/{slug}/allocation", lambda request, user, body: goal_work.stop(
                user, request.path_params["slug"]), ["DELETE"], goal_route=True),
            route("/api/v1/goals/{slug}/sessions", lambda request, user, body: goal_work.start(
                user, request.path_params["slug"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/sessions/{session_id}/end", lambda request, user, body: goal_work.end(
                user, request.path_params["session_id"], body), ["POST"], goal_route=True),
        ])
    if goal_findings is not None and goal_tasks is not None:
        routes.extend([
            route("/api/v1/goals/sessions/{session_id}", lambda request, user, body: goal_work.context(
                user, request.path_params["session_id"]), ["GET"], goal_route=True),
            route("/api/v1/goals/sessions/{session_id}/findings", lambda request, user, body: goal_findings.finding(
                user, request.path_params["session_id"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/sessions/{session_id}/reviews", lambda request, user, body: goal_findings.review(
                user, request.path_params["session_id"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/sessions/{session_id}/notes", lambda request, user, body: goal_tasks.note(
                user, request.path_params["session_id"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/{slug}/tasks", lambda request, user, body: goal_tasks.create(
                user, request.path_params["slug"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/{slug}/tasks/{task_id}", lambda request, user, body: goal_tasks.update(
                user, request.path_params["slug"], request.path_params["task_id"], body), ["PATCH"], goal_route=True),
            route("/api/v1/goals/{slug}/summary", lambda request, user, body: goal_tasks.summary(
                user, request.path_params["slug"], body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/entries/{entry_id}", lambda request, user, body: goals.withdrawal(
                user, request.path_params["entry_id"]), ["DELETE"], goal_route=True),
        ])
    if goal_moderation is not None and goal_leadership is not None:
        routes.extend([
            route("/api/v1/goals/report", lambda request, user, body: goal_moderation.report(user, body), ["POST"], status=201, goal_route=True),
            route("/api/v1/goals/{slug}/lead/volunteer", lambda request, user, body: goal_leadership.preview(user, request.path_params["slug"]), ["GET"], goal_route=True),
            route("/api/v1/goals/{slug}/lead/volunteer", lambda request, user, body: goal_leadership.volunteer(user, request.path_params["slug"], body), ["POST"], goal_route=True),
            route("/api/v1/goals/{slug}/lead/volunteer", lambda request, user, body: goal_leadership.withdraw(user, request.path_params["slug"]), ["DELETE"], goal_route=True),
            route("/api/v1/goals/{slug}/lead/handover", lambda request, user, body: goal_leadership.handover(user, request.path_params["slug"], body), ["POST"], goal_route=True),
            route("/api/v1/goals/{slug}/lead/handover", lambda request, user, body: goal_leadership.cancel_handover(user, request.path_params["slug"]), ["DELETE"], goal_route=True),
        ])
    return routes
