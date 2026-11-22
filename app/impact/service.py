"""Impact reporting: counts computed from delivered donations and platform totals.

No fake numbers. Every figure comes from a database query.
"""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import and_, case, func

from app.donations.validation import CATEGORY_LABELS
from app.extensions import db
from app.models import Claim, Donation, Organization, User, VolunteerAssignment
from app.timeutil import IST

KG_PER_PORTION = 0.5  # assumption: one portion weighs 0.5 kg
KG_PER_MEAL = 0.4  # assumption: one meal weighs 0.4 kg

ASSUMPTIONS = [
    f"Each portion is counted as {KG_PER_PORTION:g} kg when a donation is measured in portions.",
    f"Each meal is estimated at {KG_PER_MEAL:g} kg of food.",
]


def compute_impact():
    """Summarise delivered donations only."""
    rows = (
        db.session.query(
            Donation.category,
            Donation.unit,
            func.count(Donation.id),
            func.coalesce(func.sum(Donation.quantity), 0.0),
        )
        .filter(Donation.status == "delivered")
        .group_by(Donation.category, Donation.unit)
        .all()
    )

    delivered_count = 0
    delivered_kg = 0.0
    by_category = {}
    for category, unit, count, total_quantity in rows:
        delivered_count += count
        kg = float(total_quantity) if unit == "kg" else float(total_quantity) * KG_PER_PORTION
        delivered_kg += kg
        by_category[category] = by_category.get(category, 0) + count

    delivered_kg = round(delivered_kg, 2)
    return {
        "delivered_count": delivered_count,
        "delivered_kg": delivered_kg,
        "estimated_meals": round(delivered_kg / KG_PER_MEAL),
        "by_category": by_category,
        "assumptions": list(ASSUMPTIONS),
    }


def compute_platform_counts():
    """Count users per role, organisations per type and donations per status."""
    users_per_role = dict(
        db.session.query(User.role, func.count(User.id)).group_by(User.role).all()
    )
    orgs_per_type = dict(
        db.session.query(Organization.org_type, func.count(Organization.id))
        .group_by(Organization.org_type)
        .all()
    )
    donations_per_status = dict(
        db.session.query(Donation.status, func.count(Donation.id))
        .group_by(Donation.status)
        .all()
    )
    return {
        "users_per_role": users_per_role,
        "orgs_per_type": orgs_per_type,
        "donations_per_status": donations_per_status,
    }


# ---------------------------------------------------------------------------
# Impact dashboard: role-scoped statistics with an optional date filter.
#
# Every number is a database query. Nothing here writes to the database.
#
# What the date filter means (dates are India Standard Time, both ends inclusive):
#   * donation figures     -> the day the donation was posted (Donation.created_at)
#   * NGO claim figures    -> the day the claim was made (Claim.created_at)
#   * volunteer figures    -> the day the volunteer was assigned (assigned_at)
# ---------------------------------------------------------------------------

# Food measured in portions is converted with KG_PER_PORTION so every total is in kg.
KG_EXPR = case(
    (Donation.unit == "kg", Donation.quantity),
    else_=Donation.quantity * KG_PER_PORTION,
)

STATUS_LABELS = {
    "posted": "Pending (posted)",
    "claimed": "Claimed",
    "assigned": "Volunteer assigned",
    "collected": "Collected",
    "delivered": "Delivered",
    "expired": "Expired",
    "cancelled": "Cancelled",
}
STATUS_ORDER = ("posted", "claimed", "assigned", "collected", "delivered", "expired", "cancelled")
IN_PROGRESS_STATUSES = ("claimed", "assigned", "collected")
RECEIVER_ORG_TYPES = ("ngo", "community_kitchen")
PARTNER_LIMIT = 5

DASHBOARD_NOTES = [
    "Figures count donations by their current stored status. A donation shows as expired "
    "once the system has marked it so (the expire-overdue command).",
    "Claimed means an NGO has claimed the food and it has not been delivered yet.",
]


