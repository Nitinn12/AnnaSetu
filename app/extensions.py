from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

# Shared SQLAlchemy instance, initialised in app/__init__.py via db.init_app(app).
db = SQLAlchemy()

# Flask-Migrate; render_as_batch keeps ALTER TABLE migrations working on SQLite.
migrate = Migrate(render_as_batch=True)
