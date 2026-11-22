"""Donor workflow: post, edit, cancel and view donations, and manage the organisation profile."""
import math
import os
import re
import uuid
from datetime import timezone

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from app.auth.decorators import login_required, role_required
from app.donations import bp
from app.donations.validation import (
    CATEGORY_LABELS,
    STORAGE_OPTIONS,
    UNIT_OPTIONS,
    validate_donation_form,
)
from app.extensions import db
from app.models import Donation, Organization, StatusEvent, User
from app.services.claims import ClaimError, donor_cancel
from app.timeutil import IST, format_local, utcnow
from app.zones import ORG_TYPES, ZONE_COORDS, ZONES

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024
UPLOAD_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|jpeg|webp)$")
FORM_FIELDS = (
    "title",
    "category",
    "quantity",
    "unit",
    "servings",
    "prepared_at",
    "pickup_deadline",
    "storage",
    "allergens",
    "description",
    "pickup_address",
)
STATUS_LABELS = {
    "posted": "Posted",
    "claimed": "Claimed",
    "assigned": "Volunteer assigned",
    "collected": "Collected",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "expired": "Expired",
}
RECEIVER_ORG_TYPES = ("ngo", "community_kitchen")
DONOR_ORG_TYPES = ("canteen", "hostel_mess")


def _current_user():
    """Return the logged-in User, or None."""
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _uploads_dir():
    return os.path.join(current_app.instance_path, "uploads")


def _to_local_input(dt):
    """Format a naive UTC datetime as an IST datetime-local value for prefilling a form."""
    if dt is None:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%Y-%m-%dT%H:%M")


def _looks_like_image(data, ext):
    """Check the file signature matches the extension."""
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _read_image(upload):
    """Validate an uploaded photo. Returns (data, ext, error).

    data and ext are None when no photo was sent; error is None when valid.
    """
    if upload is None or not upload.filename:
        return None, None, None
    safe_ext = secure_filename(os.path.splitext(upload.filename)[1]).lower()
    ext = f".{safe_ext}" if safe_ext else ""
    if ext not in IMAGE_EXTENSIONS:
        return None, None, "Photo must be a PNG, JPG or WebP file."
    data = upload.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return None, None, "Photo must be 2 MB or smaller."
    if not _looks_like_image(data, ext):
        return None, None, "The photo does not look like a valid PNG, JPG or WebP image."
    return data, ext, None


def _save_image(data, ext):
    """Write image bytes to the uploads folder under a random name. Returns the name."""
    folder = _uploads_dir()
    os.makedirs(folder, exist_ok=True)
    name = uuid.uuid4().hex + ext
    with open(os.path.join(folder, name), "wb") as handle:
        handle.write(data)
    return name


def _remove_image(filename):
    """Delete an uploaded photo. Missing files are ignored."""
    if not filename:
        return
    try:
        os.remove(os.path.join(_uploads_dir(), filename))
    except OSError:
        pass


def _submitted_values(form):
    return {key: form.get(key, "") for key in FORM_FIELDS}


def _donation_values(donation):
    """Prefill values for the edit form from a saved donation."""
    return {
        "title": donation.title,
        "category": donation.category,
        "quantity": "" if donation.quantity is None else f"{donation.quantity:g}",
        "unit": donation.unit,
        "servings": "" if donation.servings is None else str(donation.servings),
        "prepared_at": _to_local_input(donation.prepared_at),
        "pickup_deadline": _to_local_input(donation.pickup_deadline),
        "storage": donation.storage,
        "allergens": donation.allergens or "",
        "description": donation.description or "",
        "pickup_address": donation.pickup_address,
    }


def _render_donation_form(form_values, is_edit, donation):
    return render_template(
        "donations/form.html",
        form_values=form_values,
        is_edit=is_edit,
        donation=donation,
        categories=CATEGORY_LABELS,
        storage_options=STORAGE_OPTIONS,
        unit_options=UNIT_OPTIONS,
    )


