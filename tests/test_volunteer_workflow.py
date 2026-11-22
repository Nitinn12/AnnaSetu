"""Volunteer workflow: NGO/admin assignment, volunteer pickup and delivery, permissions."""
import threading
import time

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.impact.service import compute_impact
from app.models import Donation, StatusEvent, User, VolunteerAssignment
from app.timeutil import utcnow
from app.tracking import expire_overdue
from tests.conftest import Client


# ---------- helpers ----------

def uid(app, email):
    with app.app_context():
        return User.query.filter_by(email=email).one().id


def status(app, donation_id):
    with app.app_context():
        return db.session.get(Donation, donation_id).status


def assignments(app, donation_id):
    with app.app_context():
        rows = (
            VolunteerAssignment.query.filter_by(donation_id=donation_id)
            .order_by(VolunteerAssignment.id)
            .all()
        )
        return [(a.volunteer.email, a.status) for a in rows]


def transitions(app, donation_id):
    with app.app_context():
        events = (
            StatusEvent.query.filter_by(donation_id=donation_id)
            .order_by(StatusEvent.id)
            .all()
        )
        return [(e.from_status, e.to_status, e.actor_role) for e in events]


@pytest.fixture()
def claimed(world, make_client, post_donation):
    """A donation posted by donor@t.com and claimed by ngo@t.com."""
    donation_id = post_donation(title="Volunteer lunch")
    resp = make_client("ngo@t.com").post(f"/find-food/{donation_id}/claim")
    assert resp.status_code == 302 and status(world, donation_id) == "claimed"
    return donation_id


@pytest.fixture()
def assign(world, make_client):
    def _assign(donation_id, volunteer="vol@t.com", by="ngo@t.com", follow=False):
        return make_client(by).post(
            f"/donations/{donation_id}/assign",
            {"volunteer_id": uid(world, volunteer)},
            follow_redirects=follow,
        )

    return _assign


@pytest.fixture()
def assigned(claimed, assign, world):
    assert assign(claimed).status_code == 302
    assert status(world, claimed) == "assigned"
    return claimed


# ---------- the complete workflow ----------

def test_complete_workflow_donor_to_delivery(world, make_client, post_donation):
    donor = make_client("donor@t.com")
    ngo = make_client("ngo@t.com")
    vol = make_client("vol@t.com")

    # 1. donor posts, 2. NGO claims
    donation_id = post_donation(title="End to end biryani", pickup_address="Gate 7, Synthetic Mess")
    ngo.post(f"/find-food/{donation_id}/claim")

    # volunteer sees nothing yet, and cannot collect
    assert b"No open tasks" in vol.get("/volunteer").data
    vol.post(f"/volunteer/tasks/{donation_id}/collect")
    assert status(world, donation_id) == "claimed"

    # 3. NGO assigns
    resp = ngo.post(f"/donations/{donation_id}/assign", {"volunteer_id": uid(world, "vol@t.com")})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/claims")
    assert status(world, donation_id) == "assigned"

    # volunteer now sees the task with pickup details and a collect button
    page = vol.get("/volunteer").get_data(as_text=True)
    assert "End to end biryani" in page and "Gate 7, Synthetic Mess" in page
    assert "Hostel Mess A" in page and "NGO Hub" in page and "Mark collected" in page
    assert "Mark delivered" not in page

    # 4. pickup
    resp = vol.post(f"/volunteer/tasks/{donation_id}/collect")
    assert resp.status_code == 302 and status(world, donation_id) == "collected"
    page = vol.get("/volunteer").get_data(as_text=True)
    assert "Mark delivered" in page and "Mark collected" not in page

    # 5. delivery
    resp = vol.post(f"/volunteer/tasks/{donation_id}/deliver")
    assert resp.status_code == 302 and status(world, donation_id) == "delivered"

    # the full chain is persisted, in order, with who did each step
    assert transitions(world, donation_id) == [
        (None, "posted", "donor"),
        ("posted", "claimed", "receiver"),
        ("claimed", "assigned", "receiver"),
        ("assigned", "collected", "volunteer"),
        ("collected", "delivered", "volunteer"),
    ]
    with world.app_context():
        events = StatusEvent.query.filter_by(donation_id=donation_id).order_by(StatusEvent.id).all()
        stamps = [e.created_at for e in events]
        assert all(stamps) and stamps == sorted(stamps)
        assert events[2].actor_user_id == uid(world, "ngo@t.com")
        assert events[3].actor_user_id == events[4].actor_user_id == uid(world, "vol@t.com")

        a = VolunteerAssignment.query.filter_by(donation_id=donation_id).one()
        assert a.status == "completed"
        assert a.assigned_by_user_id == uid(world, "ngo@t.com")
        assert a.assigned_at and a.collected_at and a.delivered_at
        assert a.assigned_at <= a.collected_at <= a.delivered_at
        assert a.removed_at is None
        assert compute_impact()["delivered_count"] == 1

    # every party sees the outcome
    assert b"Delivered" in donor.get("/donate").data
    assert b"Delivered" in ngo.get("/claims").data
    done = vol.get("/volunteer").get_data(as_text=True)
    assert "Completed" in done and "End to end biryani" in done and "No open tasks" in done
    detail = donor.get(f"/donations/{donation_id}").get_data(as_text=True)
    assert "Vera Volunteer" in detail and "Moved from Collected to Delivered" in detail


