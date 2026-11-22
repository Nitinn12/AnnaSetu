from flask import Blueprint

bp = Blueprint("receivers", __name__)

from app.receivers import routes  # noqa: E402,F401  (registers routes on bp)