def _timeline(donation):
    """Build display rows for the status history, oldest first."""
    rows = []
    for event in donation.events:
        if event.from_status is None:
            text = "Posted"
        elif event.from_status == event.to_status:
            text = "Details edited"
        else:
            text = (
                f"Moved from {STATUS_LABELS.get(event.from_status, event.from_status)} "
                f"to {STATUS_LABELS.get(event.to_status, event.to_status)}"
            )
        rows.append(
            {
                "status": event.to_status,
                "status_label": STATUS_LABELS.get(event.to_status, event.to_status),
                "text": text,
                "note": event.note,
                "role": event.actor_role,
                "time": format_local(event.created_at),
            }
        )
    return rows


def _can_view(donation, user, claim):
    """Donor owner, claiming organisation, assigned volunteer, or admin."""
    if user.role == "admin" or donation.donor_user_id == user.id:
        return True
    if claim is not None and user.organization_id is not None:
        if claim.organization_id == user.organization_id:
            return True
    return any(a.volunteer_user_id == user.id for a in donation.assignments)


@bp.route("/donate", methods=["GET"])
@role_required("donor")
def donate():
    user = _current_user()
    donations = (
        Donation.query.filter_by(donor_user_id=user.id)
        .order_by(Donation.created_at.desc(), Donation.id.desc())
        .all()
    )

    counts = {}
    for donation in donations:
        counts[donation.status] = counts.get(donation.status, 0) + 1
    summary = {
        "open": sum(counts.get(s, 0) for s in ("posted", "claimed", "assigned")),
        "in_delivery": sum(counts.get(s, 0) for s in ("assigned", "collected")),
        "delivered": counts.get("delivered", 0),
    }

    rows = []
    for donation in donations:
        claim = donation.active_claim
        rows.append(
            {
                "donation": donation,
                "claimed_by": claim.organization.name if claim is not None else None,
                "pickup_by": format_local(donation.pickup_deadline),
                "status_label": STATUS_LABELS.get(donation.status, donation.status),
            }
        )

    return render_template("donations/list.html", rows=rows, summary=summary)


