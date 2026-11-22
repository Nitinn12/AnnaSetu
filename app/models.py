"""SQLAlchemy models for AnnaSetu."""
from sqlalchemy import CheckConstraint, Index, text, true

from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.timeutil import utcnow

DONATION_STATUSES = (
    "posted",
    "claimed",
    "assigned",
    "collected",
    "delivered",
    "cancelled",
    "expired",
)
_STATUS_SQL = ", ".join(f"'{s}'" for s in DONATION_STATUSES)


class Organization(db.Model):
    """A food donor or receiver organisation (canteen, mess, NGO, kitchen)."""

    __tablename__ = "organization"

    # org_type is one of: canteen, hostel_mess, ngo, community_kitchen
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    org_type = db.Column(db.String(30), nullable=False)
    zone_code = db.Column(db.String(10), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    # Maximum kg this organisation can take in one claim; None means no limit.
    capacity_kg = db.Column(db.Float, nullable=True)
    # Comma-separated category keys; None (or blank) means all categories are accepted.
    accepted_categories = db.Column(db.String(255), nullable=True)
    is_synthetic = db.Column(db.Boolean, default=True)

    def accepts_category(self, key):
        """Return True if this organisation accepts the given category key."""
        if not self.accepted_categories or not self.accepted_categories.strip():
            return True
        accepted = {c.strip() for c in self.accepted_categories.split(",")}
        return key in accepted

    def __repr__(self):
        return f"<Organization {self.name}>"


class User(db.Model):
    """Platform user. role is one of: donor, receiver, volunteer, admin."""

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=utcnow)
    # Inactive users cannot log in.
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=true()
    )

    # Optional link to the user's organisation.
    organization = db.relationship("Organization", backref="users")

    def set_password(self, raw):
        """Hash and store a raw password."""
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        """Return True if raw matches the stored hash."""
        return check_password_hash(self.password_hash, raw)

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class StatusEvent(db.Model):
    """One entry in a donation's status history. Written only by app/tracking."""

    __tablename__ = "status_event"

    id = db.Column(db.Integer, primary_key=True)
    donation_id = db.Column(
        db.Integer, db.ForeignKey("donation.id"), nullable=False, index=True
    )
    from_status = db.Column(db.String(20), nullable=True)
    to_status = db.Column(db.String(20), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    actor_role = db.Column(db.String(20), nullable=True)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    donation = db.relationship("Donation", back_populates="events")
    actor = db.relationship("User")

    def __repr__(self):
        return f"<StatusEvent {self.from_status}->{self.to_status}>"


class Claim(db.Model):
    """An NGO's claim on a donation. At most one row per donation is active."""

    __tablename__ = "claim"
    __table_args__ = (
        Index(
            "uq_claim_one_active",
            "donation_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    donation_id = db.Column(
        db.Integer, db.ForeignKey("donation.id"), nullable=False, index=True
    )
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="active")  # active, released
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    released_at = db.Column(db.DateTime, nullable=True)

    donation = db.relationship("Donation", back_populates="claims")
    organization = db.relationship("Organization")
    user = db.relationship("User")

    def __repr__(self):
        return f"<Claim donation={self.donation_id} {self.status}>"


class VolunteerAssignment(db.Model):
    """A volunteer's job to collect and deliver a donation."""

    __tablename__ = "volunteer_assignment"
    __table_args__ = (
        Index(
            "uq_assignment_one_active",
            "donation_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    donation_id = db.Column(
        db.Integer, db.ForeignKey("donation.id"), nullable=False, index=True
    )
    volunteer_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    # active, completed, removed (unassigned before pickup)
    status = db.Column(db.String(12), nullable=False, default="active")
    # Who made the assignment: the NGO/admin user, or the volunteer for self-accepted tasks.
    assigned_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    assigned_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    collected_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    removed_at = db.Column(db.DateTime, nullable=True)

    donation = db.relationship("Donation", back_populates="assignments")
    volunteer = db.relationship("User", foreign_keys=[volunteer_user_id])
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_user_id])

    def __repr__(self):
        return f"<VolunteerAssignment donation={self.donation_id} {self.status}>"


class Donation(db.Model):
    """A food donation posted by a donor organisation."""

    __tablename__ = "donation"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_donation_quantity_positive"),
        CheckConstraint(
            f"status IN ({_STATUS_SQL})", name="ck_donation_status_valid"
        ),
        Index("ix_donation_status_pickup_deadline", "status", "pickup_deadline"),
    )

    id = db.Column(db.Integer, primary_key=True)
    donor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id"), nullable=False
    )
    title = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(10), nullable=False)  # kg or portions
    servings = db.Column(db.Integer, nullable=True)
    prepared_at = db.Column(db.DateTime, nullable=False)
    pickup_deadline = db.Column(db.DateTime, nullable=False)
    pickup_zone = db.Column(db.String(10), nullable=False)
    pickup_address = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    storage = db.Column(db.String(20), nullable=False)  # ambient, chilled, frozen
    allergens = db.Column(db.String(255), nullable=True)
    image_filename = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="posted")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    donor = db.relationship("User")
    organization = db.relationship("Organization")
    events = db.relationship(
        "StatusEvent",
        back_populates="donation",
        order_by=[StatusEvent.created_at, StatusEvent.id],
    )
    claims = db.relationship(
        "Claim", back_populates="donation", order_by=[Claim.id]
    )
    assignments = db.relationship(
        "VolunteerAssignment",
        back_populates="donation",
        order_by=[VolunteerAssignment.id],
    )

    @property
    def active_claim(self):
        """The active Claim on this donation, or None."""
        return next((c for c in self.claims if c.status == "active"), None)

    @property
    def active_assignment(self):
        """The active VolunteerAssignment on this donation, or None."""
        return next((a for a in self.assignments if a.status == "active"), None)

    def __repr__(self):
        return f"<Donation {self.id} {self.title!r} ({self.status})>"
