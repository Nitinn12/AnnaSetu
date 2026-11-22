"""Flask CLI commands: database initialisation, demo seeding and admin tasks."""
import click

from app.extensions import db

DEMO_PASSWORD = "Demo@12345"

# Latitude and longitude for each demo zone.
ZONE_COORDS = {
    "Z-01": (12.9716, 77.5946),
    "Z-02": (12.9750, 77.6010),
    "Z-03": (12.9650, 77.5900),
    "Z-04": (12.9800, 77.5850),
}

NGO_CAPACITY_KG = 300

DEMO_ORGANIZATIONS = [
    # (name, org_type, zone_code)
    ("Demo Hostel Mess A", "hostel_mess", "Z-01"),
    ("Demo College Canteen", "canteen", "Z-02"),
    ("Demo Community Kitchen", "community_kitchen", "Z-03"),
    ("Demo NGO Hub", "ngo", "Z-04"),
]

DEMO_USERS = [
    # (email, name, role, organization name or None)
    ("admin@example.com", "Demo Admin", "admin", None),
    ("donor@example.com", "Demo Donor", "donor", "Demo Hostel Mess A"),
    ("ngo@example.com", "Demo NGO Coordinator", "receiver", "Demo NGO Hub"),
    ("volunteer@example.com", "Demo Volunteer", "volunteer", None),
]


def register_cli(app):
    """Attach the CLI commands to the app."""

    @app.cli.command("init-db")
    def init_db():
        """Create all database tables."""
        from app import models  # noqa: F401  (ensure models are registered)

        db.create_all()
        print("Database initialised.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Insert demo organisations and users. Safe to run repeatedly."""
        from app.models import Organization, User

        db.create_all()

        # Organisations: skip any whose name already exists.
        org_by_name = {}
        for name, org_type, zone_code in DEMO_ORGANIZATIONS:
            org = Organization.query.filter_by(name=name).first()
            if org is None:
                latitude, longitude = ZONE_COORDS[zone_code]
                org = Organization(
                    name=name,
                    org_type=org_type,
                    zone_code=zone_code,
                    latitude=latitude,
                    longitude=longitude,
                    capacity_kg=NGO_CAPACITY_KG if org_type == "ngo" else None,
                    is_synthetic=True,
                )
                db.session.add(org)
                db.session.flush()  # assign id before users reference it
            org_by_name[name] = org

        # Users: skip any whose email already exists.
        for email, name, role, org_name in DEMO_USERS:
            if User.query.filter_by(email=email).first() is not None:
                continue
            user = User(
                name=name,
                email=email,
                role=role,
                organization=org_by_name.get(org_name) if org_name else None,
            )
            user.set_password(DEMO_PASSWORD)
            db.session.add(user)

        db.session.commit()

        print("Demo seed complete. Demo credentials:")
        print("-" * 72)
        print(f"{'Email':<26}{'Role':<12}{'Password':<16}Name")
        print("-" * 72)
        for email, name, role, _ in DEMO_USERS:
            print(f"{email:<26}{role:<12}{DEMO_PASSWORD:<16}{name}")
        print("-" * 72)

    @app.cli.command("expire-overdue")
    def expire_overdue_command():
        """Expire posted and claimed donations whose pickup deadline has passed."""
        from app.timeutil import utcnow
        from app.tracking import expire_overdue

        try:
            count = expire_overdue(utcnow())
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        print(f"Expired {count} overdue donation(s).")

    @app.cli.command("create-admin")
    @click.option("--email", required=True, help="Login email for the new admin.")
    @click.option("--name", required=True, help="Display name for the new admin.")
    def create_admin(email, name):
        """Create an admin account. Prompts for the password."""
        from app.models import User

        email = email.strip().lower()
        name = name.strip()
        if not email or not name:
            raise click.ClickException("Email and name must not be blank.")
        if User.query.filter_by(email=email).first() is not None:
            raise click.ClickException(f"A user with email {email} already exists.")

        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        user = User(name=name, email=email, role="admin")
        user.set_password(password)
        try:
            db.session.add(user)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        print(f"Admin account created for {email}.")