class DateRange:
    """An optional inclusive IST date range, plus the same range as naive UTC bounds."""

    def __init__(self, start=None, end=None, errors=None):
        self.start = start
        self.end = end
        self.errors = errors or []
        self.start_utc = _ist_midnight_as_utc(start) if start else None
        # End bound is exclusive: midnight at the start of the day after `end`.
        self.end_utc = _ist_midnight_as_utc(end + timedelta(days=1)) if end else None

    @property
    def is_filtered(self):
        return self.start is not None or self.end is not None

    @property
    def label(self):
        if self.start and self.end:
            return f"{self.start:%d %b %Y} to {self.end:%d %b %Y}"
        if self.start:
            return f"From {self.start:%d %b %Y}"
        if self.end:
            return f"Up to {self.end:%d %b %Y}"
        return "All time"


def _ist_midnight_as_utc(day):
    local = datetime.combine(day, time.min, tzinfo=IST)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_day(raw, label, errors):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"{label} date is not valid. Use the date picker (YYYY-MM-DD).")
        return None


def parse_date_range(start_raw, end_raw):
    """Build a DateRange from the raw ``start`` and ``end`` query values.

    Bad input never raises: the bad side is ignored and a message is recorded in
    ``errors``. If start is after end, both are ignored.
    """
    errors = []
    start = _parse_day(start_raw, "Start", errors)
    end = _parse_day(end_raw, "End", errors)
    if start and end and start > end:
        errors.append("The start date must not be after the end date. Showing all time.")
        start = end = None
    return DateRange(start, end, errors)


def _between(column, date_range):
    conditions = []
    if date_range.start_utc is not None:
        conditions.append(column >= date_range.start_utc)
    if date_range.end_utc is not None:
        conditions.append(column < date_range.end_utc)
    return conditions


def _percent(part, whole):
    return round(100 * part / whole) if whole else 0


def _meals(kg):
    return round(kg / KG_PER_MEAL)


def _donation_summary(conditions):
    """Status counts and delivered food for the donations matching ``conditions``."""
    rows = (
        db.session.query(
            Donation.status,
            func.count(Donation.id),
            func.coalesce(func.sum(KG_EXPR), 0.0),
        )
        .filter(*conditions)
        .group_by(Donation.status)
        .all()
    )
    counts = {status: int(count) for status, count, _kg in rows}
    delivered_kg = round(sum(float(kg) for status, _c, kg in rows if status == "delivered"), 2)
    total = sum(counts.values())
    status_rows = [
        {
            "status": status,
            "label": STATUS_LABELS[status],
            "count": counts.get(status, 0),
            "percent": _percent(counts.get(status, 0), total),
        }
        for status in STATUS_ORDER
    ]
    return {
        "total": total,
        "pending": counts.get("posted", 0),
        "claimed": sum(counts.get(s, 0) for s in IN_PROGRESS_STATUSES),
        "delivered": counts.get("delivered", 0),
        "expired": counts.get("expired", 0),
        "cancelled": counts.get("cancelled", 0),
        "delivered_kg": delivered_kg,
        "estimated_meals": _meals(delivered_kg),
        "status_rows": status_rows,
    }


def _category_breakdown(delivered_query):
    """Delivered donations per food category. ``delivered_query`` selects Donation rows."""
    rows = (
        delivered_query.with_entities(
            Donation.category,
            func.count(Donation.id),
            func.coalesce(func.sum(KG_EXPR), 0.0),
        )
        .group_by(Donation.category)
        .all()
    )
    result = [
        {
            "key": category,
            "label": CATEGORY_LABELS.get(category, category),
            "count": int(count),
            "kg": round(float(kg), 2),
        }
        for category, count, kg in rows
    ]
    result.sort(key=lambda r: (-r["kg"], r["label"]))
    top = max((r["kg"] for r in result), default=0)
    for row in result:
        row["percent"] = round(100 * row["kg"] / top) if top else 0
    return result


def _partners(rows):
    """Turn (name, deliveries, kg) rows into display dicts, best first."""
    partners = [
        {"name": name, "deliveries": int(deliveries), "kg": round(float(kg), 2)}
        for name, deliveries, kg in rows
    ]
    partners.sort(key=lambda p: (-p["kg"], p["name"]))
    return partners


