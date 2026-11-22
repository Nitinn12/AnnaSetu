"""Entry point for the AnnaSetu Flask app.

Run with:  python run.py
"""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug only when FLASK_DEBUG=1. Host and port come from HOST and PORT (defaults: localhost, 5000).
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
