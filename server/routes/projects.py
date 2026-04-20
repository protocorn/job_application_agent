import logging

from flask import Blueprint, jsonify, request

from auth import require_auth


def create_projects_blueprint() -> Blueprint:
    """Create project management routes."""
    projects_bp = Blueprint("projects", __name__)

    @projects_bp.route("/api/projects", methods=["GET"])
    @require_auth
    def get_projects():
        """Get all projects for the authenticated user."""
        try:
            from database_config import SessionLocal
            from migrate_add_projects import Project

            user_id = request.current_user["id"]
            db = SessionLocal()

            try:
                projects = db.query(Project).filter(Project.user_id == user_id).all()

                projects_data = []
                for project in projects:
                    projects_data.append(
                        {
                            "id": project.id,
                            "name": project.name,
                            "description": project.description,
                            "technologies": project.technologies or [],
                            "github_url": project.github_url,
                            "live_url": project.live_url,
                            "features": project.features or [],
                            "detailed_bullets": project.detailed_bullets or [],
                            "tags": project.tags or [],
                            "start_date": project.start_date,
                            "end_date": project.end_date,
                            "team_size": project.team_size,
                            "role": project.role,
                            "is_on_resume": project.is_on_resume,
                            "display_order": project.display_order,
                            "times_used": project.times_used,
                            "avg_relevance_score": project.avg_relevance_score,
                            "last_used_at": (
                                project.last_used_at.isoformat()
                                if project.last_used_at
                                else None
                            ),
                            "created_at": (
                                project.created_at.isoformat()
                                if project.created_at
                                else None
                            ),
                        }
                    )

                return jsonify({"success": True, "projects": projects_data}), 200

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error getting projects: {exc}")
            return jsonify({"error": str(exc)}), 500

    @projects_bp.route("/api/projects", methods=["POST"])
    @require_auth
    def create_project():
        """Create a new project."""
        try:
            from database_config import SessionLocal
            from migrate_add_projects import Project

            user_id = request.current_user["id"]
            data = request.json

            db = SessionLocal()

            try:
                project = Project(
                    user_id=user_id,
                    name=data.get("name"),
                    description=data.get("description"),
                    technologies=data.get("technologies", []),
                    github_url=data.get("github_url"),
                    live_url=data.get("live_url"),
                    features=data.get("features", []),
                    detailed_bullets=data.get("detailed_bullets", []),
                    tags=data.get("tags", []),
                    start_date=data.get("start_date"),
                    end_date=data.get("end_date"),
                    team_size=data.get("team_size"),
                    role=data.get("role"),
                    is_on_resume=data.get("is_on_resume", False),
                    display_order=data.get("display_order", 0),
                )

                db.add(project)
                db.commit()
                db.refresh(project)

                return (
                    jsonify(
                        {
                            "success": True,
                            "project": {
                                "id": project.id,
                                "name": project.name,
                                "description": project.description,
                                "technologies": project.technologies,
                            },
                        }
                    ),
                    201,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error creating project: {exc}")
            return jsonify({"error": str(exc)}), 500

    @projects_bp.route("/api/projects/<int:project_id>", methods=["PUT"])
    @require_auth
    def update_project(project_id):
        """Update an existing project."""
        try:
            from database_config import SessionLocal
            from migrate_add_projects import Project

            user_id = request.current_user["id"]
            data = request.json

            db = SessionLocal()

            try:
                project = (
                    db.query(Project)
                    .filter(Project.id == project_id, Project.user_id == user_id)
                    .first()
                )

                if not project:
                    return jsonify({"error": "Project not found"}), 404

                for key, value in data.items():
                    if hasattr(project, key) and key not in ["id", "user_id", "created_at"]:
                        setattr(project, key, value)

                db.commit()
                db.refresh(project)

                return jsonify({"success": True, "message": "Project updated successfully"}), 200

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error updating project: {exc}")
            return jsonify({"error": str(exc)}), 500

    @projects_bp.route("/api/projects/<int:project_id>", methods=["DELETE"])
    @require_auth
    def delete_project(project_id):
        """Delete a project."""
        try:
            from database_config import SessionLocal
            from migrate_add_projects import Project

            user_id = request.current_user["id"]
            db = SessionLocal()

            try:
                project = (
                    db.query(Project)
                    .filter(Project.id == project_id, Project.user_id == user_id)
                    .first()
                )

                if not project:
                    return jsonify({"error": "Project not found"}), 404

                db.delete(project)
                db.commit()

                return jsonify({"success": True, "message": "Project deleted successfully"}), 200

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error deleting project: {exc}")
            return jsonify({"error": str(exc)}), 500

    @projects_bp.route("/api/projects/save-discovered", methods=["POST"])
    @require_auth
    def save_discovered_projects():
        """Save discovered projects to database."""
        try:
            from database_config import SessionLocal
            from migrate_add_projects import Project

            user_id = request.current_user["id"]
            data = request.json

            projects_to_save = data.get("projects", [])

            if not projects_to_save:
                return jsonify({"error": "No projects to save"}), 400

            db = SessionLocal()
            saved_projects = []

            try:
                for proj_data in projects_to_save:
                    project = Project(
                        user_id=user_id,
                        name=proj_data.get("name"),
                        description=proj_data.get("description"),
                        technologies=proj_data.get("technologies", []),
                        github_url=proj_data.get("github_url"),
                        live_url=proj_data.get("live_url"),
                        features=proj_data.get("features", []),
                        detailed_bullets=proj_data.get("detailed_bullets", []),
                        tags=proj_data.get("tags", []),
                        is_on_resume=False,
                        display_order=0,
                    )

                    db.add(project)
                    saved_projects.append(project.name)

                db.commit()

                return (
                    jsonify(
                        {
                            "success": True,
                            "message": f"Saved {len(saved_projects)} projects",
                            "projects": saved_projects,
                        }
                    ),
                    201,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error saving discovered projects: {exc}")
            return jsonify({"error": str(exc)}), 500

    return projects_bp

