"""
Simple migration to add projects tables
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def migrate():
    print("=" * 60)
    print("DATABASE MIGRATION: Add Projects Tables")
    print("=" * 60)

    try:
        # Connect to database
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT'),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD')
        )
        cursor = conn.cursor()
        print("[OK] Database connection successful")

        # Create projects table
        print("\nCreating projects table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name VARCHAR(255) NOT NULL,
                description TEXT,
                technologies TEXT[],
                github_url VARCHAR(500),
                live_url VARCHAR(500),
                features TEXT[],
                detailed_bullets TEXT[],
                tags TEXT[],
                start_date VARCHAR(50),
                end_date VARCHAR(50),
                team_size INTEGER,
                role VARCHAR(100),
                is_on_resume BOOLEAN DEFAULT FALSE,
                display_order INTEGER DEFAULT 0,
                times_used INTEGER DEFAULT 0,
                avg_relevance_score FLOAT,
                last_used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)")
        print("[OK] Created 'projects' table")

        # Create project_usage_history table
        print("\nCreating project_usage_history table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_usage_history (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                job_description_hash VARCHAR(32),
                job_title VARCHAR(255),
                company_name VARCHAR(255),
                relevance_score INTEGER,
                was_selected BOOLEAN DEFAULT TRUE,
                selection_reason VARCHAR(50),
                replaced_project_id INTEGER REFERENCES projects(id),
                tailoring_session_id VARCHAR(50),
                resume_url VARCHAR(500),
                application_submitted BOOLEAN,
                got_interview BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_project_id ON project_usage_history(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_user_id ON project_usage_history(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_job_hash ON project_usage_history(job_description_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_created_at ON project_usage_history(created_at)")
        print("[OK] Created 'project_usage_history' table")

        # Commit changes
        conn.commit()
        cursor.close()
        conn.close()

        print("\n" + "=" * 60)
        print("MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print("Added tables:")
        print("  - projects: Store user projects")
        print("  - project_usage_history: Track project usage analytics")
        return True

    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = migrate()
    exit(0 if success else 1)
