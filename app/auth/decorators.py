from functools import wraps

from flask import flash, redirect, request, session, url_for

from app.extensions import db
from app.models import User


def login_required(view):
    """Redirect anonymous visitors to the login page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    """Allow access only to logged-in users whose role is in ``roles``."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.path))

            user = db.session.get(User, session["user_id"])
            if user is None or user.role not in roles:
                flash("You do not have access to that page.", "danger")
                return redirect(url_for("main.dashboard"))

            return view(*args, **kwargs)

        return wrapped

    return decorator
