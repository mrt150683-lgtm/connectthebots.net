"""Public campaign pages: the leaderboard and personal share links.

Both are read-only and show no member identifiers: the leaderboard shows a
first name only for members who opted in, and a share link page never names
whose link it is.
"""
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import growth_config, qr
from .campaign import public_board
from .leaderboards import connection_rows, referral_rows, window_start
from .referrals import normalize_code


def campaign_routes(auth, database, clock, settings):
    def board_data():
        with database.connect() as connection:
            config = growth_config.load(connection)
            if config["public_leaderboard"] != "on":
                return None
            since = window_start(clock(), config)
            members = {row["user_id"]: row for row in connection.execute(
                "SELECT user_id,first_name,show_name_on_leaderboards FROM member_profiles")}
            referrals = public_board(referral_rows(connection, since), members)
            return {"window_days": config["leaderboard_window_days"], "early_access_percent": config["early_access_percent"],
                    "perk_days": config["referral_perk_days"], "referrals": referrals,
                    "connections": public_board(connection_rows(connection, since), members),
                    "leader": referrals[0] if referrals and len([row for row in referrals if row["rank"] == 1]) == 1 else None}

    def code_details(code):
        with database.connect() as connection:
            exists = code is not None and connection.execute("SELECT 1 FROM referral_codes WHERE code=?", (code,)).fetchone() is not None
            return exists, growth_config.load(connection)["referral_perk_days"]

    def limited(request):
        return auth.limiter.admit(request.client.host if request.client else "unknown")

    async def leaderboard(request):
        retry = limited(request)
        if retry:
            return JSONResponse({"error": "Please wait before trying again."}, status_code=429, headers={"Retry-After": str(retry)})
        data = await run_in_threadpool(board_data)
        if data is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return auth.render("leaderboard.html", board=data, base=settings.public_base_url)

    async def share_link(request):
        retry = limited(request)
        if retry:
            return JSONResponse({"error": "Please wait before trying again."}, status_code=429, headers={"Retry-After": str(retry)})
        code = normalize_code(request.path_params["code"])
        valid, perk_days = await run_in_threadpool(code_details, code)
        response = auth.render("share_link.html", code=code if valid else None, base=settings.public_base_url,
                               perk_days=perk_days, status=200 if valid else 404)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def share_qr(request):
        # Drawn here, never by a third-party QR service. It encodes only our own
        # share link for a real code, so it cannot be used to QR-code anything else.
        retry = limited(request)
        if retry:
            return JSONResponse({"error": "Please wait before trying again."}, status_code=429, headers={"Retry-After": str(retry)})
        code = normalize_code(request.path_params["code"])
        if request.path_params["kind"] not in ("svg", "png"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        valid, _ = await run_in_threadpool(code_details, code)
        if not valid:
            return JSONResponse({"error": "Not found"}, status_code=404)
        matrix = qr.encode(f"{settings.public_base_url}/r/{code}")
        headers = {"X-Robots-Tag": "noindex"}
        if request.path_params["kind"] == "svg":
            return Response(qr.svg(matrix, label=f"QR code for the Connect the Bots invitation link {code}"),
                            media_type="image/svg+xml", headers=headers)
        if request.query_params.get("download") == "1":
            headers["Content-Disposition"] = f'attachment; filename="connect-the-bots-{code}.png"'
        return Response(qr.png(matrix), media_type="image/png", headers=headers)

    return [
        Route("/leaderboard", leaderboard, methods=["GET"]),
        Route("/r/{code}", share_link, methods=["GET"]),
        Route("/r/{code}/qr.{kind:str}", share_qr, methods=["GET"]),
    ]