def test_admin_can_assign_and_event_records_admin(world, make_client, claimed):
    admin = make_client("admin@t.com")
    page = admin.get("/admin/assignments").get_data(as_text=True)
    assert "Volunteer lunch" in page and "Assign" in page and "Victor Volunteer" in page
    assert "Inactive Volunteer" not in page

    resp = admin.post(
        f"/donations/{claimed}/assign", {"volunteer_id": uid(world, "vol2@t.com")}
    )
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/assignments")
    assert status(world, claimed) == "assigned"
    assert transitions(world, claimed)[-1] == ("claimed", "assigned", "admin")
    with world.app_context():
        a = VolunteerAssignment.query.one()
        assert a.assigned_by_user_id == uid(world, "admin@t.com")
    # assigned volunteer can proceed
    assert make_client("vol2@t.com").post(f"/volunteer/tasks/{claimed}/collect").status_code == 302
    assert status(world, claimed) == "collected"


# ---------- who may assign ----------

@pytest.mark.parametrize("email", ["donor@t.com", "vol@t.com", "vol2@t.com", "donor2@t.com"])
def test_other_roles_cannot_assign(world, make_client, claimed, email):
    client = make_client(email)
    client.post(f"/donations/{claimed}/assign", {"volunteer_id": uid(world, "vol@t.com")})
    assert status(world, claimed) == "claimed" and assignments(world, claimed) == []


def test_volunteer_cannot_assign_themselves(world, make_client, claimed):
    make_client("vol@t.com").post(f"/donations/{claimed}/assign", {"volunteer_id": uid(world, "vol@t.com")})
    assert assignments(world, claimed) == []


def test_ngo_that_did_not_claim_cannot_assign(world, make_client, claimed, assign):
    resp = assign(claimed, by="ngo2@t.com", follow=True)
    assert b"Only the NGO that claimed" in resp.data
    assert status(world, claimed) == "claimed" and assignments(world, claimed) == []


def test_ngo_without_organisation_cannot_assign(world, make_client, claimed, assign):
    resp = assign(claimed, by="ngo_noorg@t.com", follow=True)
    assert b"Only the NGO that claimed" in resp.data
    assert assignments(world, claimed) == []


def test_anonymous_is_redirected_to_login(world, make_client, claimed):
    client = make_client()
    for url in ("/volunteer", "/admin/assignments"):
        assert "/auth/login" in client.get(url).headers["Location"]
    resp = client.post(f"/donations/{claimed}/assign", {"volunteer_id": 1})
    assert "/auth/login" in resp.headers["Location"] and assignments(world, claimed) == []


def test_assign_requires_csrf_and_post(world, make_client, claimed):
    ngo = make_client("ngo@t.com")
    resp = ngo.post(
        f"/donations/{claimed}/assign", {"volunteer_id": uid(world, "vol@t.com")}, csrf=False
    )
    assert resp.status_code == 400 and assignments(world, claimed) == []
    assert ngo.get(f"/donations/{claimed}/assign").status_code == 405
    assert ngo.get(f"/donations/{claimed}/unassign").status_code == 405


# ---------- assignment validation ----------

def test_cannot_assign_unclaimed_missing_or_cancelled_donation(world, make_client, post_donation, assign):
    posted = post_donation(title="Not claimed yet")
    resp = assign(posted, follow=True)
    assert b"only be assigned to claimed" in resp.data and status(world, posted) == "posted"

    resp = assign(9999, follow=True)
    assert b"was not found" in resp.data

    cancelled = post_donation(title="To cancel")
    make_client("donor@t.com").post(f"/donate/{cancelled}/cancel")
    assert b"only be assigned to claimed" in assign(cancelled, follow=True).data


