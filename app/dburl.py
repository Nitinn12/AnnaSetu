"""Database URL helper.

Render (and Heroku-style hosts) hand out ``postgres://`` or ``postgresql://`` URLs.
SQLAlchemy 2 rejects the first and would load the psycopg2 driver for the second,
but this project ships psycopg 3, so both are rewritten to ``postgresql+psycopg://``.
"""


def normalize_database_url(url):
    """Return ``url`` ready for SQLAlchemy.

    * ``postgres://...`` and ``postgresql://...`` become ``postgresql+psycopg://...``
    * a URL that already names a driver (``postgresql+psycopg://``, ``sqlite:///``,
      and so on) is returned unchanged
    * ``None`` or a blank value returns ``""`` so callers can fall back to a default
    """
    url = (url or "").strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url
