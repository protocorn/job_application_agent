"""
Application entry helpers.

This is an incremental first step to support modular server composition while
preserving existing `api_server.py` behavior.
"""


def create_app():
    """Return the configured Flask app."""
    from api_server import app

    return app

