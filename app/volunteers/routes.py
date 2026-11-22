"""Volunteer workflow: NGOs/admins assign volunteers, volunteers collect and deliver."""
from flask import flash, redirect, render_template, request, session, url_for

from app.auth.decorators import role_required
from app.extensions import db
from app.models import Donation, User, VolunteerAssignment
from app.services.claims import (
    ClaimError,
    advance_task,
    assign_volunteer,
    unassign_volunteer,
)
from app.volunteers import bp

STEP_LABELS = {"collected": "collected", "delivered": "delivered"}
ADMIN_VISIBLE_STATUSES = ("claimed", "assigned", "collected")


def _current_user():
    """Return the logged-in User, or None."""
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def active_volunteers():
    """Volunteer accounts that can currently be assigned work."""
    return (
        User.query.filter_by(role="volunteer", is_active=True)
        .order_by(User.name, User.id)
        .all()
    )


def _after_manage(user):
    """Where NGO and admin users land after managing an assignment."""
    if user.role == "admin":
        return redirect(url_for("volunteers.admin_assignments"))
    return redirect(url_for("receivers.my_claims"))


# ---------- NGO / admin: assign and remove ----------

@bp.route("/donations/<int:donation_id>/assign", methods=["POST"])
@role_required("receiver", "admin")
def assign(donation_id):
    user = _current_user()
    raw = (request.form.get("volunteer_id") or "").strip()
    volunteer_id = int(raw) if raw.isdigit() else None
    try:
        assignment = assign_volunteer(donation_id, volunteer_id, user)
    except ClaimError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"{assignment.volunteer.name} is assigned to this pickup.", "success")
    return _after_manage(user)


@bp.route("/donations/<int:donation_id>/unassign", methods=["POST"])
@role_required("receiver", "admin")
def unassign(donation_id):
    user = _current_user()
    try:
        unassign_volunteer(donation_id, user)
    except ClaimError as exc:
        flash(str(exc), "danger")
    else:
        flash("Volunteer removed. You can assign someone else.", "info")
    return _after_manage(user)


@bp.route("/admin/assignments")
@role_required("admin")
def admin_assignments():
    donations = (
        Donation.query.filter(Donation.status.in_(ADMIN_VISIBLE_STATUSES))
        .order_by(Donation.pickup_deadline, Donation.id)
        .all()
    )
    rows = []
    for donation in donations:
        claim = donation.active_claim
        assignment = donation.active_assignment
        rows.append(
            {
                "donation": donation,
                "claimed_by": claim.organization.name if claim is not None else None,
                "volunteer_name": assignment.volunteer.name if assignment is not None else None,
                "can_assign": donation.status == "claimed" and claim is not None,
                "can_unassign": donation.status == "assigned" and assignment is not None,
            }
        )
    return render_template(
        "volunteers/admin_assignments.html", rows=rows, volunteers=active_volunteers()
    )


# ---------- Volunteer: my tasks ----------

@bp.route("/volunteer")
@role_required("volunteer")
def volunteer_home():
    user = _current_user()
    assignments = (
        VolunteerAssignment.query.filter_by(volunteer_user_id=user.id)
        .filter(VolunteerAssignment.status.in_(("active", "completed")))
        .order_by(VolunteerAssignment.assigned_at.desc(), VolunteerAssignment.id.desc())
        .all()
    )
    current, history = [], []
    for assignment in assignments:
        donation = assignment.donation
        claim = donation.active_claim
        row = {
            "assignment": assignment,
            "donation": donation,
            "donor_name": donation.organization.name,
            "receiver_name": claim.organization.name if claim is not None else None,
            "next_step": {"assigned": "collected", "collected": "delivered"}.get(donation.status),
        }
        (current if assignment.status == "active" else history).append(row)
    return render_template("volunteers/tasks.html", current=current, history=history)


def _advance(donation_id, to_status):
    user = _current_user()
    try:
        advance_task(donation_id, user, to_status)
    except ClaimError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Marked as {STEP_LABELS[to_status]}.", "success")
    return redirect(url_for("volunteers.volunteer_home"))


@bp.route("/volunteer/tasks/<int:donation_id>/collect", methods=["POST"])
@role_required("volunteer")
def collect(donation_id):
    return _advance(donation_id, "collected")


@bp.route("/volunteer/tasks/<int:donation_id>/deliver", methods=["POST"])
@role_required("volunteer")
def deliver(donation_id):
    return _advance(donation_id, "delivered")
