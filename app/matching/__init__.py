"""Matching engine between food donors and receivers.

Pure logic: this module reads donation and organisation data and returns scored
results. It never writes to the database and never calls the AI assistant.

evaluate() applies hard filters first (status, organisation type, radius,
intake capacity, accepted categories, enough time to travel). Eligible
organisations are scored 0-100 with weights in WEIGHTS.
"""
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

WEIGHTS = {"distance": 40, "urgency": 30, "quantity": 20, "category": 10}
MAX_RADIUS_KM = 15.0
TRAVEL_SPEED_KMH = 20.0
HANDLING_BUFFER_MIN = 20
KG_PER_PORTION = 0.5
RECEIVER_TYPES = ("ngo", "community_kitchen")


@dataclass
class MatchResult:
    organization: object
    eligible: bool
    score: int = 0
    distance_km: float = 0.0
    reasons: list = field(default_factory=list)
    rejections: list = field(default_factory=list)


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in kilometres."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def _donation_kg(donation):
    quantity = float(donation.quantity)
    return quantity if donation.unit == "kg" else quantity * KG_PER_PORTION


def _describe_minutes(minutes):
    if minutes < 60:
        return f"about {int(minutes)} minutes"
    hours = round(minutes / 60)
    return f"about {hours} hour{'' if hours == 1 else 's'}"


def _accepted_list(ngo):
    if not ngo.accepted_categories or not ngo.accepted_categories.strip():
        return None
    return {c.strip() for c in ngo.accepted_categories.split(",") if c.strip()}


def evaluate(donation, ngo, now):
    """Check one organisation against one donation and score it if eligible."""
    donor_org = donation.organization
    distance = haversine_km(
        donor_org.latitude, donor_org.longitude, ngo.latitude, ngo.longitude
    )
    kg = _donation_kg(donation)
    minutes_left = (donation.pickup_deadline - now).total_seconds() / 60.0
    travel_minutes = distance / TRAVEL_SPEED_KMH * 60.0
    needed_minutes = travel_minutes + HANDLING_BUFFER_MIN

    reasons = [f"About {distance:.1f} km away."]
    rejections = []

    # Hard filters: every failure is recorded.
    if donation.status != "posted":
        rejections.append(f"This donation is {donation.status}, so it is no longer open.")
    if ngo.org_type not in RECEIVER_TYPES:
        rejections.append(f"{ngo.name} is not a receiving organisation.")
    if distance > MAX_RADIUS_KM:
        rejections.append(
            f"{ngo.name} is {distance:.1f} km away, beyond the {MAX_RADIUS_KM:g} km limit."
        )

    if donation.unit != "kg":
        reasons.append(
            f"Assumption: {donation.quantity:g} portions is counted as about {kg:.1f} kg "
            f"at {KG_PER_PORTION:g} kg per portion."
        )
    if ngo.capacity_kg is None:
        reasons.append("No intake limit is recorded for this organisation.")
    elif kg <= ngo.capacity_kg:
        reasons.append("Fits your intake capacity.")
    else:
        rejections.append(
            f"About {kg:.1f} kg is more than {ngo.name} can take in one claim "
            f"({ngo.capacity_kg:g} kg)."
        )

    if not ngo.accepts_category(donation.category):
        rejections.append(f"{ngo.name} does not accept this food type.")
    elif _accepted_list(ngo) is None:
        reasons.append("Accepts all food types.")
    else:
        reasons.append("Matches your accepted food types.")

    if minutes_left <= 0:
        rejections.append("The pickup deadline has passed.")
    else:
        reasons.append(f"Pickup deadline in {_describe_minutes(minutes_left)}.")
        if minutes_left < needed_minutes:
            rejections.append(
                f"Not enough time to travel and hand over: about {round(minutes_left)} "
                f"minutes remain and about {round(needed_minutes)} minutes are needed."
            )

    if rejections:
        return MatchResult(
            organization=ngo,
            eligible=False,
            score=0,
            distance_km=distance,
            reasons=reasons,
            rejections=rejections,
        )

    # Scoring, only for eligible organisations.
    distance_sub = 100.0 * max(0.0, 1.0 - distance / MAX_RADIUS_KM)
    urgency_sub = 100.0 * max(0.0, 1.0 - minutes_left / (24 * 60))
    if ngo.capacity_kg is None:
        quantity_sub = 100.0
    else:
        target = ngo.capacity_kg * 0.6
        quantity_sub = 100.0 * max(0.0, 1.0 - abs(kg - target) / max(target, 1.0))
    accepted = _accepted_list(ngo)
    category_sub = 100.0 if accepted and donation.category in accepted else 50.0

    subscores = {
        "distance": distance_sub,
        "urgency": urgency_sub,
        "quantity": quantity_sub,
        "category": category_sub,
    }
    total = sum(WEIGHTS[k] * subscores[k] for k in WEIGHTS) / 100.0

    return MatchResult(
        organization=ngo,
        eligible=True,
        score=int(round(total)),
        distance_km=distance,
        reasons=reasons,
        rejections=[],
    )


def rank(donation, organizations, now):
    """Evaluate every organisation for a donation.

    Eligible results come first, highest score then shortest distance. Rejected
    results follow, sorted by organisation name.
    """
    results = [evaluate(donation, org, now) for org in organizations]
    eligible = sorted(
        (r for r in results if r.eligible),
        key=lambda r: (-r.score, r.distance_km),
    )
    rejected = sorted(
        (r for r in results if not r.eligible),
        key=lambda r: r.organization.name,
    )
    return eligible + rejected