@pytest.mark.parametrize(
    "volunteer_id, message",
    [("", "valid volunteer"), ("abc", "valid volunteer"), ("-1", "valid volunteer"), ("99999", "valid volunteer")],
)
def test_invalid_volunteer_id_is_rejected(world, make_client, claimed, volunteer_id, message):
    resp = make_client("ngo@t.com").post(
        f"/donations/{claimed}/assign", {"volunteer_id": volunteer_id}, follow_redirects=True
    )
    assert message.encode() in resp.data
    assert status(world, claimed) == "claimed" and assignments(world, claimed) == []


def test_missing_volunteer_field_is_rejected(world, make_client, claimed):
    resp = make_client("ngo@t.com").post(f"/donations/{claimed}/assign", {}, follow_redirects=True)
    assert b"valid volunteer" in resp.data and assignments(world, claimed) == []


@pytest.mark.parametrize("email", ["donor@t.com", "ngo2@t.com", "admin@t.com"])
def test_only_volunteer_accounts_can_be_assigned(world, claimed, assign, email):
    resp = assign(claimed, volunteer=email, follow=True)
    assert b"valid volunteer" in resp.data and assignments(world, claimed) == []


def test_inactive_volunteer_cannot_be_assigned(world, claimed, assign):
    resp = assign(claimed, volunteer="vol_off@t.com", follow=True)
    assert b"not active" in resp.data and assignments(world, claimed) == []


# ---------- duplicate assignment prevention ----------

def test_second_assignment_is_rejected(world, assigned, assign):
    resp = assign(assigned, volunteer="vol2@t.com", follow=True)
    assert b"already assigned" in resp.data
    assert assignments(world, assigned) == [("vol@t.com", "active")]
    assert [t[1] for t in transitions(world, assigned)].count("assigned") == 1
    # same volunteer again is refused too
    assert b"already assigned" in assign(assigned, follow=True).data
    assert assignments(world, assigned) == [("vol@t.com", "active")]


