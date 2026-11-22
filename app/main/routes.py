from flask import redirect, render_template, session, url_for

from app.extensions import db
from app.main import bp
from app.models import User

DASHBOARD_ROUTES = {
    "donor": "donations.donate",
    "receiver": "receivers.find_food",
    "volunteer": "volunteers.volunteer_home",
    "admin": "impact.impact",
}


@bp.route("/")
def index():
    return render_template("main/index.html")


@bp.route("/about")
def about():
    return render_template("main/about.html")


@bp.route("/food-safety")
def food_safety():
    return render_template("main/food_safety.html")


@bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = db.session.get(User, session["user_id"])
    if user is None:
        session.pop("user_id", None)
        return redirect(url_for("auth.login"))

    endpoint = DASHBOARD_ROUTES.get(user.role)
    if endpoint is None:
        return redirect(url_for("main.index"))
    return redirect(url_for(endpoint))
