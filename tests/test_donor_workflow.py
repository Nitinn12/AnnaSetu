"""Donor side: create, validate, persist, edit, cancel, permissions."""
import io
from datetime import timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import Claim, Donation, StatusEvent
from app.timeutil import utcnow
from tests.conftest import donation_form, to_local


def _donation(app, donation_id):
    with app.app_context():
        return db.session.get(Donation, donation_id)


# ---------- create + persistence ----------

def test_donor_can_post_and_data_persists(world, make_client):
    client = make_client("donor@t.com")
    resp = client.post("/donate/new", donation_form(title="Dal & rice", quantity="12.5"))
    assert resp.status_code == 302 and "/donations/" in resp.headers["Location"]

    with world.app_context():
        d = Donation.query.one()
        assert (d.title, d.quantity, d.unit, d.status) == ("Dal & rice", 12.5, "kg", "posted")
        assert d.pickup_zone == "Z-01"  # taken from the donor's organisation
        assert d.organization.name == "Hostel Mess A"
        events = StatusEvent.query.filter_by(donation_id=d.id).all()
        assert [(e.from_status, e.to_status) for e in events] == [(None, "posted")]


def test_data_survives_app_restart(world, tmp_path, post_donation):
    donation_id = post_donation(title="Persistent curry")
    # A brand-new app object pointed at the same database file.
    app2 = create_app(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}"}
    )
    with app2.app_context():
        assert db.session.get(Donation, donation_id).title == "Persistent curry"


def test_donor_list_and_detail_render(make_client, post_donation):
    donation_id = post_donation(title="Listed item")
    client = make_client("donor@t.com")
    assert b"Listed item" in client.get("/donate").data
    resp = client.get(f"/donations/{donation_id}")
    assert resp.status_code == 200 and b"Status history" in resp.data


def test_donor_without_organisation_is_sent_to_profile(make_client):
    client = make_client("donor_noorg@t.com")
    resp = client.get("/donate/new")
    assert resp.status_code == 302 and "/profile/organization" in resp.headers["Location"]
    resp = client.post("/donate/new", donation_form())
    assert resp.status_code == 302 and "/profile/organization" in resp.headers["Location"]


# ---------- validation ----------

def _future(hours):
    return to_local(utcnow() + timedelta(hours=hours))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"title": ""}, "Title is required"),
        ({"title": "x" * 121}, "120 characters"),
        ({"category": "pizza"}, "valid food category"),
        ({"quantity": "0"}, "greater than 0"),
        ({"quantity": "-3"}, "greater than 0"),
        ({"quantity": "abc"}, "greater than 0"),
        ({"quantity": "nan"}, "greater than 0"),
        ({"quantity": "inf"}, "greater than 0"),
        ({"quantity": "99999"}, "5000 or less"),
        ({"unit": "litres"}, "kg or portions"),
        ({"servings": "0"}, "whole number"),
        ({"servings": "1.5"}, "whole number"),
        ({"storage": "lava"}, "valid storage"),
        ({"pickup_address": ""}, "Pickup address is required"),
        ({"allergens": "a" * 256}, "255 characters"),
        ({"description": "d" * 1001}, "1000 characters"),
        ({"prepared_at": ""}, "valid preparation"),
        ({"prepared_at": _future(3)}, "future"),
        ({"pickup_deadline": to_local(utcnow() - timedelta(hours=1))}, "must be in the future"),
        ({"pickup_deadline": _future(49)}, "within 48 hours"),
        ({"pickup_deadline": "not-a-date"}, "valid pickup deadline"),
    ],
)
def test_invalid_donation_is_rejected_and_not_saved(world, make_client, overrides, message):
    client = make_client("donor@t.com")
    resp = client.post("/donate/new", donation_form(**overrides))
    assert resp.status_code == 200  # form re-rendered with errors
    assert message in resp.get_data(as_text=True)
    with world.app_context():
        assert Donation.query.count() == 0


