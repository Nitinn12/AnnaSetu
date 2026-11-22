"""Claim, release, volunteer-task and donor-cancel operations."""
import functools

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.matching import evaluate
from app.models import Claim, Donation, User, VolunteerAssignment
from app.timeutil import utcnow
from app.tracking import TransitionError, change_status


class ClaimError(ValueError):
    """A user-facing error from a claim or task operation."""


def _atomic(func):
    """Roll back the session on any failure, then re-raise (TransitionError becomes ClaimError)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TransitionError as exc:
            db.session.rollback()
            raise ClaimError(str(exc)) from exc
        except Exception:
            db.session.rollback()
            raise

    return wrapper


@_atomic
def claim_donation(donation_id, user):
    """An NGO account claims a posted donation. Returns the new Claim."""
    if user.role != "receiver" or user.organization_id is None:
        raise ClaimError("Only NGO accounts with an organisation can claim food.")

    donation = db.session.get(Donation, donation_id, with_for_update=True)
    if donation is None or donation.status != "posted":
        raise ClaimError("This donation is no longer available.")

    result = evaluate(donation, user.organization, utcnow())
    if not result.eligible:
        raise ClaimError(" ".join(result.rejections))

    claim = Claim(
        donation_id=donation.id,
        organization_id=user.organization_id,
        user_id=user.id,
        status="active",
    )
    db.session.add(claim)
    change_status(donation, "claimed", "receiver", user.id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        raise ClaimError("Someone else has already claimed this donation.") from exc
    return claim


@_atomic
def release_claim(claim_id, user):
    """The claiming NGO gives up its active claim, and the donation is posted again."""
    if user.role != "receiver" or user.organization_id is None:
        raise ClaimError("Only NGO accounts with an organisation can release a claim.")

    claim = db.session.get(Claim, claim_id, with_for_update=True)
    if (
        claim is None
        or claim.organization_id != user.organization_id
        or claim.status != "active"
    ):
        raise ClaimError("This claim cannot be released.")

    donation = db.session.get(Donation, claim.donation_id, with_for_update=True)
    if donation is None or donation.status != "claimed":
        raise ClaimError("This donation can no longer be released.")

    claim.status = "released"
    claim.released_at = utcnow()
    change_status(donation, "posted", "receiver", user.id, note="Claim released")
    db.session.commit()
    return claim


def _check_can_manage(actor, donation):
    """NGO users may manage only donations claimed by their own organisation; admins any."""
    if actor.role not in ("receiver", "admin"):
        raise ClaimError("Only the claiming NGO or an admin can manage volunteer assignments.")
    claim = donation.active_claim
    if claim is None:
        raise ClaimError("This donation has no active claim.")
    if actor.role == "receiver" and (
        actor.organization_id is None or claim.organization_id != actor.organization_id
    ):
        raise ClaimError("Only the NGO that claimed this donation can manage its volunteer.")


@_atomic
def assign_volunteer(donation_id, volunteer_id, actor):
    """The claiming NGO (or an admin) assigns a volunteer to a claimed donation."""
    if actor.role not in ("receiver", "admin"):
        raise ClaimError("Only the claiming NGO or an admin can assign volunteers.")

    donation = db.session.get(Donation, donation_id, with_for_update=True)
    if donation is None:
        raise ClaimError("This donation was not found.")
    if donation.status == "assigned":
        raise ClaimError("A volunteer is already assigned to this donation.")
    if donation.status != "claimed":
        raise ClaimError("Volunteers can only be assigned to claimed donations.")
    _check_can_manage(actor, donation)

    volunteer = db.session.get(User, volunteer_id) if volunteer_id else None
    if volunteer is None or volunteer.role != "volunteer":
        raise ClaimError("Choose a valid volunteer.")
    if not volunteer.is_active:
        raise ClaimError("That volunteer account is not active.")
    if donation.active_assignment is not None:
        raise ClaimError("A volunteer is already assigned to this donation.")

    now = utcnow()
    assignment = VolunteerAssignment(
        donation_id=donation.id,
        volunteer_user_id=volunteer.id,
        assigned_by_user_id=actor.id,
        status="active",
        assigned_at=now,
    )
    db.session.add(assignment)
    change_status(
        donation, "assigned", actor.role, actor.id, note=f"Assigned to {volunteer.name}"
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        raise ClaimError("A volunteer is already assigned to this donation.") from exc
    return assignment


@_atomic
def unassign_volunteer(donation_id, actor):
    """Remove the volunteer before pickup. The donation returns to claimed."""
    if actor.role not in ("receiver", "admin"):
        raise ClaimError("Only the claiming NGO or an admin can remove a volunteer.")

    donation = db.session.get(Donation, donation_id, with_for_update=True)
    if donation is None:
        raise ClaimError("This donation was not found.")
    assignment = donation.active_assignment
    if donation.status != "assigned" or assignment is None:
        raise ClaimError("A volunteer can only be removed before the food is collected.")
    _check_can_manage(actor, donation)

    name = assignment.volunteer.name
    now = utcnow()
    assignment.status = "removed"
    assignment.removed_at = now
    change_status(
        donation, "claimed", actor.role, actor.id, note=f"Volunteer {name} removed"
    )
    db.session.commit()
    return donation


@_atomic
def accept_task(donation_id, volunteer):
    """A volunteer accepts a claimed donation for pickup and delivery."""
    if volunteer.role != "volunteer":
        raise ClaimError("Only volunteers can accept delivery tasks.")

    donation = db.session.get(Donation, donation_id, with_for_update=True)
    if donation is None or donation.status != "claimed":
        raise ClaimError("This task is not available to accept.")
    if donation.active_assignment is not None:
        raise ClaimError("Another volunteer has already accepted this task.")

    assignment = VolunteerAssignment(
        donation_id=donation.id,
        volunteer_user_id=volunteer.id,
        assigned_by_user_id=volunteer.id,
        status="active",
        assigned_at=utcnow(),
    )
    db.session.add(assignment)
    change_status(donation, "assigned", "volunteer", volunteer.id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        raise ClaimError("Another volunteer has already accepted this task.") from exc
    return assignment


@_atomic
def advance_task(donation_id, volunteer, to_status):
    """The assigned volunteer marks the donation collected or delivered."""
    if to_status not in ("collected", "delivered"):
        raise ClaimError("Unknown task step.")

    donation = db.session.get(Donation, donation_id, with_for_update=True)
    if donation is None:
        raise ClaimError("This task was not found.")

    assignment = donation.active_assignment
    if assignment is None or assignment.volunteer_user_id != volunteer.id:
        raise ClaimError("This task is not assigned to you.")

    change_status(donation, to_status, "volunteer", volunteer.id)
    now = utcnow()
    if to_status == "collected":
        assignment.collected_at = now
    else:
        assignment.delivered_at = now
        assignment.status = "completed"
    db.session.commit()
    return donation


@_atomic
def donor_cancel(donation, user):
    """The donor who posted a donation cancels it. An active claim is closed too."""
    if user.role != "donor" or donation.donor_user_id != user.id:
        raise ClaimError("Only the donor who posted this donation can cancel it.")

    active_claim = donation.active_claim
    change_status(donation, "cancelled", "donor", user.id)
    if active_claim is not None:
        active_claim.status = "released"
        active_claim.released_at = utcnow()
    db.session.commit()
    return donation
