"""Time helpers. All datetimes stored in the database are naive UTC.

Users type and read times in India Standard Time (IST, UTC+05:30).
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

_LOCAL_FORMAT = "%Y-%m-%dT%H:%M"  # value produced by an HTML datetime-local input
_DISPLAY_FORMAT = "%d %b %Y, %H:%M"


def utcnow():
    """Return the current time as a naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_local_to_utc(text):
    """Parse an IST datetime-local string and return a naive UTC datetime.

    Raises ValueError if the text is missing or not in the expected format.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A date and time is required.")
    local = datetime.strptime(text.strip(), _LOCAL_FORMAT)
    aware = local.replace(tzinfo=IST)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def format_local(dt):
    """Format a naive UTC datetime for display in IST. Returns "" for None."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime(_DISPLAY_FORMAT)
