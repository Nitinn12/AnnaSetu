"""Donation status tracking: the single source of truth for status changes.

Every status change goes through change_status(), which checks the transition
table and the actor rules and writes a StatusEvent. Callers commit the session.
"""
from app.extensions import db
from app.models import Donation, StatusEvent
from app.timeutil import utcnow

STATUSES = ("posted", "claimed", "assigned", "collected", "delivered", "cancelled", "expired")
TERMINAL = {"delivered", "cancelled", "expired"}

ALLOWED = {
    "posted": {"claimed", "cancelled", "expired"},
    "claimed": {"posted", "assigned", "cancelled", "expired"},
    "assigned": {"collected", "claimed", "cancelled"},
    "collected": {"delivered"},
}

ACTOR_RULES = {
    ("posted", "claimed"): {"receiver"},
    ("claimed", "posted"): {"receiver"},
    ("claimed", "assigned"): {"volunteer", "receiver", "admin"},
    # Unassign: only before pickup; the donation goes back to claimed.
    ("assigned", "claimed"): {"receiver", "admin"},
    ("assigned", "collected"): {"volunteer"},
    ("collected", "delivered"): {"volunteer"},
    ("posted", "cancelled"): {"donor", "admin"},
    ("claimed", "cancelled"): {"donor", "admin"},
    ("assigned", "cancelled"): {"admin"},
    ("posted", "expired"): {"system"},
    ("claimed", "expired"): {"system"},
    ("assigned", "expired"): {"system"},
}


class TransitionError(ValueError):
    """Raised when a status change is not allowed."""


def change_status(donation, to_status, actor_role, actor_user_id=None, note=""):
    """Move a donation to to_status if the transition and actor are allowed.

    Adds a StatusEvent to the session and returns it. Does not commit.
    """
    if to_status not in STATUSES:
        raise TransitionError(f"Unknown status: {to_status}.")

    from_status = donation.status
    if from_status in TERMINAL:
        raise TransitionError(f"This donation is already {from_status}.")
    if to_status not in ALLOWED.get(from_status, set()):
        raise TransitionError(f"Cannot move from {from_status} to {to_status}.")
    if actor_role not in ACTOR_RULES.get((from_status, to_status), set()):
        raise TransitionError("Your role cannot make this change.")

    donation.status = to_status
    donation.updated_at = utcnow()
    event = StatusEvent(
        donation=donation,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        note=note or None,
        created_at=donation.updated_at,
    )
    db.session.add(event)
    return event


def expire_overdue(now):
    """Expire posted and claimed donations whose pickup deadline has passed.

    Assigned donations are never auto-expired. Returns the count. Does not commit.
    """
    overdue = (
        Donation.query.filter(
            Donation.status.in_(("posted", "claimed")),
            Donation.pickup_deadline < now,
        )
        .all()
    )
    for donation in overdue:
        change_status(donation, "expired", "system", note="Pickup deadline passed")
    return len(overdue)