def test_database_rejects_two_active_assignments(world, assigned):
    with world.app_context():
        db.session.add(
            VolunteerAssignment(donation_id=assigned, volunteer_user_id=uid(world, "vol2@t.com"), status="active")
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        # history rows (not active) are fine
        db.session.add(
            VolunteerAssignment(donation_id=assigned, volunteer_user_id=uid(world, "vol2@t.com"), status="removed")
        )
        db.session.commit()


def test_concurrent_assignments_produce_one_winner(world, claimed):
    attempts = [("ngo@t.com", "vol@t.com"), ("admin@t.com", "vol2@t.com")] * 3
    clients = []
    for who, _vol in attempts:
        client = Client(world)
        assert client.login(who).status_code == 302
        clients.append(client)
    barrier = threading.Barrier(len(clients))
    errors = []

    def go(client, vol):
        try:
            barrier.wait(timeout=10)
            client.post(f"/donations/{claimed}/assign", {"volunteer_id": uid(world, vol)})
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(c, v)) for c, (_w, v) in zip(clients, attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    rows = assignments(world, claimed)
    assert [s for _e, s in rows].count("active") == 1, rows
    assert status(world, claimed) == "assigned"
    assert [t[1] for t in transitions(world, claimed)].count("assigned") == 1


# ---------- volunteer pickup and delivery: permissions and order ----------

def test_other_volunteer_cannot_update_someone_elses_task(world, assigned, make_client):
    other = make_client("vol2@t.com")
    for step in ("collect", "deliver"):
        resp = other.post(f"/volunteer/tasks/{assigned}/{step}", follow_redirects=True)
        assert b"not assigned to you" in resp.data
    assert status(world, assigned) == "assigned"
    assert b"Volunteer lunch" not in other.get("/volunteer").data


@pytest.mark.parametrize("email", ["donor@t.com", "ngo@t.com", "admin@t.com"])
def test_non_volunteers_cannot_collect_or_deliver(world, assigned, make_client, email):
    client = make_client(email)
    assert client.get("/volunteer").status_code == 302
    client.post(f"/volunteer/tasks/{assigned}/collect")
    client.post(f"/volunteer/tasks/{assigned}/deliver")
    assert status(world, assigned) == "assigned"


def test_cannot_collect_or_deliver_unassigned_donation(world, claimed, make_client):
    vol = make_client("vol@t.com")
    assert b"not assigned to you" in vol.post(f"/volunteer/tasks/{claimed}/collect", follow_redirects=True).data
    assert b"not assigned to you" in vol.post(f"/volunteer/tasks/{claimed}/deliver", follow_redirects=True).data
    assert b"was not found" in vol.post("/volunteer/tasks/9999/collect", follow_redirects=True).data
    assert status(world, claimed) == "claimed"


def test_steps_must_happen_in_order_and_only_once(world, assigned, make_client):
    vol = make_client("vol@t.com")
    # cannot deliver before collecting
    resp = vol.post(f"/volunteer/tasks/{assigned}/deliver", follow_redirects=True)
    assert b"Cannot move from assigned to delivered" in resp.data
    assert status(world, assigned) == "assigned"

    vol.post(f"/volunteer/tasks/{assigned}/collect")
    # cannot collect twice
    resp = vol.post(f"/volunteer/tasks/{assigned}/collect", follow_redirects=True)
    assert b"Cannot move from collected to collected" in resp.data

    vol.post(f"/volunteer/tasks/{assigned}/deliver")
    # nothing further is possible once delivered
    for step in ("collect", "deliver"):
        resp = vol.post(f"/volunteer/tasks/{assigned}/{step}", follow_redirects=True)
        assert b"not assigned to you" in resp.data
    assert status(world, assigned) == "delivered"
    assert [t[1] for t in transitions(world, assigned)].count("collected") == 1
    assert [t[1] for t in transitions(world, assigned)].count("delivered") == 1


def test_timestamps_are_recorded_per_step(world, assigned, make_client):
    vol = make_client("vol@t.com")
    with world.app_context():
        a = VolunteerAssignment.query.one()
        assert a.assigned_at and a.collected_at is None and a.delivered_at is None
    before = utcnow()
    vol.post(f"/volunteer/tasks/{assigned}/collect")
    with world.app_context():
        a = VolunteerAssignment.query.one()
        assert a.collected_at >= before and a.delivered_at is None and a.status == "active"
    time.sleep(0.01)
    vol.post(f"/volunteer/tasks/{assigned}/deliver")
    with world.app_context():
        a = VolunteerAssignment.query.one()
        assert a.delivered_at >= a.collected_at >= a.assigned_at and a.status == "completed"


def test_volunteer_step_requires_csrf_and_post(world, assigned, make_client):
    vol = make_client("vol@t.com")
    assert vol.post(f"/volunteer/tasks/{assigned}/collect", csrf=False).status_code == 400
    assert vol.get(f"/volunteer/tasks/{assigned}/collect").status_code == 405
    assert status(world, assigned) == "assigned"


# ---------- remove / reassign ----------

def test_ngo_can_remove_and_reassign_before_pickup(world, assigned, make_client, assign):
    ngo = make_client("ngo@t.com")
    resp = ngo.post(f"/donations/{assigned}/unassign")
    assert resp.status_code == 302
    assert status(world, assigned) == "claimed"
    assert assignments(world, assigned) == [("vol@t.com", "removed")]
    with world.app_context():
        a = VolunteerAssignment.query.one()
        assert a.removed_at is not None
    assert transitions(world, assigned)[-1] == ("assigned", "claimed", "receiver")

    # removed volunteer loses access; the task is gone from their list
    vol = make_client("vol@t.com")
    assert b"not assigned to you" in vol.post(f"/volunteer/tasks/{assigned}/collect", follow_redirects=True).data
    assert b"Volunteer lunch" not in vol.get("/volunteer").data

    # a different volunteer can now be assigned and finish the job
    assert assign(assigned, volunteer="vol2@t.com").status_code == 302
    assert assignments(world, assigned) == [("vol@t.com", "removed"), ("vol2@t.com", "active")]
    vol2 = make_client("vol2@t.com")
    vol2.post(f"/volunteer/tasks/{assigned}/collect")
    vol2.post(f"/volunteer/tasks/{assigned}/deliver")
    assert status(world, assigned) == "delivered"


def test_admin_can_remove_volunteer(world, assigned, make_client):
    make_client("admin@t.com").post(f"/donations/{assigned}/unassign")
    assert status(world, assigned) == "claimed"
    assert transitions(world, assigned)[-1] == ("assigned", "claimed", "admin")


def test_cannot_remove_volunteer_after_pickup(world, assigned, make_client):
    make_client("vol@t.com").post(f"/volunteer/tasks/{assigned}/collect")
    resp = make_client("ngo@t.com").post(f"/donations/{assigned}/unassign", follow_redirects=True)
    assert b"before the food is collected" in resp.data
    assert status(world, assigned) == "collected"
    assert assignments(world, assigned) == [("vol@t.com", "active")]


def test_cannot_remove_when_nobody_assigned(world, claimed, make_client):
    resp = make_client("ngo@t.com").post(f"/donations/{claimed}/unassign", follow_redirects=True)
    assert b"before the food is collected" in resp.data


@pytest.mark.parametrize("email", ["ngo2@t.com", "donor@t.com", "vol@t.com", "vol2@t.com"])
def test_unauthorised_users_cannot_remove_volunteer(world, assigned, make_client, email):
    make_client(email).post(f"/donations/{assigned}/unassign")
    assert status(world, assigned) == "assigned"
    assert assignments(world, assigned) == [("vol@t.com", "active")]


# ---------- existing donation and claim behaviour is preserved ----------

def test_claim_cannot_be_released_while_a_volunteer_is_assigned(world, assigned, make_client):
    with world.app_context():
        from app.models import Claim
        claim_id = Claim.query.one().id
    resp = make_client("ngo@t.com").post(f"/claims/{claim_id}/release", follow_redirects=True)
    assert b"can no longer be released" in resp.data
    assert status(world, assigned) == "assigned"
    # after removing the volunteer, release works again
    make_client("ngo@t.com").post(f"/donations/{assigned}/unassign")
    make_client("ngo@t.com").post(f"/claims/{claim_id}/release")
    assert status(world, assigned) == "posted"


def test_donor_cannot_cancel_once_a_volunteer_is_assigned(world, assigned, make_client):
    resp = make_client("donor@t.com").post(f"/donate/{assigned}/cancel", follow_redirects=True)
    assert b"Your role cannot make this change" in resp.data
    assert status(world, assigned) == "assigned"
    assert assignments(world, assigned) == [("vol@t.com", "active")]


def test_donor_cannot_edit_assigned_donation(world, assigned, make_client):
    assert make_client("donor@t.com").get(f"/donate/{assigned}/edit").status_code == 302


def test_assigned_donations_are_not_auto_expired(world, assigned):
    with world.app_context():
        d = db.session.get(Donation, assigned)
        d.pickup_deadline = utcnow().replace(year=2020)
        db.session.commit()
        assert expire_overdue(utcnow()) == 0
        assert db.session.get(Donation, assigned).status == "assigned"


def test_assigned_donation_leaves_the_browse_list(make_client, assigned):
    assert "Volunteer lunch" not in make_client("ngo2@t.com").get("/find-food").get_data(as_text=True)


# ---------- pages ----------

def test_claims_page_offers_assign_then_remove(world, claimed, make_client, assign):
    ngo = make_client("ngo@t.com")
    page = ngo.get("/claims").get_data(as_text=True)
    assert "Choose volunteer" in page and "Vera Volunteer" in page and "Victor Volunteer" in page
    assert "Inactive Volunteer" not in page and "Remove" not in page

    assign(claimed)
    page = ngo.get("/claims").get_data(as_text=True)
    assert "Vera Volunteer" in page and "Remove" in page and "Choose volunteer" not in page
    # claim cannot be released while a volunteer is assigned: the button is gone
    assert "Release" not in page


def test_detail_visibility_for_volunteers(world, assigned, make_client):
    url = f"/donations/{assigned}"
    assert make_client("vol@t.com").get(url).status_code == 200
    assert make_client("vol2@t.com").get(url).status_code == 302
    detail = make_client("ngo@t.com").get(url).get_data(as_text=True)
    assert "Vera Volunteer" in detail and "Volunteer assigned" in detail


def test_pages_render_for_each_role(world, assigned, make_client):
    for email, url in [
        ("vol@t.com", "/volunteer"),
        ("vol2@t.com", "/volunteer"),
        ("ngo@t.com", "/claims"),
        ("admin@t.com", "/admin/assignments"),
    ]:
        assert make_client(email).get(url).status_code == 200, (email, url)
    # dashboard routing is unchanged
    assert make_client("vol@t.com").get("/dashboard").headers["Location"].endswith("/volunteer")
    assert b"Assignments" in make_client("admin@t.com").get("/admin/assignments").data


def test_admin_page_is_admin_only(make_client, claimed):
    for email in ("donor@t.com", "ngo@t.com", "vol@t.com"):
        assert make_client(email).get("/admin/assignments").status_code == 302


def test_admin_page_lists_in_flight_work_only(world, claimed, make_client, post_donation):
    post_donation(title="Still just posted")
    page = make_client("admin@t.com").get("/admin/assignments").get_data(as_text=True)
    assert "Volunteer lunch" in page and "Still just posted" not in page
