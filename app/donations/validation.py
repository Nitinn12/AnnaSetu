"""Server-side validation for the donation create and edit form."""
import math
from datetime import timedelta

from app.timeutil import parse_local_to_utc

CATEGORY_LABELS = {
    "cooked_meal": "Cooked meal",
    "packaged_food": "Packaged food",
    "fresh_produce": "Fresh produce",
    "bakery": "Bakery",
    "dairy": "Dairy",
    "other": "Other",
}

STORAGE_OPTIONS = {
    "ambient": "Ambient (room temperature)",
    "chilled": "Chilled",
    "frozen": "Frozen",
}

UNIT_OPTIONS = {
    "kg": "Kilograms (kg)",
    "portions": "Portions",
}

MAX_QUANTITY = 5000.0
MAX_SERVINGS = 10000
FUTURE_PREPARED_TOLERANCE = timedelta(minutes=10)
MAX_PICKUP_WINDOW = timedelta(hours=48)


def _text(form, key):
    value = form.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def validate_donation_form(form, now):
    """Validate donation form data.

    form is a mapping such as request.form. now is the current naive UTC time.
    Returns (data, errors). Datetimes in data are naive UTC. errors is a list of
    plain-English messages; it is empty when the form is valid.
    """
    errors = []
    data = {}

    title = _text(form, "title")
    if not title:
        errors.append("Title is required.")
    elif len(title) > 120:
        errors.append("Title must be 120 characters or fewer.")
    data["title"] = title

    category = _text(form, "category")
    if category not in CATEGORY_LABELS:
        errors.append("Choose a valid food category.")
    data["category"] = category

    quantity_raw = _text(form, "quantity")
    quantity = None
    try:
        quantity = float(quantity_raw)
    except ValueError:
        pass
    if quantity is None or not math.isfinite(quantity) or quantity <= 0:
        errors.append("Quantity must be a number greater than 0.")
    elif quantity > MAX_QUANTITY:
        errors.append(f"Quantity must be {MAX_QUANTITY:g} or less.")
        quantity = None
    data["quantity"] = quantity

    unit = _text(form, "unit")
    if unit not in UNIT_OPTIONS:
        errors.append("Choose kg or portions as the unit.")
    data["unit"] = unit

    servings_raw = _text(form, "servings")
    servings = None
    if servings_raw:
        try:
            servings = int(servings_raw)
        except ValueError:
            servings = -1
        if not 1 <= servings <= MAX_SERVINGS:
            errors.append(f"Servings must be a whole number from 1 to {MAX_SERVINGS}.")
            servings = None
    data["servings"] = servings

    prepared_at = None
    try:
        prepared_at = parse_local_to_utc(_text(form, "prepared_at"))
    except ValueError:
        errors.append("Enter a valid preparation date and time.")
    else:
        if prepared_at > now + FUTURE_PREPARED_TOLERANCE:
            errors.append("Preparation time cannot be more than 10 minutes in the future.")
    data["prepared_at"] = prepared_at

    pickup_deadline = None
    try:
        pickup_deadline = parse_local_to_utc(_text(form, "pickup_deadline"))
    except ValueError:
        errors.append("Enter a valid pickup deadline.")
    else:
        if pickup_deadline <= now:
            errors.append("Pickup deadline must be in the future.")
        elif pickup_deadline > now + MAX_PICKUP_WINDOW:
            errors.append("Pickup deadline must be within 48 hours.")
        if prepared_at is not None and pickup_deadline <= prepared_at:
            errors.append("Pickup deadline must be after the preparation time.")
    data["pickup_deadline"] = pickup_deadline

    storage = _text(form, "storage")
    if storage not in STORAGE_OPTIONS:
        errors.append("Choose a valid storage type.")
    data["storage"] = storage

    allergens = _text(form, "allergens")
    if len(allergens) > 255:
        errors.append("Allergens must be 255 characters or fewer.")
    data["allergens"] = allergens or None

    description = _text(form, "description")
    if len(description) > 1000:
        errors.append("Description must be 1000 characters or fewer.")
    data["description"] = description or None

    pickup_address = _text(form, "pickup_address")
    if not pickup_address:
        errors.append("Pickup address is required.")
    elif len(pickup_address) > 255:
        errors.append("Pickup address must be 255 characters or fewer.")
    data["pickup_address"] = pickup_address

    return data, errors
