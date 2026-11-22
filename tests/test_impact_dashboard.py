"""Impact dashboard: real figures, role scoping and date filters.

One seeded dataset (see ``dataset``) is used throughout so every expected number
can be checked by hand.

    id  donor   status     food               NGO        volunteer
     1  donor   posted     10 kg
     2  donor   claimed     5 kg              NGO Hub
     3  donor   assigned    4 kg              NGO Hub    vol  (active)
     4  donor   delivered  20 kg cooked meal  NGO Hub    vol  (completed)
     5  donor   delivered  10 portions bakery Community  vol2 (completed)  -> 5 kg
     6  donor   expired     7 kg
     7  donor   cancelled   3 kg
     8  donor2  delivered   7 kg dairy        Community  vol  (completed)
     9  donor2  posted      2 kg
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.impact.service import (
    KG_PER_PORTION,
    admin_impact,
    compute_impact,
    donor_impact,
    impact_for_user,
    parse_date_range,
    receiver_impact,
    volunteer_impact,
)
from app.models import Claim, Donation, StatusEvent, User, VolunteerAssignment
from app.timeutil import IST, utcnow

NO_FILTER = parse_date_range(None, None)


def _user(email):
    return User.query.filter_by(email=email).one()


def _add_donation(
    donor_email,
    status="posted",
    quantity=10,
    unit="kg",
    category="cooked_meal",
    created=None,
    ngo_email=None,
    claim_status="active",
    volunteer_email=None,
    assignment_status="active",
):
    """Insert a donation (and optionally its claim and assignment) directly."""
    donor = _user(donor_email)
    when = created or utcnow()
    donation = Donation(
        donor_user_id=donor.id,
        organization_id=donor.organization_id,
        title=f"{category} {status}",
        category=category,
        quantity=quantity,
        unit=unit,
        prepared_at=when - timedelta(hours=1),
        pickup_deadline=when + timedelta(hours=5),
        pickup_zone="Z-01",
        pickup_address="Gate 2, Synthetic Hostel",
        storage="ambient",
        status=status,
        created_at=when,
        updated_at=when,
    )
    db.session.add(donation)
    db.session.flush()
    if ngo_email:
        ngo = _user(ngo_email)
        db.session.add(
            Claim(
                donation_id=donation.id,
                organization_id=ngo.organization_id,
                user_id=ngo.id,
                status=claim_status,
                created_at=when,
            )
        )
    if volunteer_email:
        volunteer = _user(volunteer_email)
        db.session.add(
            VolunteerAssignment(
                donation_id=donation.id,
                volunteer_user_id=volunteer.id,
                status=assignment_status,
                assigned_at=when,
                delivered_at=when if assignment_status == "completed" else None,
            )
        )
    db.session.commit()
    return donation.id


@pytest.fixture()
def dataset(world):
    with world.app_context():
        _add_donation("donor@t.com", "posted", 10)
        _add_donation("donor@t.com", "claimed", 5, ngo_email="ngo@t.com")
        _add_donation(
            "donor@t.com", "assigned", 4, ngo_email="ngo@t.com",
            volunteer_email="vol@t.com", assignment_status="active",
        )
        _add_donation(
            "donor@t.com", "delivered", 20, ngo_email="ngo@t.com",
            volunteer_email="vol@t.com", assignment_status="completed",
        )
        _add_donation(
            "donor@t.com", "delivered", 10, unit="portions", category="bakery",
            ngo_email="ngo2@t.com", volunteer_email="vol2@t.com",
            assignment_status="completed",
        )
        _add_donation("donor@t.com", "expired", 7)
        _add_donation("donor@t.com", "cancelled", 3)
        _add_donation(
            "donor2@t.com", "delivered", 7, category="dairy", ngo_email="ngo2@t.com",
            volunteer_email="vol@t.com", assignment_status="completed",
        )
        _add_donation("donor2@t.com", "posted", 2)
    return world


# ---------- admin: whole platform ----------

def test_admin_summary_counts_and_kg(dataset):
    with dataset.app_context():
        s = admin_impact(NO_FILTER)["summary"]
    assert s["total"] == 9
    assert s["pending"] == 2
    assert s["claimed"] == 2  # claimed + assigned
    assert s["delivered"] == 3
    assert s["expired"] == 1
    assert s["cancelled"] == 1
    # 20 kg + (10 portions * 0.5) + 7 kg
    assert KG_PER_PORTION == 0.5
    assert s["delivered_kg"] == 32.0
    assert s["estimated_meals"] == 80
    # Every donation is in exactly one bucket.
    assert s["pending"] + s["claimed"] + s["delivered"] + s["expired"] + s["cancelled"] == s["total"]


def test_admin_status_rows_cover_every_status(dataset):
    with dataset.app_context():
        rows = admin_impact(NO_FILTER)["summary"]["status_rows"]
    assert {r["status"] for r in rows} == {
        "posted", "claimed", "assigned", "collected", "delivered", "expired", "cancelled",
    }
    assert sum(r["count"] for r in rows) == 9


def test_admin_category_breakdown(dataset):
    with dataset.app_context():
        categories = admin_impact(NO_FILTER)["categories"]
    by_key = {c["key"]: c for c in categories}
    assert by_key["cooked_meal"]["kg"] == 20.0 and by_key["cooked_meal"]["count"] == 1
    assert by_key["bakery"]["kg"] == 5.0
    assert by_key["dairy"]["kg"] == 7.0
    assert categories[0]["key"] == "cooked_meal"  # largest first
    assert categories[0]["percent"] == 100


def test_admin_volunteers_and_ngos(dataset):
    with dataset.app_context():
        data = admin_impact(NO_FILTER)
    assert data["volunteers"] == {"accounts": 2, "engaged": 2, "deliveries": 3}  # inactive volunteer excluded
    assert data["donors_posting"] == 2
    ngos = data["ngos"]
    assert ngos["registered"] == 2  # donor organisations are not counted
    assert ngos["claimed"] == 2
    assert ngos["received"] == 2
    top = ngos["top"]
    assert [n["name"] for n in top] == ["NGO Hub", "Community Kitchen"]
    assert top[0]["claims"] == 3 and top[0]["deliveries"] == 1 and top[0]["kg"] == 20.0
    assert top[1]["claims"] == 2 and top[1]["deliveries"] == 2 and top[1]["kg"] == 12.0


def test_ngo_that_only_claims_is_participating_but_has_not_received(world):
    with world.app_context():
        _add_donation("donor@t.com", "claimed", 5, ngo_email="ngo@t.com")
        ngos = admin_impact(NO_FILTER)["ngos"]
    assert ngos["claimed"] == 1
    assert ngos["received"] == 0
    assert ngos["top"][0]["deliveries"] == 0 and ngos["top"][0]["kg"] == 0.0


def test_empty_database_returns_zeros_not_errors(world):
    with world.app_context():
        data = admin_impact(NO_FILTER)
    assert data["summary"]["total"] == 0
    assert data["summary"]["delivered_kg"] == 0
    assert data["categories"] == []
    assert data["ngos"]["top"] == []
    assert data["volunteers"]["engaged"] == 0
    assert all(r["percent"] == 0 for r in data["summary"]["status_rows"])


def test_delivered_total_matches_existing_compute_impact(dataset):
    with dataset.app_context():
        legacy = compute_impact()
        new = admin_impact(NO_FILTER)["summary"]
    assert legacy["delivered_count"] == new["delivered"]
    assert legacy["delivered_kg"] == new["delivered_kg"]


# ---------- donor: own data only ----------

def test_donor_sees_only_own_donations(dataset):
    with dataset.app_context():
        d1 = donor_impact(_user("donor@t.com"), NO_FILTER)
        d2 = donor_impact(_user("donor2@t.com"), NO_FILTER)
    s1, s2 = d1["summary"], d2["summary"]
    assert (s1["total"], s1["pending"], s1["claimed"], s1["delivered"]) == (7, 1, 2, 2)
    assert (s1["expired"], s1["cancelled"], s1["delivered_kg"]) == (1, 1, 25.0)
    assert (s2["total"], s2["pending"], s2["delivered"], s2["delivered_kg"]) == (2, 1, 1, 7.0)
    assert s1["total"] + s2["total"] == 9


def test_donor_partners_are_the_ngos_that_received_food(dataset):
    with dataset.app_context():
        d1 = donor_impact(_user("donor@t.com"), NO_FILTER)
        d2 = donor_impact(_user("donor2@t.com"), NO_FILTER)
    assert d1["partner_count"] == 2
    assert [(p["name"], p["kg"]) for p in d1["partners"]] == [
        ("NGO Hub", 20.0),
        ("Community Kitchen", 5.0),
    ]
    assert [(p["name"], p["kg"]) for p in d2["partners"]] == [("Community Kitchen", 7.0)]


def test_donor_with_no_donations_gets_zeros(world):
    with world.app_context():
        data = donor_impact(_user("donor_noorg@t.com"), NO_FILTER)
    assert data["summary"]["total"] == 0 and data["partners"] == []


# ---------- NGO / receiver: own claims only ----------

def test_receiver_claim_breakdown_and_food_received(dataset):
    with dataset.app_context():
        hub = receiver_impact(_user("ngo@t.com"), NO_FILTER)
        kitchen = receiver_impact(_user("ngo2@t.com"), NO_FILTER)
    assert hub["claims"] == {"made": 3, "in_progress": 2, "delivered": 1, "expired": 0, "released": 0}
    assert hub["delivered_kg"] == 20.0 and hub["estimated_meals"] == 50
    assert [p["name"] for p in hub["partners"]] == ["Hostel Mess A"]
    assert kitchen["claims"]["made"] == 2 and kitchen["claims"]["delivered"] == 2
    assert kitchen["delivered_kg"] == 12.0
    assert kitchen["partner_count"] == 2


def test_receiver_released_claim_is_counted_but_not_as_delivered(dataset):
    with dataset.app_context():
        _add_donation("donor@t.com", "posted", 6, ngo_email="ngo2@t.com", claim_status="released")
        kitchen = receiver_impact(_user("ngo2@t.com"), NO_FILTER)
    assert kitchen["claims"]["made"] == 3
    assert kitchen["claims"]["released"] == 1
    assert kitchen["claims"]["delivered"] == 2
    assert kitchen["delivered_kg"] == 12.0


def test_receiver_without_organisation_gets_a_prompt_not_an_error(world):
    with world.app_context():
        data = receiver_impact(_user("ngo_noorg@t.com"), NO_FILTER)
    assert data == {"scope": "receiver", "needs_profile": True}


# ---------- volunteer: own tasks only ----------

def test_volunteer_sees_only_own_tasks(dataset):
    with dataset.app_context():
        vera = volunteer_impact(_user("vol@t.com"), NO_FILTER)
        victor = volunteer_impact(_user("vol2@t.com"), NO_FILTER)
    assert vera["tasks"] == {"assigned": 3, "in_progress": 1, "delivered": 2}
    assert vera["delivered_kg"] == 27.0
    assert victor["tasks"] == {"assigned": 1, "in_progress": 0, "delivered": 1}
    assert victor["delivered_kg"] == 5.0


def test_removed_assignments_are_not_counted(world):
    with world.app_context():
        _add_donation(
            "donor@t.com", "claimed", 5, ngo_email="ngo@t.com",
            volunteer_email="vol@t.com", assignment_status="removed",
        )
        assert volunteer_impact(_user("vol@t.com"), NO_FILTER)["tasks"]["assigned"] == 0
        assert admin_impact(NO_FILTER)["volunteers"]["engaged"] == 0


def test_impact_for_user_dispatches_by_role_and_rejects_unknown(dataset):
    with dataset.app_context():
        assert impact_for_user(_user("admin@t.com"), NO_FILTER)["scope"] == "admin"
        assert impact_for_user(_user("donor@t.com"), NO_FILTER)["scope"] == "donor"
        assert impact_for_user(_user("ngo@t.com"), NO_FILTER)["scope"] == "receiver"
        assert impact_for_user(_user("vol@t.com"), NO_FILTER)["scope"] == "volunteer"
        stranger = User(name="X", email="x@t.com", role="mystery", password_hash="x")
        assert impact_for_user(stranger, NO_FILTER) is None


# ---------- date filters ----------

def _ist_date(naive_utc):
    return (naive_utc + timedelta(hours=5, minutes=30)).date().isoformat()


@pytest.fixture()
def with_old_delivery(dataset):
    """Add a 100 kg delivery from 40 days ago on top of the base dataset."""
    old = utcnow() - timedelta(days=40)
    with dataset.app_context():
        _add_donation(
            "donor@t.com", "delivered", 100, created=old, ngo_email="ngo@t.com",
            volunteer_email="vol@t.com", assignment_status="completed",
        )
    return dataset, old


def test_date_filter_excludes_old_data_for_every_figure(with_old_delivery):
    app, old = with_old_delivery
    last_week = parse_date_range(_ist_date(utcnow() - timedelta(days=7)), _ist_date(utcnow()))
    with app.app_context():
        everything = admin_impact(NO_FILTER)
        recent = admin_impact(last_week)
        hub = receiver_impact(_user("ngo@t.com"), last_week)
        mine = donor_impact(_user("donor@t.com"), last_week)
        vera = volunteer_impact(_user("vol@t.com"), last_week)
    assert everything["summary"]["total"] == 10 and everything["summary"]["delivered_kg"] == 132.0
    assert recent["summary"]["total"] == 9 and recent["summary"]["delivered_kg"] == 32.0
    assert hub["delivered_kg"] == 20.0 and hub["claims"]["made"] == 3
    assert mine["summary"]["delivered_kg"] == 25.0
    assert vera["tasks"]["assigned"] == 3 and vera["delivered_kg"] == 27.0


def test_date_filter_can_isolate_a_past_day(with_old_delivery):
    app, old = with_old_delivery
    day = _ist_date(old)
    only_old = parse_date_range(day, day)
    with app.app_context():
        data = admin_impact(only_old)
    assert data["summary"]["total"] == 1
    assert data["summary"]["delivered"] == 1 and data["summary"]["delivered_kg"] == 100.0
    assert data["volunteers"]["engaged"] == 1 and data["volunteers"]["deliveries"] == 1
    assert data["ngos"]["claimed"] == 1 and data["donors_posting"] == 1


def test_date_filter_uses_ist_day_boundaries(world):
    midnight_ist = datetime(2026, 10, 31, 18, 30)  # 1 Nov 00:00 IST
    with world.app_context():
        _add_donation("donor@t.com", "posted", 1, created=midnight_ist)
        _add_donation("donor@t.com", "posted", 1, created=midnight_ist - timedelta(minutes=1))
        _add_donation("donor@t.com", "posted", 1, created=midnight_ist + timedelta(days=1) - timedelta(minutes=1))
        _add_donation("donor@t.com", "posted", 1, created=midnight_ist + timedelta(days=1))
        one_day = parse_date_range("2026-11-01", "2026-11-01")
        assert admin_impact(one_day)["summary"]["total"] == 2  # 00:00 IST and 23:59 IST
        assert admin_impact(parse_date_range("2026-11-01", None))["summary"]["total"] == 3
        assert admin_impact(parse_date_range(None, "2026-11-01"))["summary"]["total"] == 3


def test_parse_date_range_validation():
    ok = parse_date_range("2026-11-01", "2026-11-20")
    assert ok.is_filtered and not ok.errors
    assert ok.label == "01 Nov 2026 to 20 Nov 2026"
    assert not parse_date_range("", "").is_filtered
    assert parse_date_range(None, None).label == "All time"

    bad = parse_date_range("garbage", "2026-11-05")
    assert bad.start is None and bad.end is not None and len(bad.errors) == 1
    assert parse_date_range("2026-02-30", None).errors  # impossible calendar date

    reversed_range = parse_date_range("2026-11-10", "2026-11-01")
    assert not reversed_range.is_filtered and len(reversed_range.errors) == 1


# ---------- HTTP: access control ----------

def test_anonymous_user_is_sent_to_login(make_client):
    resp = make_client().get("/impact")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "email, heading",
    [
        ("admin@t.com", "Impact dashboard"),
        ("donor@t.com", "Your impact"),
        ("ngo@t.com", "Your NGO&#39;s impact"),
        ("vol@t.com", "Your impact"),
    ],
)
def test_every_role_can_open_its_dashboard(dataset, make_client, email, heading):
    resp = make_client(email).get("/impact")
    assert resp.status_code == 200
    assert heading in resp.get_data(as_text=True)


def test_admin_dashboard_shows_platform_metrics(dataset, make_client):
    page = make_client("admin@t.com").get("/impact").get_data(as_text=True)
    for label in (
        "Total donations", "Pending", "Claimed", "Delivered", "Expired",
        "Food rescued", "Active volunteers", "NGOs participating", "NGO participation",
    ):
        assert label in page, label
    assert "32.0 kg" in page
    assert "NGO Hub" in page and "Community Kitchen" in page


def test_donor_dashboard_is_scoped_and_hides_platform_panels(dataset, make_client):
    page = make_client("donor2@t.com").get("/impact").get_data(as_text=True)
    assert "NGOs that received your food" in page
    assert "7.0 kg" in page
    assert "25.0 kg" not in page and "32.0 kg" not in page  # other donor / platform totals
    assert "Active volunteers" not in page and "NGO participation" not in page


def test_ngo_dashboard_is_scoped(dataset, make_client):
    page = make_client("ngo@t.com").get("/impact").get_data(as_text=True)
    assert "Donors who supplied you" in page and "Hostel Mess A" in page
    assert "20.0 kg" in page
    assert "Community Kitchen" not in page  # another NGO's name never appears
    assert "NGO participation" not in page


def test_ngo_without_organisation_sees_setup_prompt(world, make_client):
    resp = make_client("ngo_noorg@t.com").get("/impact")
    assert resp.status_code == 200
    assert "Add your organisation first" in resp.get_data(as_text=True)


def test_volunteer_dashboard_shows_own_deliveries(dataset, make_client):
    page = make_client("vol2@t.com").get("/impact").get_data(as_text=True)
    assert "Deliveries completed" in page and "5.0 kg" in page
    assert "27.0 kg" not in page


# ---------- HTTP: date filter form ----------

def test_date_filter_applies_through_the_query_string(with_old_delivery, make_client):
    app, old = with_old_delivery
    client = make_client("admin@t.com")
    unfiltered = client.get("/impact").get_data(as_text=True)
    assert "132.0 kg" in unfiltered and "All time" in unfiltered

    day = _ist_date(old)
    filtered = client.get(f"/impact?start={day}&end={day}").get_data(as_text=True)
    assert "100.0 kg" in filtered and "132.0 kg" not in filtered
    assert f'value="{day}"' in filtered  # the form remembers the chosen dates


def test_invalid_dates_show_a_message_and_fall_back_to_all_time(dataset, make_client):
    client = make_client("admin@t.com")
    page = client.get("/impact?start=not-a-date").get_data(as_text=True)
    assert "date is not valid" in page and "32.0 kg" in page

    reversed_page = client.get("/impact?start=2026-11-10&end=2026-11-01").get_data(as_text=True)
    assert "must not be after the end date" in reversed_page and "32.0 kg" in reversed_page


def test_date_filter_values_are_escaped(dataset, make_client):
    page = make_client("admin@t.com").get('/impact?start="><script>alert(1)</script>').get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page


# ---------- the dashboard never changes data; existing pages still work ----------

def test_viewing_the_dashboard_changes_nothing(dataset, make_client):
    with dataset.app_context():
        before = (
            [(d.id, d.status) for d in Donation.query.order_by(Donation.id)],
            StatusEvent.query.count(),
            Claim.query.count(),
            VolunteerAssignment.query.count(),
        )
    for email in ("admin@t.com", "donor@t.com", "ngo@t.com", "vol@t.com"):
        assert make_client(email).get("/impact").status_code == 200
    with dataset.app_context():
        after = (
            [(d.id, d.status) for d in Donation.query.order_by(Donation.id)],
            StatusEvent.query.count(),
            Claim.query.count(),
            VolunteerAssignment.query.count(),
        )
    assert before == after


def test_dashboard_redirects_and_existing_pages_still_work(dataset, make_client):
    assert make_client("admin@t.com").get("/dashboard").headers["Location"].endswith("/impact")
    assert make_client("donor@t.com").get("/donate").status_code == 200
    assert make_client("ngo@t.com").get("/claims").status_code == 200
    assert make_client("vol@t.com").get("/volunteer").status_code == 200
    assert make_client("admin@t.com").get("/admin/assignments").status_code == 200


def test_impact_link_is_in_the_navigation_for_every_role(dataset, make_client):
    for email, page in (
        ("donor@t.com", "/donate"),
        ("ngo@t.com", "/claims"),
        ("vol@t.com", "/volunteer"),
        ("admin@t.com", "/impact"),
    ):
        html = make_client(email).get(page).get_data(as_text=True)
        assert 'href="/impact"' in html, email
