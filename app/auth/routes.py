import re

from flask import flash, redirect, render_template, request, session, url_for

from app.auth import bp
from app.extensions import db
from app.models import User

ROLE_CHOICES = [
    ("donor", "Donor"),
    ("receiver", "NGO / Receiver"),
    ("volunteer", "Volunteer"),
]
ALLOWED_ROLES = {value for value, _label in ROLE_CHOICES}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_safe_next(url):
    """Accept only local, absolute-path redirect targets."""
    return bool(url) and url.startswith("/") and not url.startswith("//") and "\\" not in url


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        selected_role = request.args.get("role", "")
        if selected_role not in ALLOWED_ROLES:
            selected_role = ""
        return render_template(
            "auth/register.html",
            roles=ROLE_CHOICES,
            selected_role=selected_role,
            form_values={},
        )

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    organization_name = request.form.get("organization_name", "").strip()
    role = request.form.get("role", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    errors = []
    if not name:
        errors.append("Name is required.")
    elif len(name) > 120:
        errors.append("Name must be 120 characters or fewer.")

    if not email or len(email) > 190 or not EMAIL_RE.match(email):
        errors.append("Please enter a valid email address (190 characters or fewer).")
    elif User.query.filter_by(email=email).first() is not None:
        errors.append("An account with this email already exists.")

    if role not in ALLOWED_ROLES:
        errors.append("Please choose a valid role.")

    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != confirm_password:
        errors.append("Passwords do not match.")

    if errors:
        for message in errors:
            flash(message, "danger")
        return render_template(
            "auth/register.html",
            roles=ROLE_CHOICES,
            selected_role=role if role in ALLOWED_ROLES else "",
            form_values={
                "name": name,
                "email": email,
                "organization_name": organization_name,
                "role": role,
            },
        )

    user = User(name=name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    flash(f"Welcome to AnnaSetu, {name}!", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html", form_values={})

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        flash("Invalid email or password.", "danger")
        return render_template("auth/login.html", form_values={"email": email})

    if not user.is_active:
        flash("This account has been deactivated. Please contact support.", "danger")
        return render_template("auth/login.html", form_values={"email": email})

    session.clear()
    session["user_id"] = user.id
    flash("Logged in successfully.", "success")

    next_url = request.form.get("next") or request.args.get("next")
    if _is_safe_next(next_url):
        return redirect(next_url)
    return redirect(url_for("main.dashboard"))


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))
