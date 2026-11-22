"""Impact dashboard: real figures from the database, scoped to the viewer's role.

Admins see the whole platform. Donors, NGOs (receivers) and volunteers see only
their own donations, claims or tasks. The optional ``start`` and ``end`` query
parameters (YYYY-MM-DD, IST) filter every figure.
"""
from datetime import datetime, timedelta

from flask import abort, render_template, request, session, url_for

from app.auth.decorators import role_required
from app.extensions import db
from app.impact import bp
from app.impact.service import (
    ASSUMPTIONS,
    DASHBOARD_NOTES,
    impact_for_user,
    parse_date_range,
)
from app.models import User
from app.timeutil import IST

TITLES = {
    "admin": "Impact dashboard",
    "donor": "Your impact",
    "receiver": "Your NGO's impact",
    "volunteer": "Your impact",
}


def _presets(today):
    """Quick date links: last 7 days, last 30 days, all time."""
    def link(days):
        start = today - timedelta(days=days - 1)
        return url_for("impact.impact", start=start.isoformat(), end=today.isoformat())

    return [
        ("Last 7 days", link(7)),
        ("Last 30 days", link(30)),
        ("All time", url_for("impact.impact")),
    ]


@bp.route("/impact")
@role_required("admin", "donor", "receiver", "volunteer")
def impact():
    user = db.session.get(User, session["user_id"])
    date_range = parse_date_range(request.args.get("start"), request.args.get("end"))
    data = impact_for_user(user, date_range)
    if data is None:
        abort(403)

    today = datetime.now(IST).date()
    return render_template(
        "dashboard/impact.html",
        data=data,
        rng=date_range,
        title=TITLES.get(user.role, "Impact"),
        presets=_presets(today),
        today=today.isoformat(),
        notes=DASHBOARD_NOTES,
        assumptions=ASSUMPTIONS,
    )
