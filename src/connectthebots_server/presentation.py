"""Human-facing dates; storage and API timestamps retain their exact values."""
from datetime import datetime, timezone


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def readable_time(value):
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return "Time unavailable"
        instant = instant.astimezone(timezone.utc)
        return f"{instant.day} {_MONTHS[instant.month - 1]} {instant.year} at {instant:%H:%M} UTC"
    except (AttributeError, TypeError, ValueError, OverflowError):
        return "Time unavailable"
