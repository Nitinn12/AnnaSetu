"""Minimal session-based CSRF protection helpers."""
import hmac
import secrets

from flask import session


def generate_csrf_token():
    """Return the session's CSRF token, creating one if absent."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]


def validate_csrf(submitted):
    """Constant-time comparison of the submitted token against the session."""
    expected = session.get("_csrf_token", "")
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)
