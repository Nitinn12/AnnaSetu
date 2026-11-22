"""Application factory for AnnaSetu."""
import os

from dotenv import load_dotenv
from flask import Flask, abort, render_template, request, session

from app.csrf import generate_csrf_token, validate_csrf
from app.dburl import normalize_database_url
from app.extensions import db, migrate
from app.timeutil import format_local


def create_app(test_config=None):
    """Build and configure the Flask application."""
    # Load variables from a .env file (if present) before reading config.
    load_dotenv()

    app = Flask(__name__, instance_relative_config=False)
    os.makedirs(app.instance_path, exist_ok=True)

    # Core configuration. Environment variables override defaults.
    default_db = "sqlite:///" + os.path.join(
        app.instance_path, "annasetu.db"
    ).replace("\\", "/")
    secret = os.environ.get("SECRET_KEY", "")
    debug_mode = os.environ.get("FLASK_DEBUG") == "1"
    testing = bool((test_config or {}).get("TESTING"))
    if not secret and not (debug_mode or testing):
        raise RuntimeError(
            "SECRET_KEY is not set. Set SECRET_KEY in the environment, "
            "or set FLASK_DEBUG=1 for local development."
        )

    app.config.update(
        SECRET_KEY=secret or "dev-only-change-me",
        SQLALCHEMY_DATABASE_URI=(
            normalize_database_url(os.environ.get("DATABASE_URL")) or default_db
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY", ""),  # unused in phase 1
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    migrate.init_app(app, db)

    # Blueprints are imported here to avoid circular imports at module load.
    from app.main import bp as main_bp
    from app.auth import bp as auth_bp
    from app.donations import bp as donations_bp
    from app.receivers import bp as receivers_bp
    from app.volunteers import bp as volunteers_bp
    from app.impact import bp as impact_bp

    for blueprint in (
        main_bp,
        auth_bp,
        donations_bp,
        receivers_bp,
        volunteers_bp,
        impact_bp,
    ):
        app.register_blueprint(blueprint)

    from app.cli import register_cli

    register_cli(app)

    from app.models import User

    @app.context_processor
    def inject_globals():
        """Make current_user and csrf_token available in all templates."""
        user_id = session.get("user_id")
        current_user = db.session.get(User, user_id) if user_id else None
        return {
            "current_user": current_user,
            "csrf_token": generate_csrf_token,
        }

    # Templates can also call csrf_token() directly.
    app.jinja_env.globals["csrf_token"] = generate_csrf_token
    # {{ some_utc_datetime|local_time }} renders in IST.
    app.jinja_env.filters["local_time"] = format_local

    @app.before_request
    def check_csrf():
        """Reject any POST whose form token does not match the session."""
        if request.method == "POST":
            token = request.form.get("csrf_token")
            if not validate_csrf(token):
                abort(
                    400,
                    description=(
                        "Invalid or missing security token. "
                        "Please reload the page and try again."
                    ),
                )

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_template("errors/500.html"), 500

    return app
