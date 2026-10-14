"""User-local time helpers. Everything is stored in UTC; "today" is decided in the user's own time zone."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import models


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def zone_for(user: models.User) -> ZoneInfo:
    name = user.profile.timezone if user.profile else "UTC"
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def local_today(user: models.User) -> date:
    return datetime.now(zone_for(user)).date()


def local_date(dt: datetime, user: models.User) -> date:
    """Calendar date of a stored timestamp in the user's zone. Naive values (SQLite) are treated as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone_for(user)).date()