def _delivered_totals(categories):
    kg = round(sum(c["kg"] for c in categories), 2)
    return {
        "delivered": sum(c["count"] for c in categories),
        "delivered_kg": kg,
        "estimated_meals": _meals(kg),
    }


# ---------- admin: the whole platform ----------

def admin_impact(date_range):
    donation_conds = _between(Donation.created_at, date_range)
    claim_conds = _between(Claim.created_at, date_range)
    assigned_conds = _between(VolunteerAssignment.assigned_at, date_range)
    delivered_conds = _between(VolunteerAssignment.delivered_at, date_range)

    summary = _donation_summary(donation_conds)
    categories = _category_breakdown(
        db.session.query(Donation).filter(Donation.status == "delivered", *donation_conds)
    )

    donors_posting = (
        db.session.query(func.count(Donation.organization_id.distinct()))
        .filter(*donation_conds)
        .scalar()
        or 0
    )

    volunteer_accounts = (
        db.session.query(func.count(User.id))
        .filter(User.role == "volunteer", User.is_active.is_(True))
        .scalar()
        or 0
    )
    volunteers_engaged = (
        db.session.query(func.count(VolunteerAssignment.volunteer_user_id.distinct()))
        .filter(VolunteerAssignment.status.in_(("active", "completed")), *assigned_conds)
        .scalar()
        or 0
    )
    volunteer_deliveries = (
        db.session.query(func.count(VolunteerAssignment.id))
        .filter(VolunteerAssignment.status == "completed", *delivered_conds)
        .scalar()
        or 0
    )

    ngos_registered = (
        db.session.query(func.count(Organization.id))
        .filter(Organization.org_type.in_(RECEIVER_ORG_TYPES))
        .scalar()
        or 0
    )
    ngos_claimed = (
        db.session.query(func.count(Claim.organization_id.distinct()))
        .filter(*claim_conds)
        .scalar()
        or 0
    )
    is_delivered_claim = and_(Claim.status == "active", Donation.status == "delivered")
    ngos_received = (
        db.session.query(func.count(Claim.organization_id.distinct()))
        .select_from(Claim)
        .join(Donation, Donation.id == Claim.donation_id)
        .filter(is_delivered_claim, *claim_conds)
        .scalar()
        or 0
    )
    top_rows = (
        db.session.query(
            Organization.name,
            func.count(Claim.id),
            func.coalesce(func.sum(case((is_delivered_claim, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_delivered_claim, KG_EXPR), else_=0.0)), 0.0),
        )
        .select_from(Claim)
        .join(Organization, Organization.id == Claim.organization_id)
        .join(Donation, Donation.id == Claim.donation_id)
        .filter(*claim_conds)
        .group_by(Organization.id, Organization.name)
        .all()
    )
    top_ngos = [
        {"name": name, "claims": int(claims), "deliveries": int(deliveries), "kg": round(float(kg), 2)}
        for name, claims, deliveries, kg in top_rows
    ]
    top_ngos.sort(key=lambda n: (-n["kg"], -n["claims"], n["name"]))

    return {
        "scope": "admin",
        "summary": summary,
        "categories": categories,
        "donors_posting": int(donors_posting),
        "volunteers": {
            "accounts": int(volunteer_accounts),
            "engaged": int(volunteers_engaged),
            "deliveries": int(volunteer_deliveries),
        },
        "ngos": {
            "registered": int(ngos_registered),
            "claimed": int(ngos_claimed),
            "received": int(ngos_received),
            "top": top_ngos[:PARTNER_LIMIT],
        },
    }


# ---------- donor: their own donations ----------

def donor_impact(user, date_range):
    donation_conds = [Donation.donor_user_id == user.id] + _between(
        Donation.created_at, date_range
    )
    summary = _donation_summary(donation_conds)
    categories = _category_breakdown(
        db.session.query(Donation).filter(Donation.status == "delivered", *donation_conds)
    )
    partner_rows = (
        db.session.query(
            Organization.name,
            func.count(Donation.id),
            func.coalesce(func.sum(KG_EXPR), 0.0),
        )
        .select_from(Donation)
        .join(Claim, and_(Claim.donation_id == Donation.id, Claim.status == "active"))
        .join(Organization, Organization.id == Claim.organization_id)
        .filter(Donation.status == "delivered", *donation_conds)
        .group_by(Organization.id, Organization.name)
        .all()
    )
    partners = _partners(partner_rows)
    return {
        "scope": "donor",
        "summary": summary,
        "categories": categories,
        "partners": partners[:PARTNER_LIMIT],
        "partner_count": len(partners),
    }


