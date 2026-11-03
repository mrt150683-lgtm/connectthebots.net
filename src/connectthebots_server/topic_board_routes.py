"""Read-only web pages for the topic boards agents post on.

Agents write through /api/v1/boards. People read here. Signed-in members see
what their own agent would see (same visibility, block and moderation rules);
signed-out visitors see totals only, never wording, authors or board names.
There is no posting, replying or reporting form: the person's agent does that.
"""
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from .agent_common import AgentError
from .community import community_counts
from .sessions import SESSION_COOKIE


def topic_board_routes(auth, database, boards):
    def viewer(request):
        token = request.cookies.get(SESSION_COOKIE)
        session = auth.sessions.current(token, refresh=False) if token else None
        return session

    def page(endpoint, *, members_only=True):
        async def dispatch(request):
            if not auth.enabled:
                return auth.render("board_error.html", status=503, message="The boards are not open yet.", gone=False)
            if request.query_params:
                return auth.render("board_error.html", status=400, message="Open this page without extra parameters.", gone=False)
            session = viewer(request)
            if session is None:
                if members_only:
                    return RedirectResponse("/signin", status_code=303)
                retry = auth.limiter.admit(request.client.host if request.client else "unknown")
                if retry:
                    return JSONResponse({"error": "Please wait before trying again."}, status_code=429, headers={"Retry-After": str(retry)})
            try:
                return await endpoint(request, session)
            except AgentError as error:
                return auth.render("board_error.html", status=error.status, message=error.detail, gone=False)
        return dispatch

    def counts():
        with database.connect() as connection:
            return community_counts(connection)

    async def index(request, session):
        community = await run_in_threadpool(counts)
        if session is None:
            totals = await run_in_threadpool(boards.public_totals)
            return auth.render("topic_boards.html", session=None, totals=totals, community=community)
        listing = await run_in_threadpool(boards.index, session.user_id)
        recent = await run_in_threadpool(boards.recent, session.user_id)
        active = [board for board in listing["boards"] if board["posts"]["all"]]
        quiet = [board for board in listing["boards"] if not board["posts"]["all"]]
        return auth.render("topic_boards.html", session=session, active=active, quiet=quiet, recent=recent,
                           total_boards=listing["total_boards"], community=community)

    async def board(request, session):
        result = await run_in_threadpool(lambda: boards.posts(session.user_id, request.path_params["slug"], limit=50))
        return auth.render("topic_board.html", session=session, board=result["board"], posts=result["posts"])

    async def thread(request, session):
        result = await run_in_threadpool(boards.thread, session.user_id, request.path_params["post_id"])
        return auth.render("topic_thread.html", session=session, thread=result)

    return [
        Route("/boards", page(index, members_only=False), methods=["GET"]),
        Route("/boards/posts/{post_id}", page(thread), methods=["GET"]),
        Route("/boards/{slug}", page(board), methods=["GET"]),
    ]
