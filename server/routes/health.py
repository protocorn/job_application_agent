import os
import time
import logging

from flask import Blueprint, jsonify


def create_health_blueprint(*, sentry_enabled: bool) -> Blueprint:
    """Create health-related routes."""
    health_bp = Blueprint("health", __name__)

    @health_bp.route("/health", methods=["GET"])
    @health_bp.route("/api/health", methods=["GET"])
    def health():
        """Health check endpoint with optional resource details."""
        health_data = {
            "status": "ok",
            "timestamp": time.time(),
            "sentry_enabled": sentry_enabled,
            # Bump this whenever old CLI versions must be blocked.
            # Read from env so it can be changed without a redeploy.
            "min_cli_version": os.getenv("MIN_CLI_VERSION", "0.2.46"),
        }

        try:
            from health_monitor import get_system_status

            system_status = get_system_status()
            if system_status.get("initialized"):
                health_data["resource_management"] = {
                    "enabled": True,
                    "resource_manager": system_status.get("resource_manager", {}),
                    "connection_pool": system_status.get("connection_pool", {}),
                    "health_status": system_status.get("health", {}).get(
                        "current_status", "unknown"
                    ),
                }
        except Exception as exc:
            logging.debug(f"Resource management status not available: {exc}")

        return jsonify(health_data), 200

    @health_bp.route("/ready", methods=["GET"])
    def readiness_check():
        """
        Readiness check endpoint - verifies all dependencies are working.
        Returns 200 if ready, 503 if not ready.
        """
        checks = {}
        all_ready = True

        try:
            from database_config import engine
            from sqlalchemy import text

            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = {"status": "ready", "message": "Connected"}
        except Exception as exc:
            checks["database"] = {"status": "not_ready", "error": str(exc)}
            all_ready = False

        try:
            from rate_limiter import redis_client

            redis_client.ping()
            checks["redis"] = {"status": "ready", "message": "Connected"}
        except Exception as exc:
            checks["redis"] = {"status": "not_ready", "error": str(exc)}
            all_ready = False

        response = {
            "status": "ready" if all_ready else "not_ready",
            "timestamp": time.time(),
            "checks": checks,
        }

        return jsonify(response), (200 if all_ready else 503)

    return health_bp

