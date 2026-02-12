"""
Database Migration: Add LaTeX Resume Storage Fields

Adds user profile columns for storing LaTeX ZIP resume sources and metadata.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from project .env (same pattern as app runtime)
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)


def get_database_url():
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "job_agent_db")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD")
    if not db_password:
        raise ValueError("DB_PASSWORD environment variable is required")
    encoded_password = quote_plus(db_password)
    return f"postgresql://{db_user}:{encoded_password}@{db_host}:{db_port}/{db_name}"


def check_column_exists(engine, table_name, column_name):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None


def migrate_add_latex_resume_storage():
    logger.info("=" * 60)
    logger.info("DATABASE MIGRATION: Add LaTeX Resume Storage")
    logger.info("=" * 60)
    try:
        engine = create_engine(get_database_url())
        columns_to_add = [
            ("resume_source_type", "VARCHAR DEFAULT 'google_doc'"),
            ("latex_zip_base64", "TEXT"),
            ("latex_main_tex_path", "VARCHAR"),
            ("latex_file_manifest", "JSON"),
            ("latex_uploaded_at", "TIMESTAMP"),
        ]
        with engine.connect() as conn:
            for column_name, column_type in columns_to_add:
                if check_column_exists(engine, "user_profiles", column_name):
                    logger.info("Column '%s' already exists, skipping", column_name)
                    continue
                logger.info("Adding column '%s' to user_profiles...", column_name)
                conn.execute(
                    text(
                        f"""
                        ALTER TABLE user_profiles
                        ADD COLUMN {column_name} {column_type}
                        """
                    )
                )
                logger.info("Added column '%s'", column_name)
            conn.commit()
        logger.info("Migration completed successfully")
        return True
    except Exception as e:
        logger.error("Migration failed: %s", e)
        return False


def rollback_latex_resume_storage():
    logger.info("=" * 60)
    logger.info("ROLLBACK: Remove LaTeX Resume Storage")
    logger.info("=" * 60)
    try:
        engine = create_engine(get_database_url())
        columns_to_drop = [
            "resume_source_type",
            "latex_zip_base64",
            "latex_main_tex_path",
            "latex_file_manifest",
            "latex_uploaded_at",
        ]
        with engine.connect() as conn:
            for column_name in columns_to_drop:
                if not check_column_exists(engine, "user_profiles", column_name):
                    logger.info("Column '%s' does not exist, skipping", column_name)
                    continue
                conn.execute(
                    text(
                        f"""
                        ALTER TABLE user_profiles
                        DROP COLUMN IF EXISTS {column_name}
                        """
                    )
                )
                logger.info("Dropped column '%s'", column_name)
            conn.commit()
        logger.info("Rollback completed successfully")
        return True
    except Exception as e:
        logger.error("Rollback failed: %s", e)
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        ok = rollback_latex_resume_storage()
    else:
        ok = migrate_add_latex_resume_storage()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
