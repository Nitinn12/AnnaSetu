from flask import Blueprint

bp = Blueprint("donations", __name__)

from app.donations import routes  # noqa: E402,F401  (registers routes on bp)
