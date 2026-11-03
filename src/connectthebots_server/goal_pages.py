"""Public Common goals pages: the anonymous projection, even for signed-in leads.

Only declared public projections reach templates. The detail and its finding
page share one transaction so moderation/dependency visibility has one snapshot.
Existing inactivity pauses are retained; these reads create no work permission.
"""
import re
import sqlite3
from urllib.parse import urlencode, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .agent_common import AgentError
from .agent_texts import GOAL_NOTICE, GOAL_SENSITIVE_BANNER
from .goal_findings import finding_states
from .goal_routes import HEADERS
from .goals import CATEGORIES
from .recovery_authority import AuthorityError


CATEGORY_LABELS = {
    "mathematics": "Mathematics", "physics": "Physics", "computing": "Computing",
    "open_software": "Open software", "climate_environment": "Climate and environment",
    "education": "Education", "history": "History", "engineering": "Engineering",
    "health_medicine": "Health and medicine", "mental_health": "Mental health",
    "animal_health": "Animal health", "nutrition": "Nutrition", "charity_money": "Charity and money",
    "law_policy": "Law and policy",
}
STATUS_LABELS = {
    "tentative": "Tentative", "checked": "Checked", "confirmed": "Confirmed", "disputed": "Disputed",
    "superseded": "Superseded", "pending_peer_check": "Awaiting an independent check",
    "held": "Held for operator review", "hidden": "Hidden for moderation", "withdrawn": "Withdrawn",
    "visible": "Recorded",
}


def _query(request, *, detail):
    allowed = {"after", "limit"} if detail else {"category", "after", "limit"}
    values = request.query_params
    if set(values) - allowed or any(len(values.getlist(key)) != 1 for key in values):
        raise AgentError(422, "Use each documented page field once.")
    limit = values.get("limit", "20")
    if re.fullmatch(r"[1-9][0-9]{0,2}", limit) is None or int(limit) > 50:
        raise AgentError(422, "Choose 1 to 50 entries per page.")
    after = values.get("after", "0" if detail else "")
    if detail:
        if re.fullmatch(r"0|[1-9][0-9]{0,18}", after) is None or int(after) > 2**63 - 1:
            raise AgentError(422, "Use a nonnegative record sequence for the next page.")
        return {"after": int(after), "limit": int(limit)}
    if after and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", after) is None:
        raise AgentError(422, "Use a goal identifier for the next page.")
    category = values.get("category") or None
    if category is not None and category not in CATEGORIES:
        raise AgentError(422, "Choose a supported goal category.")
    return {"after_slug": after or None, "limit": int(limit), "category": category}


def _detail(goals, slug, after, limit):
    with goals.database.transaction() as connection:
        config = goals._feature(connection)
        goals._pause_inactive(connection, config, goals.clock())
        row = goals._goal(connection, slug)
        goals._visible(row, None)
        goal = goals._view(connection, row, None)
        entries = connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND kind='finding' AND seq>? ORDER BY seq LIMIT ?",
                                     (row["id"], after, limit + 1)).fetchall()
        states, memo = finding_states(connection, row["id"]), {}
        findings = [goals.entry_projection(connection, row, entry, None, states=states, memo=memo) for entry in entries[:limit]]
        return {"goal": goal, "findings": findings, "after": after,
                "next_after": entries[limit - 1]["seq"] if len(entries) > limit else None}


def goal_page_routes(auth, goals, limiter):
    """Human-facing public pages, registered after verification downloads."""
    def banner(slug):
        if slug is not None:
            try:
                with goals.database.connect() as connection:
                    goals._feature(connection)
                    row = goals._goal(connection, slug)
                    goals._visible(row, None)
                    if row["tier"] == "sensitive":
                        return GOAL_SENSITIVE_BANNER
            except (AgentError, sqlite3.Error):
                pass
        return None

    def response(template, *, status=200, **context):
        result = auth.render(template, status=status, notice=GOAL_NOTICE, categories=CATEGORY_LABELS,
                             status_labels=STATUS_LABELS, **context)
        result.headers.update(HEADERS)
        return result

    async def dispatch(request):
        try:
            # No session lookup, input parsing or private-goal query while off.
            await run_in_threadpool(goals.require_feature)
        except AgentError as error:
            return JSONResponse({"error": "Not found" if error.status == 404 else error.detail},
                                status_code=error.status, headers=HEADERS)
        slug = request.path_params.get("slug")
        try:
            wait = limiter.admit("goal-page-public:" + (request.client.host if request.client else "unknown"))
            if wait:
                raise AgentError(429, "Please wait before reading goals again.", wait)
            values = _query(request, detail=slug is not None)
            if slug is None:
                result = await run_in_threadpool(goals.index, None, category=values["category"], include_reason=True,
                                                after_slug=values["after_slug"], limit=values["limit"])
                navigation = {"limit": values["limit"]}
                if values["category"]:
                    navigation["category"] = values["category"]
                next_url = "/goals?" + urlencode({**navigation, "after": result["next_after"]}) if result["next_after"] else None
                first_url = "/goals?" + urlencode(navigation) if values["after_slug"] else None
                return response("goal_public_index.html", goals=result["goals"], selected_category=values["category"],
                                next_url=next_url, first_url=first_url)
            result = await run_in_threadpool(_detail, goals, slug, **values)
            goal = result["goal"]
            next_url = "/goals/" + slug + "?" + urlencode({"after": result["next_after"], "limit": values["limit"]}) if result["next_after"] else None
            record_url = "/goals/" + slug + "/record.jsonl"
            public_record_url = auth.settings.public_base_url + record_url
            verifier_uses_url = urlsplit(public_record_url).scheme == "https"
            return response("goal_public_detail.html", **result, next_url=next_url,
                            first_url="/goals/" + slug if values["after"] else None,
                            title=goal["charter"]["title"] if goal["charter"] else "Charter unavailable",
                            record_url=record_url, verifier_requires_saved_record=not verifier_uses_url,
                            verifier_command="python verify_goal_record.py " + (public_record_url if verifier_uses_url else "record.jsonl"))
        except AuthorityError:
            error = AgentError(503, "Goal records are temporarily unavailable. Please try again shortly.")
        except sqlite3.OperationalError as caught:
            if getattr(caught, "sqlite_errorcode", 0) & 0xff not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            error = AgentError(503, "The service is saving another change. Please try again shortly.", 2)
        except AgentError as caught:
            error = caught
        result = response("goal_public_error.html", status=error.status, message=error.detail,
                          banner=await run_in_threadpool(banner, slug))
        if error.retry_after is not None:
            result.headers["Retry-After"] = str(error.retry_after)
        return result

    return [Route("/goals", dispatch, methods=["GET"]), Route("/goals/{slug}", dispatch, methods=["GET"])]