@bp.route("/donate/new", methods=["GET", "POST"])
@role_required("donor")
def new_donation():
    user = _current_user()
    if user.organization_id is None:
        flash("Add your organisation before posting food.", "warning")
        return redirect(url_for("donations.organization_profile"))

    if request.method == "GET":
        return _render_donation_form({}, False, None)

    now = utcnow()
    data, errors = validate_donation_form(request.form, now)
    image_data, image_ext, image_error = _read_image(request.files.get("image"))
    if image_error:
        errors.append(image_error)
    if errors:
        for message in errors:
            flash(message, "danger")
        return _render_donation_form(_submitted_values(request.form), False, None)

    image_filename = _save_image(image_data, image_ext) if image_data else None
    donation = Donation(
        donor_user_id=user.id,
        organization_id=user.organization_id,
        pickup_zone=user.organization.zone_code,
        status="posted",
        created_at=now,
        updated_at=now,
        image_filename=image_filename,
        **data,
    )
    db.session.add(donation)
    try:
        db.session.flush()
        db.session.add(
            StatusEvent(
                donation_id=donation.id,
                from_status=None,
                to_status="posted",
                actor_user_id=user.id,
                actor_role="donor",
                note="Donation posted",
                created_at=now,
            )
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        _remove_image(image_filename)
        flash("Could not save the donation. Please try again.", "danger")
        return _render_donation_form(_submitted_values(request.form), False, None)

    flash("Donation posted. Receivers nearby can now see it.", "success")
    return redirect(url_for("donations.detail", donation_id=donation.id))


@bp.route("/donate/<int:donation_id>/edit", methods=["GET", "POST"])
@role_required("donor")
def edit_donation(donation_id):
    user = _current_user()
    donation = db.session.get(Donation, donation_id)
    if donation is None or donation.donor_user_id != user.id:
        flash("You do not have access to that donation.", "danger")
        return redirect(url_for("donations.donate"))
    if donation.status != "posted":
        flash("Only posted donations can be edited.", "danger")
        return redirect(url_for("donations.donate"))

    if request.method == "GET":
        return _render_donation_form(_donation_values(donation), True, donation)

    now = utcnow()
    data, errors = validate_donation_form(request.form, now)
    image_data, image_ext, image_error = _read_image(request.files.get("image"))
    if image_error:
        errors.append(image_error)
    if errors:
        for message in errors:
            flash(message, "danger")
        return _render_donation_form(_submitted_values(request.form), True, donation)

    # A receiver may have claimed the food while the form was open. Re-read the
    # row under a lock so an edit can never overwrite a claimed donation.
    donation = db.session.get(
        Donation, donation_id, with_for_update=True, populate_existing=True
    )
    if donation is None or donation.status != "posted":
        db.session.rollback()
        flash("This donation was claimed or changed, so it can no longer be edited.", "danger")
        return redirect(url_for("donations.donate"))

    old_image = donation.image_filename
    new_image = _save_image(image_data, image_ext) if image_data else None
    for key, value in data.items():
        setattr(donation, key, value)
    donation.updated_at = now
    if new_image:
        donation.image_filename = new_image
    db.session.add(
        StatusEvent(
            donation_id=donation.id,
            from_status="posted",
            to_status="posted",
            actor_user_id=user.id,
            actor_role="donor",
            note="Details edited",
            created_at=now,
        )
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        _remove_image(new_image)
        flash("Could not save changes. Please try again.", "danger")
        return _render_donation_form(_submitted_values(request.form), True, donation)

    if new_image and old_image:
        _remove_image(old_image)
    flash("Donation updated.", "success")
    return redirect(url_for("donations.detail", donation_id=donation.id))


@bp.route("/donate/<int:donation_id>/cancel", methods=["POST"])
@role_required("donor")
def cancel_donation(donation_id):
    user = _current_user()
    donation = db.session.get(Donation, donation_id)
    if donation is None:
        flash("This donation was not found.", "danger")
        return redirect(url_for("donations.donate"))
    try:
        donor_cancel(donation, user)
    except ClaimError as exc:
        flash(str(exc), "danger")
    else:
        flash("Donation cancelled.", "info")
    return redirect(url_for("donations.donate"))


@bp.route("/donations/<int:donation_id>", methods=["GET"])
@login_required
def detail(donation_id):
    user = _current_user()
    if user is None:
        return redirect(url_for("auth.login"))
    donation = db.session.get(Donation, donation_id)
    if donation is None:
        flash("You do not have access to that donation.", "danger")
        return redirect(url_for("main.dashboard"))

    claim = donation.active_claim
    if not _can_view(donation, user, claim):
        flash("You do not have access to that donation.", "danger")
        return redirect(url_for("main.dashboard"))

    assignment = donation.active_assignment
    is_owner = donation.donor_user_id == user.id
    return render_template(
        "donations/detail.html",
        donation=donation,
        timeline=_timeline(donation),
        claim=claim,
        claimed_by=claim.organization.name if claim is not None else None,
        assignment=assignment,
        assigned_to=assignment.volunteer.name if assignment is not None else None,
        viewer_role=user.role,
        is_owner=is_owner,
        can_edit=is_owner and donation.status == "posted",
        can_cancel=is_owner and donation.status in ("posted", "claimed"),
        status_label=STATUS_LABELS.get(donation.status, donation.status),
        prepared_text=format_local(donation.prepared_at),
        deadline_text=format_local(donation.pickup_deadline),
        category_labels=CATEGORY_LABELS,
        storage_labels=STORAGE_OPTIONS,
        unit_labels=UNIT_OPTIONS,
    )


@bp.route("/uploads/<path:filename>", methods=["GET"])
@login_required
def uploaded_image(filename):
    if not UPLOAD_NAME_RE.fullmatch(filename):
        abort(404)
    return send_from_directory(_uploads_dir(), filename)


def _org_form_values(form):
    return {
        "name": form.get("name", ""),
        "org_type": form.get("org_type", ""),
        "zone_code": form.get("zone_code", ""),
        "capacity_kg": form.get("capacity_kg", ""),
        "categories": form.getlist("categories"),
    }


def _validate_org(form, is_receiver, creating):
    """Validate organisation form data. Returns (data, errors)."""
    errors = []
    data = {}

    name = (form.get("name") or "").strip()
    if not name:
        errors.append("Organisation name is required.")
    elif len(name) > 120:
        errors.append("Organisation name must be 120 characters or fewer.")
    data["name"] = name

    zone_code = (form.get("zone_code") or "").strip()
    if zone_code not in ZONE_COORDS:
        errors.append("Choose a valid zone.")
    data["zone_code"] = zone_code

    if creating:
        allowed = RECEIVER_ORG_TYPES if is_receiver else DONOR_ORG_TYPES
        org_type = (form.get("org_type") or "").strip()
        if org_type not in allowed:
            errors.append("Choose a valid organisation type.")
        data["org_type"] = org_type

    capacity = None
    if is_receiver:
        raw = (form.get("capacity_kg") or "").strip()
        if raw:
            try:
                capacity = float(raw)
            except ValueError:
                capacity = None
            if capacity is None or not math.isfinite(capacity) or not 1 <= capacity <= 100000:
                errors.append("Capacity must be a number from 1 to 100000 kg, or left blank.")
                capacity = None
    data["capacity_kg"] = capacity

    categories_value = None
    if is_receiver:
        chosen = []
        for key in form.getlist("categories"):
            if key not in CATEGORY_LABELS:
                errors.append("Choose valid food categories.")
                break
            if key not in chosen:
                chosen.append(key)
        categories_value = ",".join(chosen) if chosen else None
    data["accepted_categories"] = categories_value

    return data, errors


@bp.route("/profile/organization", methods=["GET", "POST"])
@login_required
def organization_profile():
    user = _current_user()
    if user is None:
        return redirect(url_for("auth.login"))
    if user.role not in ("donor", "receiver"):
        flash("Only donor and receiver accounts have an organisation profile.", "warning")
        return redirect(url_for("main.dashboard"))

    is_receiver = user.role == "receiver"
    org = user.organization
    creating = org is None
    if is_receiver:
        org_types = [(v, label) for v, label in ORG_TYPES if v in RECEIVER_ORG_TYPES]
    else:
        org_types = [(v, label) for v, label in ORG_TYPES if v in DONOR_ORG_TYPES]

    if request.method == "POST":
        data, errors = _validate_org(request.form, is_receiver, creating)
        if errors:
            for message in errors:
                flash(message, "danger")
            return _render_org_form(
                _org_form_values(request.form), is_receiver, creating, org, org_types
            )

        lat, lon = ZONE_COORDS[data["zone_code"]]
        categories_value = data.pop("accepted_categories")
        if creating:
            org = Organization(
                name=data["name"],
                org_type=data["org_type"],
                zone_code=data["zone_code"],
                latitude=lat,
                longitude=lon,
                capacity_kg=data["capacity_kg"],
                accepted_categories=categories_value,
                is_synthetic=True,
            )
            db.session.add(org)
            user.organization = org
        else:
            org.name = data["name"]
            org.zone_code = data["zone_code"]
            org.latitude = lat
            org.longitude = lon
            if is_receiver:
                org.capacity_kg = data["capacity_kg"]
                org.accepted_categories = categories_value
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not save the organisation. Please try again.", "danger")
            return _render_org_form(
                _org_form_values(request.form), is_receiver, creating, org, org_types
            )

        flash("Organisation profile saved.", "success")
        return redirect(url_for("main.dashboard"))

    if org is None:
        values = {}
    else:
        values = {
            "name": org.name,
            "org_type": org.org_type,
            "zone_code": org.zone_code,
            "capacity_kg": "" if org.capacity_kg is None else f"{org.capacity_kg:g}",
            "categories": (org.accepted_categories or "").split(",")
            if org.accepted_categories
            else [],
        }
    return _render_org_form(values, is_receiver, creating, org, org_types)


def _render_org_form(values, is_receiver, creating, org, org_types):
    return render_template(
        "donations/organization.html",
        fv=values,
        is_receiver=is_receiver,
        is_new=creating,
        org=org,
        org_types=org_types,
        zones=ZONES,
        categories=CATEGORY_LABELS,
    )
