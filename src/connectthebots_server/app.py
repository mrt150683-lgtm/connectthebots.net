"""Application factory. Creating an app has no database or network effects."""

from contextlib import asynccontextmanager
import logging
import sqlite3

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .clock import Clock, utc_now
from .config import Settings
from .db import Database
from .security import SecurityBoundary
from .auth import AuthService
from .account_data import account_routes
from .report_store import ReportService
from .report_routes import report_routes
from .board import BoardService
from .board_routes import board_routes
from .interactions import InteractionService
from .interaction_routes import interaction_routes
from .notifications import NotificationService
from .notification_routes import notification_routes
from .moderation import ModerationService
from .moderation_routes import moderation_routes
from .operator_alerts import OperatorAlerts
from .lifecycle import AccountLifecycle
from .recovery import recovery_held, runtime_lock
from .security_contact import security_contact_routes
from .agent_api import AgentApi, agent_routes
from .campaign_routes import campaign_routes
from .referrals import ReferralService
from .taste import TasteService
from .topic_boards import TopicBoardService


logger = logging.getLogger("connectthebots_server")

RECOVERY_UNAVAILABLE = "This change could not be saved. Recovery storage needs operator attention."
BUSY = "The service is saving another change. Please try again shortly."


def _wants_page(request):
    """People in a browser get a page; agents and API callers keep JSON."""
    path = request.url.path
    if path.startswith("/api/") or path == "/healthz" or path.endswith(".json"):
        return False
    return "application/json" not in request.headers.get("accept", "").lower()


def _service_page(auth, request, status, *, title, message, retry_after=None, api_message=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    if auth is not None and _wants_page(request):
        try:
            response = auth.render("service_error.html", status=status, title=title, message=message,
                                   signin=request.url.path.startswith("/auth/"))
            response.headers.update(headers)
            return response
        except Exception:
            # Never let the error page itself fail; fall back to the fixed JSON.
            pass
    return JSONResponse({"error": api_message or message}, status_code=status, headers=headers)


def create_app(settings: Settings | None = None, clock: Clock | None = None, *, google_provider=None) -> SecurityBoundary:
    settings = Settings.from_env() if settings is None else settings
    clock = utc_now if clock is None else clock
    from .recovery_authority import AuthorityError, from_settings
    database = Database(settings.database_path, clock, recovery_authority=from_settings(settings))
    operator_alerts = OperatorAlerts(database, clock, settings)
    notifications = NotificationService(database, clock, settings, operator_alerts=operator_alerts)
    auth = AuthService(settings, database, clock, provider=google_provider, notifications=notifications)
    reports = ReportService(database, clock)
    board = BoardService(database, clock)
    interactions = InteractionService(database, clock, board, notifications=notifications)
    moderation = ModerationService(database, clock, board, interactions, operator_alerts)
    lifecycle = AccountLifecycle(database, clock, board, interactions, notifications, moderation)
    referrals = ReferralService(database, clock)
    taste = TasteService(database, clock, referrals)
    topic_boards = TopicBoardService(database, clock)
    agents = AgentApi(database, clock, settings, taste, referrals, topic_boards, interactions)
    auth.referrals = referrals

    async def _unexpected_error(request, exc):
        return _service_page(auth, request, 500, title="Something went wrong.",
                             message="Something went wrong on our side. Please try again in a few minutes.",
                             api_message="Service temporarily unavailable")

    async def _not_found(request, exc):
        return JSONResponse({"error": "Not found"}, status_code=404)

    async def _recovery_unavailable(request, exc):
        # The detail is one of our fixed recovery messages: it never contains
        # request data, identities or provider responses. It names the cause so
        # the operator can tell storage outages, quota refusals and mismatches apart.
        logger.error("Protected change refused by recovery authority: %s", getattr(exc, "detail", "unknown"))
        return _service_page(auth, request, 503, title="We could not save that just now.", api_message=RECOVERY_UNAVAILABLE,
                             message="Our backup system did not confirm this change, so nothing was saved. "
                                     "The problem is on our side, not yours. Please try again in a little while.")

    async def _database_unavailable(request, exc):
        code = getattr(exc, "sqlite_errorcode", 0) & 0xff
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return _service_page(auth, request, 503, title="One moment.", message=BUSY, retry_after="2")
        return await _unexpected_error(request, exc)

    @asynccontextmanager
    async def lifespan(application):
        database.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        with runtime_lock(database.path):
            await run_in_threadpool(database.migrate)
            yield

    async def health(request):
        healthy = await run_in_threadpool(database.is_current)
        if healthy:
            healthy = not await run_in_threadpool(recovery_held, database, settings)
        return JSONResponse({"status": "ok" if healthy else "unavailable"}, status_code=200 if healthy else 503)

    application = Starlette(
        debug=False,
        lifespan=lifespan,
        routes=[
            Route("/healthz", health, methods=["GET"]),
            *security_contact_routes(settings, clock),
            *auth.routes(),
            *account_routes(auth, board, interactions, notifications, moderation, lifecycle, agents),
            *report_routes(auth, reports, board, interactions, agents),
            *board_routes(auth, board),
            *interaction_routes(auth, interactions, board),
            *notification_routes(auth, notifications),
            *moderation_routes(auth, moderation),
            *agent_routes(auth, agents),
            *campaign_routes(auth, database, clock, settings),
        ],
        exception_handlers={404: _not_found, AuthorityError: _recovery_unavailable,
                            sqlite3.OperationalError: _database_unavailable, Exception: _unexpected_error},
    )
    application.state.settings = settings
    application.state.clock = clock
    application.state.database = database
    application.state.auth = auth
    application.state.reports = reports
    application.state.board = board
    application.state.interactions = interactions
    application.state.notifications = notifications
    application.state.moderation = moderation
    application.state.operator_alerts = operator_alerts
    application.state.lifecycle = lifecycle
    application.state.referrals = referrals
    application.state.taste = taste
    application.state.topic_boards = topic_boards
    application.state.agents = agents
    return SecurityBoundary(application, settings)
