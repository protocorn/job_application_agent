"""
Migration script to add email verification fields to users table
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database_config import engine, SessionLocal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate():
    """Add email verification columns to users table"""
    db = SessionLocal()
    try:
        logger.info("Starting migration to add email verification fields...")

        # Add email_verified column
        try:
            db.execute(text("""
                ALTER TABLE public.users
                ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE
            """))
            logger.info("Added email_verified column")
        except Exception as e:
            logger.warning(f"email_verified column may already exist: {e}")

        # Add verification_token column
        try:
            db.execute(text("""
                ALTER TABLE public.users
                ADD COLUMN IF NOT EXISTS verification_token VARCHAR UNIQUE
            """))
            logger.info("Added verification_token column")
        except Exception as e:
            logger.warning(f"verification_token column may already exist: {e}")

        # Add verification_token_expires column
        try:
            db.execute(text("""
                ALTER TABLE public.users
                ADD COLUMN IF NOT EXISTS verification_token_expires TIMESTAMP
            """))
            logger.info("Added verification_token_expires column")
        except Exception as e:
            logger.warning(f"verification_token_expires column may already exist: {e}")

        db.commit()
        logger.info("Migration completed successfully!")

    except Exception as e:
        db.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
