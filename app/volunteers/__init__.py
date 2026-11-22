from flask import Blueprint

bp = Blueprint("volunteers", __name__)

from app.volunteers import routes  # noqa: E402,F401  (registers routes on bp)
