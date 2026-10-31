"""Dashboard state comes only from stored reports and explicit owner settings."""
from datetime import datetime, timedelta


def dashboard_state(settings, latest, pending, active_keys, now):
    if settings["paused"]:
        return "paused"
    # Both kinds need an owner decision. Show the newest pending kind; every
    # pending report stays in the separate queue until explicitly reviewed.
    if pending["reports"]:
        return pending["reports"][0]["payload"]["kind"]
    due = settings["next_due_at"]
    if settings["cadence_minutes"] is not None and due is not None:
        if now >= datetime.fromisoformat(due.replace("Z", "+00:00")) + timedelta(hours=6):
            return "failed"
    if latest is not None and latest["payload"]["kind"] == "nothing":
        return "quiet"
    if latest is not None:
        return "working"
    return "setup"


def dashboard_context(service, user_id, now):
    settings = service.settings(user_id)
    reports = service.list_reports(user_id)
    pending = service.pending_reports(user_id)
    keys = service.list_keys(user_id)
    latest = reports[0] if reports else None
    active_keys = [key for key in keys if key["revoked_at"] is None]
    return {
        "state": dashboard_state(settings, latest, pending, active_keys, now),
        "report_settings": settings, "reports": reports, "latest": latest,
        "pending": pending, "active_keys": active_keys,
    }
