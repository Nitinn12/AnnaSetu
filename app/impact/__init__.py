from flask import Blueprint

bp = Blueprint("impact", __name__)

from app.impact import routes  # noqa: E402,F401  (registers routes on bp)
