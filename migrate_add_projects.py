"""
Database Migration: Add Projects and Project Usage History Tables

This migration adds:
1. projects table - stores all user projects separately from UserProfile JSON
2. project_usage_history table - tracks which projects are used for which jobs
"""

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, ForeignKey, JSON, Float, ARRAY
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

# Database Configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)
Base = declarative_base()


class Project(Base):
    """
    Project model - stores user projects with detailed information.

    Replaces the JSON array in UserProfile.projects with structured table.
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Basic project info
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Technologies and details
    technologies = Column(ARRAY(String), default=[])  # Array of technology names
    github_url = Column(String(500))
    live_url = Column(String(500))
    features = Column(ARRAY(Text), default=[])  # Array of feature descriptions
    detailed_bullets = Column(ARRAY(Text), default=[])  # Pre-written bullet points
    tags = Column(ARRAY(String), default=[])  # Auto-generated tags for search

    # Project metadata
    start_date = Column(String(50))  # Can be "Jan 2023", "2023", or null
    end_date = Column(String(50))  # Can be "Present", "Mar 2023", or null
    team_size = Column(Integer)  # Number of team members (1 = solo project)
    role = Column(String(100))  # e.g., "Full Stack Developer", "Team Lead"

    # Resume management
    is_on_resume = Column(Boolean, default=False)  # Is this currently on the user's resume?
    display_order = Column(Integer, default=0)  # Order on resume (0 = not on resume)

    # Analytics
    times_used = Column(Integer, default=0)  # How many times used across all tailorings
    avg_relevance_score = Column(Float)  # Average relevance score when used
    last_used_at = Column(DateTime)  # Last time this project was selected for a resume

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProjectUsageHistory(Base):
    """
    Tracks which projects were used for which job applications.

    Enables analytics like:
    - Which projects are most valuable?
    - What types of jobs does each project match best with?
    - Success rates per project
    """
    __tablename__ = "project_usage_history"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Job context
    job_description_hash = Column(String(32), index=True)  # MD5 hash of job description
    job_title = Column(String(255))
    company_name = Column(String(255))

    # Selection details
    relevance_score = Column(Integer)  # 0-100 score when selected
    was_selected = Column(Boolean, default=True)  # True if used, False if considered but rejected
    selection_reason = Column(String(50))  # 'initial', 'swap_in', 'manual'
    replaced_project_id = Column(Integer, ForeignKey("projects.id"))  # If swapped, which project was replaced?

    # Tailoring session
    tailoring_session_id = Column(String(50))  # UUID for the tailoring session
    resume_url = Column(String(500))  # URL of the tailored resume

    # Outcome tracking (optional - can be updated later)
    application_submitted = Column(Boolean)  # Was application actually submitted?
    got_interview = Column(Boolean)  # Did user get an interview?

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def migrate():
    """Run the migration"""
    print("=" * 60)
    print("DATABASE MIGRATION: Add Projects Tables")
    print("=" * 60)

    try:
        # Test connection
        connection = engine.connect()
        print("✓ Database connection successful")
        connection.close()

        # Create new tables
        print("\nCreating new tables...")
        Base.metadata.create_all(bind=engine)
        print("✓ Created 'projects' table")
        print("✓ Created 'project_usage_history' table")

        print("\n" + "=" * 60)
        print("MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print("\nNew tables:")
        print("  - projects: Stores structured project data")
        print("  - project_usage_history: Tracks project usage analytics")
        print("\nNext steps:")
        print("  1. Existing UserProfile.projects (JSON) will remain as backup")
        print("  2. New projects should be created in 'projects' table")
        print("  3. Consider running data migration to copy existing projects")

        return True

    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        print("\nPlease check:")
        print("  - Database credentials in .env file")
        print("  - PostgreSQL server is running")
        print("  - Database exists")
        return False


def rollback():
    """Rollback the migration (drop the new tables)"""
    print("=" * 60)
    print("ROLLING BACK MIGRATION")
    print("=" * 60)

    try:
        connection = engine.connect()

        print("\nDropping tables...")
        Base.metadata.drop_all(bind=engine, tables=[
            Project.__table__,
            ProjectUsageHistory.__table__
        ])

        connection.close()
        print("✓ Tables dropped successfully")
        return True

    except Exception as e:
        print(f"✗ Rollback failed: {e}")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        rollback()
    else:
        migrate()
