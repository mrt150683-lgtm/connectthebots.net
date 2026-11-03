"""Public verification downloads, using the same current record projections."""
from .agent_common import AgentError
from .goal_record import canonical_json
from importlib import resources

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def goal_routes(goals, limiter):
    def public_banner(slug):
        if slug is None:
            return None
        try:
            with goals.database.connect() as connection:
                goals._feature(connection)
                row = goals._goal(connection, slug)
                goals._visible(row, None)
                if row["tier"] == "sensitive":
                    from .agent_texts import GOAL_SENSITIVE_BANNER
                    return GOAL_SENSITIVE_BANNER
        except AgentError:
            return None
        return None

    async def dispatch(request):
        try:
            # Default-off downloads have no surface, including malformed queries.
            await run_in_threadpool(goals.require_feature)
            address = request.client.host if request.client else "unknown"
            wait = limiter.admit("goal-record-public:" + address)
            if wait:
                raise AgentError(429, "Too many record requests. Try again shortly.", wait)
            if request.url.path == "/goals/verify_goal_record.py":
                if request.query_params:
                    raise AgentError(422, "The verifier download does not take query fields.")
                content = await run_in_threadpool(
                    resources.files("connectthebots_server").joinpath("goal_verifier.py").read_bytes)
                return Response(content, media_type="text/x-python", headers={
                    **HEADERS, "Content-Disposition": 'attachment; filename="verify_goal_record.py"'})
            if request.url.path == "/goals/heads.json":
                if request.query_params:
                    raise AgentError(422, "heads.json does not take query fields.")
                return JSONResponse(await run_in_threadpool(goals.heads), headers=HEADERS)
            values = {"after": 0, "limit": 100}
            if set(request.query_params) - set(values):
                raise AgentError(422, "Use only after and limit for a record page.")
            for field in values:
                supplied = request.query_params.getlist(field)
                if supplied:
                    value = supplied[0]
                    if len(supplied) != 1 or not value.isascii() or not value.isdigit() or len(value) > 19:
                        raise AgentError(422, field + " must be a whole number.")
                    values[field] = int(value)
            result = await run_in_threadpool(goals.public_record, request.path_params["slug"], **values)
            headers = {**HEADERS, "X-Record-Next-After": str(result["next_after"])}
            if result.get("banner"):
                headers["X-Goal-Banner"] = result["banner"]
            if len(result["entries"]) == values["limit"]:
                next_url = request.url.replace(query=f"after={result['next_after']}&limit={values['limit']}")
                headers["Link"] = f'<{next_url}>; rel="next"'
            body = "".join(canonical_json(entry) + "\n" for entry in result["entries"])
            return Response(body, media_type="application/x-ndjson", headers=headers)
        except AgentError as error:
            headers = {**HEADERS, **({"Retry-After": str(error.retry_after)} if error.retry_after is not None else {})}
            payload = {"error": error.detail}
            banner = await run_in_threadpool(public_banner, request.path_params.get("slug"))
            if banner:
                payload["banner"] = banner
            return JSONResponse(payload, status_code=error.status, headers=headers)

    return [Route("/goals/verify_goal_record.py", dispatch, methods=["GET"]),
            Route("/goals/heads.json", dispatch, methods=["GET"]),
            Route("/goals/{slug}/record.jsonl", dispatch, methods=["GET"])]