def test_deadline_must_be_after_prepared_time(world, make_client):
    client = make_client("donor@t.com")
    resp = client.post(
        "/donate/new",
        donation_form(prepared_at=_future(-1), pickup_deadline=_future(-2)),
    )
    assert resp.status_code == 200
    with world.app_context():
        assert Donation.query.count() == 0


def test_photo_validation(world, make_client):
    client = make_client("donor@t.com")
    bad = donation_form()
    bad["image"] = (io.BytesIO(b"not an image"), "x.png")
    resp = client.post("/donate/new", bad, content_type="multipart/form-data")
    assert resp.status_code == 200 and b"does not look like" in resp.data

    bad["image"] = (io.BytesIO(b"hello"), "x.exe")
    resp = client.post("/donate/new", bad, content_type="multipart/form-data")
    assert b"PNG, JPG or WebP" in resp.data

    good = donation_form()
    good["image"] = (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 50), "ok.png")
    resp = client.post("/donate/new", good, content_type="multipart/form-data")
    assert resp.status_code == 302
    with world.app_context():
        assert Donation.query.one().image_filename.endswith(".png")


# ---------- edit ----------

def test_owner_can_edit_posted_donation(world, make_client, post_donation):
    donation_id = post_donation()
    client = make_client("donor@t.com")
    assert client.get(f"/donate/{donation_id}/edit").status_code == 200
    resp = client.post(f"/donate/{donation_id}/edit", donation_form(title="Edited", quantity="7"))
    assert resp.status_code == 302
    d = _donation(world, donation_id)
    assert (d.title, d.quantity) == ("Edited", 7)
    with world.app_context():
        assert StatusEvent.query.filter_by(donation_id=donation_id).count() == 2


def test_edit_with_invalid_data_keeps_original(world, make_client, post_donation):
    donation_id = post_donation(title="Original")
    client = make_client("donor@t.com")
    resp = client.post(f"/donate/{donation_id}/edit", donation_form(quantity="-1"))
    assert resp.status_code == 200
    assert _donation(world, donation_id).title == "Original"


def test_other_donor_cannot_edit_or_cancel(world, make_client, post_donation):
    donation_id = post_donation(title="Mine")
    other = make_client("donor2@t.com")
    assert other.get(f"/donate/{donation_id}/edit").status_code == 302
    other.post(f"/donate/{donation_id}/edit", donation_form(title="Hijacked"))
    other.post(f"/donate/{donation_id}/cancel")
    d = _donation(world, donation_id)
    assert (d.title, d.status) == ("Mine", "posted")


def test_cannot_edit_once_claimed(world, make_client, post_donation):
    donation_id = post_donation(title="Before claim")
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    donor = make_client("donor@t.com")
    donor.post(f"/donate/{donation_id}/edit", donation_form(title="After claim"))
    d = _donation(world, donation_id)
    assert (d.title, d.status) == ("Before claim", "claimed")


def test_stale_edit_form_cannot_overwrite_a_claimed_donation(world, make_client, post_donation):
    """Donor opens the edit page, an NGO claims, then the donor submits the old form."""
    donation_id = post_donation(title="Before claim")
    donor = make_client("donor@t.com")
    assert donor.get(f"/donate/{donation_id}/edit").status_code == 200
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    resp = donor.post(f"/donate/{donation_id}/edit", donation_form(title="Stale overwrite"))
    assert resp.status_code == 302
    d = _donation(world, donation_id)
    assert (d.title, d.status) == ("Before claim", "claimed")


