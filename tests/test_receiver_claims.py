"""NGO side: browse, filter, claim, duplicate-claim prevention, release, permissions."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Claim, Donation, Organization, StatusEvent
from app.timeutil import utcnow
from app.tracking import expire_overdue
from tests.conftest import Client, donation_form, to_local


def _claims(app, donation_id=None):
    with app.app_context():
        q = Claim.query
        if donation_id:
            q = q.filter_by(donation_id=donation_id)
        return [(c.organization.name, c.status) for c in q.order_by(Claim.id).all()]


def _status(app, donation_id):
    with app.app_context():
        return db.session.get(Donation, donation_id).status


# ---------- browse ----------

def test_ngo_sees_posted_donations(make_client, post_donation):
    post_donation(title="Visible biryani")
    page = make_client("ngo@t.com").get("/find-food").get_data(as_text=True)
    assert "Visible biryani" in page and "Claim this food" in page
    assert "Gate 2, Synthetic Hostel" not in page  # address stays hidden until claimed


def test_browse_filters(make_client, post_donation):
    post_donation(title="Fresh apples", category="fresh_produce", quantity="5")
    post_donation(title="Hot dal", category="cooked_meal", quantity="5")
    client = make_client("ngo@t.com")
    by_cat = client.get("/find-food?category=fresh_produce").get_data(as_text=True)
    assert "Fresh apples" in by_cat and "Hot dal" not in by_cat
    by_q = client.get("/find-food?q=dal").get_data(as_text=True)
    assert "Hot dal" in by_q and "Fresh apples" not in by_q
    by_zone = client.get("/find-food?zone=Z-02").get_data(as_text=True)
    assert "Hot dal" not in by_zone
    # Junk filters are ignored, not an error; LIKE wildcards are matched literally.
    assert client.get("/find-food?category=zzz&zone=zzz").status_code == 200
    assert "Hot dal" not in client.get("/find-food?q=%25").get_data(as_text=True)


def test_ineligible_donations_are_hidden(world, make_client, post_donation):
    post_donation(title="Huge batch", quantity="400")  # over the NGO's 300 kg capacity
    page = make_client("ngo@t.com").get("/find-food").get_data(as_text=True)
    assert "Huge batch" not in page and "hidden" in page

    with world.app_context():
        ngo = Organization.query.filter_by(name="NGO Hub").one()
        ngo.accepted_categories = "bakery"
        db.session.commit()
    post_donation(title="Cooked lunch", quantity="5", category="cooked_meal")
    assert "Cooked lunch" not in make_client("ngo@t.com").get("/find-food").get_data(as_text=True)


def test_only_posted_donations_are_listed(world, make_client, post_donation):
    donation_id = post_donation(title="Soon gone")
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    assert "Soon gone" not in make_client("ngo2@t.com").get("/find-food").get_data(as_text=True)


def test_receiver_without_organisation(make_client, post_donation):
    donation_id = post_donation()
    client = make_client("ngo_noorg@t.com")
    page = client.get("/find-food").get_data(as_text=True)
    assert "Add your organisation first" in page
    assert client.get("/claims").status_code == 200
    resp = client.post(f"/find-food/{donation_id}/claim", follow_redirects=True)
    assert b"Only NGO accounts with an organisation" in resp.data


# ---------- claim ----------

def test_ngo_can_claim_and_it_persists(world, make_client, post_donation):
    donation_id = post_donation()
    resp = make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/claims")
    assert _status(world, donation_id) == "claimed"
    assert _claims(world, donation_id) == [("NGO Hub", "active")]
    with world.app_context():
        events = StatusEvent.query.filter_by(donation_id=donation_id).order_by(StatusEvent.id).all()
        assert [(e.from_status, e.to_status, e.actor_role) for e in events] == [
            (None, "posted", "donor"),
            ("posted", "claimed", "receiver"),
        ]


def test_claims_page_shows_pickup_address(make_client, post_donation):
    donation_id = post_donation(title="Claimed lunch")
    client = make_client("ngo@t.com")
    client.post(f"/find-food/{donation_id}/claim")
    page = client.get("/claims").get_data(as_text=True)
    assert "Claimed lunch" in page and "Gate 2, Synthetic Hostel" in page and "Release" in page
    # The other NGO sees none of it.
    assert "Claimed lunch" not in make_client("ngo2@t.com").get("/claims").get_data(as_text=True)


def test_second_ngo_cannot_claim_an_already_claimed_donation(world, make_client, post_donation):
    donation_id = post_donation()
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    resp = make_client("ngo2@t.com").post(f"/find-food/{donation_id}/claim", follow_redirects=True)
    assert b"no longer available" in resp.data
    assert _claims(world, donation_id) == [("NGO Hub", "active")]


def test_same_ngo_cannot_claim_twice(world, make_client, post_donation):
    donation_id = post_donation()
    client = make_client("ngo@t.com")
    client.post(f"/find-food/{donation_id}/claim")
    resp = client.post(f"/find-food/{donation_id}/claim", follow_redirects=True)
    assert b"no longer available" in resp.data
    assert _claims(world, donation_id) == [("NGO Hub", "active")]
    with world.app_context():
        # No duplicate status event either.
        assert StatusEvent.query.filter_by(donation_id=donation_id, to_status="claimed").count() == 1


def test_database_itself_rejects_two_active_claims(world, post_donation):
    """Defence in depth: the partial unique index blocks duplicates even if the app code is bypassed."""
    donation_id = post_donation()
    with world.app_context():
        org_ids = [o.id for o in Organization.query.filter(Organization.org_type.in_(("ngo", "community_kitchen")))]
        db.session.add(Claim(donation_id=donation_id, organization_id=org_ids[0], user_id=1, status="active"))
        db.session.commit()
        db.session.add(Claim(donation_id=donation_id, organization_id=org_ids[1], user_id=1, status="active"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        # Released claims are history and do not count.
        db.session.add(Claim(donation_id=donation_id, organization_id=org_ids[1], user_id=1, status="released"))
        db.session.commit()


def test_concurrent_claims_produce_exactly_one_winner(world, post_donation):
    donation_id = post_donation()
    emails = ["ngo@t.com", "ngo2@t.com"] * 3
    clients = [Client(world) for _ in emails]
    for client, email in zip(clients, emails):
        assert client.login(email).status_code == 302
    barrier = threading.Barrier(len(clients))
    results, errors = [], []

    def attempt(client):
        try:
            barrier.wait(timeout=10)
            results.append(client.post(f"/find-food/{donation_id}/claim").status_code)
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    claims = _claims(world, donation_id)
    assert [s for _n, s in claims].count("active") == 1, claims
    assert _status(world, donation_id) == "claimed"
    with world.app_context():
        assert StatusEvent.query.filter_by(donation_id=donation_id, to_status="claimed").count() == 1


def test_claim_nonexistent_donation(make_client):
    resp = make_client("ngo@t.com").post("/find-food/9999/claim", follow_redirects=True)
    assert b"no longer available" in resp.data


def test_cannot_claim_cancelled_or_expired_donation(world, make_client, post_donation):
    cancelled = post_donation(title="Cancelled one")
    make_client("donor@t.com").post(f"/donate/{cancelled}/cancel")
    resp = make_client("ngo@t.com").post(f"/find-food/{cancelled}/claim", follow_redirects=True)
    assert b"no longer available" in resp.data

    overdue = post_donation(title="Overdue one")
    with world.app_context():
        d = db.session.get(Donation, overdue)
        d.pickup_deadline = utcnow() - timedelta(minutes=5)
        db.session.commit()
    resp = make_client("ngo@t.com").post(f"/find-food/{overdue}/claim", follow_redirects=True)
    assert b"deadline has passed" in resp.data
    assert _claims(world, overdue) == []
    # The sweeper then expires it.
    with world.app_context():
        assert expire_overdue(utcnow()) == 1
        db.session.commit()
        assert db.session.get(Donation, overdue).status == "expired"


def test_claim_rejected_when_ngo_cannot_take_it(world, make_client, post_donation):
    donation_id = post_donation(quantity="400")
    resp = make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim", follow_redirects=True)
    assert b"more than NGO Hub can take" in resp.data
    assert _claims(world, donation_id) == [] and _status(world, donation_id) == "posted"


@pytest.mark.parametrize("email", ["donor@t.com", "vol@t.com", "admin@t.com"])
def test_other_roles_cannot_claim_or_browse(world, make_client, post_donation, email):
    donation_id = post_donation()
    client = make_client(email)
    assert client.get("/find-food").status_code == 302
    assert client.get("/claims").status_code == 302
    client.post(f"/find-food/{donation_id}/claim")
    assert _claims(world, donation_id) == [] and _status(world, donation_id) == "posted"


def test_claim_requires_csrf_token(world, make_client, post_donation):
    donation_id = post_donation()
    resp = make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim", csrf=False)
    assert resp.status_code == 400 and _claims(world, donation_id) == []


def test_claim_is_post_only(make_client, post_donation):
    donation_id = post_donation()
    assert make_client("ngo@t.com").get(f"/find-food/{donation_id}/claim").status_code == 405


# ---------- release ----------

def test_release_reopens_donation_for_other_ngos(world, make_client, post_donation):
    donation_id = post_donation()
    ngo = make_client("ngo@t.com")
    ngo.post(f"/find-food/{donation_id}/claim")
    with world.app_context():
        claim_id = Claim.query.one().id
    resp = ngo.post(f"/claims/{claim_id}/release")
    assert resp.status_code == 302
    assert _status(world, donation_id) == "posted"
    assert _claims(world, donation_id) == [("NGO Hub", "released")]

    # Another NGO can now claim it; history keeps both rows, only one is active.
    make_client("ngo2@t.com").post(f"/find-food/{donation_id}/claim")
    assert _claims(world, donation_id) == [("NGO Hub", "released"), ("Community Kitchen", "active")]
    assert _status(world, donation_id) == "claimed"


def test_cannot_release_someone_elses_or_inactive_claim(world, make_client, post_donation):
    donation_id = post_donation()
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    with world.app_context():
        claim_id = Claim.query.one().id
    resp = make_client("ngo2@t.com").post(f"/claims/{claim_id}/release", follow_redirects=True)
    assert b"cannot be released" in resp.data
    assert make_client("donor@t.com").post(f"/claims/{claim_id}/release").status_code == 302
    assert _claims(world, donation_id) == [("NGO Hub", "active")]

    # Releasing twice is refused.
    owner = make_client("ngo@t.com")
    owner.post(f"/claims/{claim_id}/release")
    resp = owner.post(f"/claims/{claim_id}/release", follow_redirects=True)
    assert b"cannot be released" in resp.data


def test_released_claim_shows_in_history(make_client, post_donation):
    donation_id = post_donation(title="Back and forth")
    ngo = make_client("ngo@t.com")
    ngo.post(f"/find-food/{donation_id}/claim")
    ngo.post("/claims/1/release")
    page = ngo.get("/claims").get_data(as_text=True)
    assert "Back and forth" in page and "Released" in page
    # Pickup address is only shown while the claim is active.
    assert "Gate 2, Synthetic Hostel" not in page
