"""NGO / receiver routes: find food, claim it, follow claims and release them."""
from flask import flash, redirect, render_template, request, session, url_for

from app.auth.decorators import role_required
from app.donations.validation import CATEGORY_LABELS, STORAGE_OPTIONS
from app.extensions import db
from app.matching import evaluate
from app.models import Claim, Donation, User
from app.receivers import bp
from app.services.claims import ClaimError
from app.services.claims import claim_donation as claim_donation_service
from app.services.claims import release_claim as release_claim_service
from app.timeutil import format_local, utcnow
from app.zones import ZONES

ZONE_CODES = {code for code, _label in ZONES}
ZONE_LABELS = dict(ZONES)
MAX_QUERY_LENGTH = 60
FIND_LIMIT = 200


def _current_user():
    """Return the logged-in User, or None."""
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _escape_like(text):
    """Escape LIKE wildcards so user text is matched literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@bp.route("/find-food")
@role_required("receiver")
def find_food():
    user = _current_user()
    filters = {"q": "", "category": "", "zone": ""}
    org = user.organization if user.organization_id is not None else None

    if org is None:
        return render_template(
            "receivers/find.html",
            needs_profile=True,
            results=[],
            hidden_count=0,
            filters=filters,
            categories=CATEGORY_LABELS,
            zones=ZONES,
            my_active_claims=0,
        )

    q = (request.args.get("q") or "").strip()[:MAX_QUERY_LENGTH]
    category = request.args.get("category", "")
    if category not in CATEGORY_LABELS:
        category = ""
    zone = request.args.get("zone", "")
    if zone not in ZONE_CODES:
        zone = ""
    filters = {"q": q, "category": category, "zone": zone}

    now = utcnow()
    query = Donation.query.filter(
        Donation.status == "posted",
        Donation.pickup_deadline > now,
    )
    if category:
        query = query.filter(Donation.category == category)
    if zone:
        query = query.filter(Donation.pickup_zone == zone)
    if q:
        query = query.filter(
            Donation.title.ilike(f"%{_escape_like(q)}%", escape="\\")
        )
    candidates = query.order_by(Donation.pickup_deadline).limit(FIND_LIMIT).all()

    results = []
    for donation in candidates:
        result = evaluate(donation, org, now)
        if result.eligible:
            results.append((donation, result))
    hidden_count = len(candidates) - len(results)
    results.sort(key=lambda pair: (-pair[1].score, pair[1].distance_km))

    my_active_claims = Claim.query.filter_by(
        organization_id=org.id, status="active"
    ).count()

    return render_template(
        "receivers/find.html",
        needs_profile=False,
        results=results,
        hidden_count=hidden_count,
        filters=filters,
        categories=CATEGORY_LABELS,
        zones=ZONES,
        zone_labels=ZONE_LABELS,
        storage_labels=STORAGE_OPTIONS,
        my_active_claims=my_active_claims,
    )


@bp.route("/find-food/<int:donation_id>/claim", methods=["POST"])
@role_required("receiver")
def claim_donation(donation_id):
    try:
        claim_donation_service(donation_id, _current_user())
    except ClaimError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("receivers.find_food"))
    flash("Claimed. Pick-up details are now visible in My claims.", "success")
    return redirect(url_for("receivers.my_claims"))


@bp.route("/claims")
@role_required("receiver")
def my_claims():
    user = _current_user()
    claims = []
    if user.organization_id is not None:
        claims = (
            Claim.query.filter_by(organization_id=user.organization_id)
            .order_by(Claim.created_at.desc(), Claim.id.desc())
            .all()
        )

    rows = []
    for claim in claims:
        donation = claim.donation
        assignment = donation.active_assignment
        volunteer_name = None
        if assignment is not None:
            volunteer = db.session.get(User, assignment.volunteer_user_id)
            volunteer_name = volunteer.name if volunteer is not None else None
        rows.append(
            {
                "claim": claim,
                "donation": donation,
                "pickup_by": format_local(donation.pickup_deadline),
                "donor_name": donation.organization.name,
                "volunteer_name": volunteer_name,
                "quantity_label": f"{donation.quantity:g} {donation.unit}",
                "can_release": claim.status == "active" and donation.status == "claimed",
                "can_assign": claim.status == "active" and donation.status == "claimed",
                "can_unassign": claim.status == "active" and donation.status == "assigned",
            }
        )

    summary = {
        "active": sum(1 for r in rows if r["claim"].status == "active"),
        "in_delivery": sum(
            1 for r in rows if r["donation"].status in ("assigned", "collected")
        ),
        "delivered": sum(1 for r in rows if r["donation"].status == "delivered"),
    }
    from app.volunteers.routes import active_volunteers

    return render_template(
        "receivers/claims.html",
        rows=rows,
        summary=summary,
        volunteers=active_volunteers(),
    )


@bp.route("/claims/<int:claim_id>/release", methods=["POST"])
@role_required("receiver")
def release_claim(claim_id):
    try:
        release_claim_service(claim_id, _current_user())
    except ClaimError as exc:
        flash(str(exc), "danger")
    else:
        flash("Claim released. The donation is open again.", "info")
    return redirect(url_for("receivers.my_claims"))
