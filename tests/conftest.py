"""Shared fixtures for the AnnaSetu workflow tests. Each test gets a fresh SQLite file."""
from datetime import timedelta, timezone

import pytest

from app import create_app
from app.extensions import db
from app.models import Donation, Organization, User
from app.timeutil import IST, utcnow
from werkzeug.security import generate_password_hash

from app.zones import ZONE_COORDS

PASSWORD = "Demo@12345"
CSRF = "test-csrf-token"


def to_local(dt):
    """Naive UTC datetime -> the IST string an HTML datetime-local input sends."""
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%Y-%m-%dT%H:%M")


def donation_form(**overrides):
    now = utcnow()
    data = {
        "title": "Veg biryani",
        "category": "cooked_meal",
        "quantity": "20",
        "unit": "kg",
        "servings": "60",
        "prepared_at": to_local(now - timedelta(hours=1)),
        "pickup_deadline": to_local(now + timedelta(hours=5)),
        "storage": "ambient",
        "allergens": "nuts",
        "description": "Freshly cooked",
        "pickup_address": "Gate 2, Synthetic Hostel",
    }
    data.update(overrides)
    return data


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test",
        }
    )
    app.config["UPLOAD_TEST_DIR"] = str(tmp_path)
    with app.app_context():
        db.create_all()
    return app


def _org(name, org_type, zone, **kw):
    lat, lon = ZONE_COORDS[zone]
    org = Organization(
        name=name, org_type=org_type, zone_code=zone, latitude=lat, longitude=lon, **kw
    )
    db.session.add(org)
    db.session.flush()
    return org


def _user(email, role, org=None, name=None, active=True):
    user = User(
        name=name or email.split("@")[0],
        email=email,
        role=role,
        organization_id=org.id if org else None,
        is_active=active,
    )
    user.password_hash = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1")
    db.session.add(user)
    return user


@pytest.fixture()
def world(app):
    """Two donors, two NGOs, a volunteer, an admin, and one org-less user per role."""
    with app.app_context():
        donor_org = _org("Hostel Mess A", "hostel_mess", "Z-01")
        donor2_org = _org("College Canteen", "canteen", "Z-02")
        ngo_org = _org("NGO Hub", "ngo", "Z-04", capacity_kg=300)
        ngo2_org = _org("Community Kitchen", "community_kitchen", "Z-03", capacity_kg=300)
        _user("donor@t.com", "donor", donor_org)
        _user("donor2@t.com", "donor", donor2_org)
        _user("ngo@t.com", "receiver", ngo_org)
        _user("ngo2@t.com", "receiver", ngo2_org)
        _user("ngo_noorg@t.com", "receiver")
        _user("donor_noorg@t.com", "donor")
        _user("vol@t.com", "volunteer", name="Vera Volunteer")
        _user("vol2@t.com", "volunteer", name="Victor Volunteer")
        _user("vol_off@t.com", "volunteer", name="Inactive Volunteer", active=False)
        _user("admin@t.com", "admin")
        _user("inactive@t.com", "donor", donor_org, active=False)
        db.session.commit()
    return app


class Client:
    """Thin wrapper over the Flask test client that handles login and CSRF."""

    def __init__(self, app):
        self.c = app.test_client()
        with self.c.session_transaction() as sess:
            sess["_csrf_token"] = CSRF

    def login(self, email):
        return self.post("/auth/login", {"email": email, "password": PASSWORD})

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, csrf=True, **kw):
        data = dict(data or {})
        # Login calls session.clear(), so make sure the session holds the token.
        with self.c.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        if csrf:
            data["csrf_token"] = CSRF
        return self.c.post(url, data=data, **kw)


@pytest.fixture()
def make_client(world):
    def factory(email=None):
        client = Client(world)
        if email:
            resp = client.login(email)
            assert resp.status_code == 302, f"login failed for {email}"
        return client

    return factory


@pytest.fixture()
def post_donation(make_client):
    """Post a donation as the given donor and return its id."""

    def _post(email="donor@t.com", **overrides):
        client = make_client(email)
        resp = client.post("/donate/new", donation_form(**overrides))
        assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
        with client.c.application.app_context():
            return Donation.query.order_by(Donation.id.desc()).first().id

    return _post
