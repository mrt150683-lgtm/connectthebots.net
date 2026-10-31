"""OS-authorized operator CLI. This module provides no HTTP routes."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys


def _parser():
    parser = argparse.ArgumentParser(description="Operate the service locally over SSH.")
    commands = parser.add_subparsers(dest="group", required=True)
    invite = commands.add_parser("invite").add_subparsers(dest="action", required=True)
    invite.add_parser("create").add_argument("--days", type=int, default=7)
    listing = invite.add_parser("list")
    listing.add_argument("--page", type=int, default=1)
    listing.add_argument("--limit", type=int, default=20)
    invite.add_parser("revoke").add_argument("id")
    users = commands.add_parser("user").add_subparsers(dest="action", required=True)
    for action in ("show", "suspend", "unsuspend", "delete"):
        command = users.add_parser(action)
        command.add_argument("id")
        if action == "delete":
            command.add_argument("--confirm", required=True)
    postings = commands.add_parser("posting").add_subparsers(dest="action", required=True)
    for action in ("hide", "unhide"):
        postings.add_parser(action).add_argument("slug")
    reports = commands.add_parser("reports").add_subparsers(dest="action", required=True)
    listing = reports.add_parser("list")
    listing.add_argument("--page", type=int, default=1)
    listing.add_argument("--limit", type=int, default=20)
    reports.add_parser("show").add_argument("id")
    commands.add_parser("stats")
    commands.add_parser("numbers").add_argument("--days", type=int, default=14)
    commands.add_parser("backup")
    restore = commands.add_parser("restore")
    restore.add_argument("--from", dest="source", type=Path, required=True)
    restore.add_argument("--lost-host", action="store_true", help="Restore only to a missing database with independent authority")
    restore.add_argument("--old-host-fenced", action="store_true", help="Confirm the previous host is stopped and its storage credentials revoked")
    recovery = commands.add_parser("recovery").add_subparsers(dest="action", required=True)
    recovery.add_parser("release")
    recovery.add_parser("enroll")
    recovery.add_parser("status")
    commands.add_parser("certificate").add_subparsers(dest="action", required=True).add_parser("check")
    growth = commands.add_parser("growth").add_subparsers(dest="action", required=True)
    growth.add_parser("config")
    setting = growth.add_parser("set")
    setting.add_argument("key")
    setting.add_argument("value")
    growth.add_parser("flagged")
    growth.add_parser("clear").add_argument("id")
    growth.add_parser("flag").add_argument("id")
    growth.add_parser("recalculate")
    growth.add_parser("board-reports")
    growth.add_parser("unhide").add_argument("id")
    alerts = commands.add_parser("alerts")
    alerts.add_argument("kind", choices=("abuse_report", "certificate_expiry"))
    alerts.add_argument("state", choices=("on", "off"))
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    locks = ExitStack()
    try:
        from .board import BoardService
        from .clock import utc_now
        from .config import Settings
        from .db import Database
        from .interactions import InteractionService
        from .moderation import ModerationService
        from .notifications import NotificationService
        from .operator import OperatorError, OperatorService
        from .operator_alerts import OperatorAlerts
        settings = Settings.from_env()
        from .recovery_authority import from_settings
        database = Database(settings.database_path, utc_now, recovery_authority=from_settings(settings))
        from .recovery import runtime_lock
        if args.group != "restore":
            locks.enter_context(runtime_lock(database.path))
        if not (args.group == "restore" and args.lost_host) and not database.is_current():
            print("Operator database is unavailable or not current.", file=sys.stderr)
            return 2
        alerts = OperatorAlerts(database, utc_now, settings)
        board = BoardService(database, utc_now)
        notifications = NotificationService(database, utc_now, settings, operator_alerts=alerts)
        interactions = InteractionService(database, utc_now, board, notifications=notifications)
        moderation = ModerationService(database, utc_now, board, interactions, alerts)
        lifecycle = None
        if args.group == "user" and args.action == "delete":
            if args.confirm != args.id:
                raise OperatorError(422, "Repeat the exact account ID with --confirm before deleting it.")
            from .lifecycle import AccountLifecycle
            lifecycle = AccountLifecycle(database, utc_now, board, interactions, notifications, moderation)
        operator = OperatorService(database, utc_now, board, interactions, notifications, moderation, lifecycle=lifecycle)
        if args.group == "invite":
            if args.action == "create":
                result = operator.invite_create(args.days)
            elif args.action == "list":
                result = operator.invite_list(args.page, args.limit)
            else:
                result = operator.invite_revoke(args.id)
        elif args.group == "user":
            result = getattr(operator, "user_" + args.action)(args.id)
        elif args.group == "posting":
            result = getattr(operator, "posting_" + args.action)(args.slug)
        elif args.group == "reports":
            result = operator.reports_list(args.page, args.limit) if args.action == "list" else operator.report_show(args.id)
        elif args.group == "stats":
            result = operator.stats()
        elif args.group == "numbers":
            result = operator.numbers(args.days)
        elif args.group == "certificate":
            result = alerts.check_certificate()
        elif args.group == "alerts":
            result = alerts.set_enabled(args.kind, args.state == "on")
        elif args.group == "growth":
            from .growth import operate
            result = operate(database, utc_now, args)
        else:
            from .recovery import RecoveryService
            recovery = RecoveryService(database, utc_now, settings)
            if args.group == "backup":
                result = recovery.backup()
                if settings.backup_heartbeat_url:
                    from .backup_monitor import signal_backup_success
                    signal_backup_success(database, settings.backup_heartbeat_url, result)
                    result["heartbeat_sent"] = True
            elif args.group == "restore":
                result = recovery.restore(args.source, lost_host=args.lost_host, old_host_fenced=args.old_host_fenced)
            elif args.action == "enroll":
                if database.recovery_authority is None:
                    from .recovery_authority import AuthorityError
                    raise AuthorityError("Configure offsite storage before enrollment.")
                result = database.recovery_authority.enroll(database)
            elif args.action == "status":
                from .recovery_authority import local_state
                with database.connect() as connection:
                    state = local_state(connection)
                if state["enrolled"]:
                    recovery._independent().require(state)
                result = {"enrolled": bool(state["enrolled"]), "generation": state["generation"], "authority_verified": bool(state["enrolled"])}
            else:
                result = recovery.release_hold()
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, default=str))
        return 0
    except Exception as error:
        # Only our fixed, user-safe domain messages may enter CLI output.
        from .moderation import ModerationError
        from .operator import OperatorError
        from .operator_alerts import OperatorAlertError
        from .recovery import RecoveryError
        from .recovery_authority import AuthorityError
        from .backup_monitor import BackupMonitorError
        from .agent_common import AgentError
        from .growth_config import GrowthConfigError
        if isinstance(error, (AgentError, GrowthConfigError, ModerationError, OperatorError, OperatorAlertError, RecoveryError, AuthorityError, BackupMonitorError)):
            print(error.detail, file=sys.stderr)
        else:
            print("Operator command failed. Check configuration and database health.", file=sys.stderr)
        return 2
    finally:
        locks.close()


if __name__ == "__main__":
    raise SystemExit(main())