def test_claim_landing_mid_edit_blocks_the_edit(world, make_client, post_donation, monkeypatch):
    """A claim commits *between* the handler's first status check and its save.

    Forced deterministically by claiming from inside the validation step. Without the
    locked re-read in edit_donation, the donor's edit would overwrite a claimed donation.
    """
    import app.donations.routes as routes

    donation_id = post_donation(title="Before claim")
    real_validate = routes.validate_donation_form
    claimed = []

    def validate_then_claim(form, now):
        if not claimed:
            claimed.append(make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim"))
        return real_validate(form, now)

    monkeypatch.setattr(routes, "validate_donation_form", validate_then_claim)
    donor = make_client("donor@t.com")
    resp = donor.post(
        f"/donate/{donation_id}/edit", donation_form(title="Mid-flight overwrite"),
        follow_redirects=True,
    )
    assert claimed and claimed[0].status_code == 302
    assert b"can no longer be edited" in resp.data
    d = _donation(world, donation_id)
    assert (d.title, d.status) == ("Before claim", "claimed")


# ---------- cancel ----------

def test_donor_can_cancel_posted_donation(world, make_client, post_donation):
    donation_id = post_donation()
    client = make_client("donor@t.com")
    client.post(f"/donate/{donation_id}/cancel")
    assert _donation(world, donation_id).status == "cancelled"
    # Cancelling again is refused and leaves the state alone.
    resp = client.post(f"/donate/{donation_id}/cancel", follow_redirects=True)
    assert b"already cancelled" in resp.data
    assert _donation(world, donation_id).status == "cancelled"


def test_cancelling_claimed_donation_releases_the_claim(world, make_client, post_donation):
    donation_id = post_donation()
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    make_client("donor@t.com").post(f"/donate/{donation_id}/cancel")
    with world.app_context():
        assert db.session.get(Donation, donation_id).status == "cancelled"
        claim = Claim.query.one()
        assert claim.status == "released" and claim.released_at is not None


# ---------- role permissions ----------

@pytest.mark.parametrize("email", ["ngo@t.com", "vol@t.com", "admin@t.com"])
def test_non_donors_cannot_use_donor_pages(world, make_client, email):
    client = make_client(email)
    for url in ("/donate", "/donate/new"):
        resp = client.get(url)
        assert resp.status_code == 302 and "/donate" not in resp.headers["Location"]
    resp = client.post("/donate/new", donation_form())
    assert resp.status_code == 302
    with world.app_context():
        assert Donation.query.count() == 0


def test_anonymous_is_sent_to_login(make_client):
    client = make_client()
    for url in ("/donate", "/donate/new", "/find-food", "/claims", "/donations/1"):
        resp = client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"], url


def test_post_without_csrf_token_is_rejected(world, make_client):
    client = make_client("donor@t.com")
    assert client.post("/donate/new", donation_form(), csrf=False).status_code == 400
    with world.app_context():
        assert Donation.query.count() == 0


def test_detail_visibility(world, make_client, post_donation):
    donation_id = post_donation()
    url = f"/donations/{donation_id}"
    assert make_client("donor@t.com").get(url).status_code == 200
    assert make_client("admin@t.com").get(url).status_code == 200
    # Unrelated donor / NGO / volunteer are redirected away.
    for email in ("donor2@t.com", "ngo@t.com", "ngo2@t.com", "vol@t.com"):
        assert make_client(email).get(url).status_code == 302, email
    # The NGO that claims it can see it; a different NGO still cannot.
    make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    assert make_client("ngo@t.com").get(url).status_code == 200
    assert make_client("ngo2@t.com").get(url).status_code == 302


def test_inactive_user_cannot_log_in(make_client):
    client = make_client()
    resp = client.login("inactive@t.com")
    assert resp.status_code == 200 and b"deactivated" in resp.data
    assert client.get("/donate").status_code == 302


def test_donor_summary_counts(make_client, post_donation):
    first = post_donation()
    post_donation(title="Second")
    make_client("ngo@t.com").post(f"/find-food/{first}/claim")
    page = make_client("donor@t.com").get("/donate").get_data(as_text=True)
    assert "Claimed" in page and "Posted" in page and "NGO Hub" in page