# ---------- NGO / receiver: claims made by their organisation ----------

def receiver_impact(user, date_range):
    org = user.organization
    if org is None:
        return {"scope": "receiver", "needs_profile": True}

    claim_conds = [Claim.organization_id == org.id] + _between(Claim.created_at, date_range)
    rows = (
        db.session.query(Claim.status, Donation.status, func.count(Claim.id))
        .select_from(Claim)
        .join(Donation, Donation.id == Claim.donation_id)
        .filter(*claim_conds)
        .group_by(Claim.status, Donation.status)
        .all()
    )
    made = delivered = in_progress = expired = released = 0
    for claim_status, donation_status, count in rows:
        count = int(count)
        made += count
        if claim_status == "released":
            released += count
        elif donation_status == "delivered":
            delivered += count
        elif donation_status in IN_PROGRESS_STATUSES:
            in_progress += count
        elif donation_status == "expired":
            expired += count

    delivered_query = (
        db.session.query(Donation)
        .join(Claim, Claim.donation_id == Donation.id)
        .filter(Claim.status == "active", Donation.status == "delivered", *claim_conds)
    )
    categories = _category_breakdown(delivered_query)
    totals = _delivered_totals(categories)

    partner_rows = (
        db.session.query(
            Organization.name,
            func.count(Donation.id),
            func.coalesce(func.sum(KG_EXPR), 0.0),
        )
        .select_from(Claim)
        .join(Donation, Donation.id == Claim.donation_id)
        .join(Organization, Organization.id == Donation.organization_id)
        .filter(Claim.status == "active", Donation.status == "delivered", *claim_conds)
        .group_by(Organization.id, Organization.name)
        .all()
    )
    partners = _partners(partner_rows)
    return {
        "scope": "receiver",
        "needs_profile": False,
        "org_name": org.name,
        "claims": {
            "made": made,
            "in_progress": in_progress,
            "delivered": delivered,
            "expired": expired,
            "released": released,
        },
        "delivered_kg": totals["delivered_kg"],
        "estimated_meals": totals["estimated_meals"],
        "categories": categories,
        "partners": partners[:PARTNER_LIMIT],
        "partner_count": len(partners),
    }


# ---------- volunteer: their own pickups and deliveries ----------

def volunteer_impact(user, date_range):
    own_conds = [VolunteerAssignment.volunteer_user_id == user.id] + _between(
        VolunteerAssignment.assigned_at, date_range
    )
    rows = (
        db.session.query(VolunteerAssignment.status, func.count(VolunteerAssignment.id))
        .filter(VolunteerAssignment.status.in_(("active", "completed")), *own_conds)
        .group_by(VolunteerAssignment.status)
        .all()
    )
    by_status = {status: int(count) for status, count in rows}

    delivered_query = (
        db.session.query(Donation)
        .join(VolunteerAssignment, VolunteerAssignment.donation_id == Donation.id)
        .filter(
            VolunteerAssignment.status == "completed",
            Donation.status == "delivered",
            *own_conds,
        )
    )
    categories = _category_breakdown(delivered_query)
    totals = _delivered_totals(categories)
    return {
        "scope": "volunteer",
        "tasks": {
            "assigned": sum(by_status.values()),
            "in_progress": by_status.get("active", 0),
            "delivered": by_status.get("completed", 0),
        },
        "delivered_kg": totals["delivered_kg"],
        "estimated_meals": totals["estimated_meals"],
        "categories": categories,
    }


def impact_for_user(user, date_range):
    """Return the dashboard data for this user's role, or None for an unknown role."""
    if user.role == "admin":
        return admin_impact(date_range)
    if user.role == "donor":
        return donor_impact(user, date_range)
    if user.role == "receiver":
        return receiver_impact(user, date_range)
    if user.role == "volunteer":
        return volunteer_impact(user, date_range)
    return None
