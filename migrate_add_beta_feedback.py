"""
Database Migration: Add Beta Feedback Table
Adds table to store beta tester feedback and track credit rewards
"""

import os
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from database_config import Base, User, engine, SessionLocal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate():
    """Add beta_feedback table and has_submitted_beta_feedback flag to users"""
    try:
        # Add column to User table if it doesn't exist
        from sqlalchemy import text
        db = SessionLocal()

        try:
            # Check if column exists
            result = db.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='users' AND column_name='has_submitted_beta_feedback'
            """))

            if not result.fetchone():
                logger.info("Adding has_submitted_beta_feedback column to users table...")
                db.execute(text("""
                    ALTER TABLE users
                    ADD COLUMN has_submitted_beta_feedback BOOLEAN DEFAULT FALSE
                """))
                db.commit()
                logger.info("✓ Added has_submitted_beta_feedback column")
            else:
                logger.info("has_submitted_beta_feedback column already exists")

        finally:
            db.close()

        # Create beta_feedback table
        logger.info("Creating beta_feedback table...")

        from sqlalchemy import Table, MetaData
        metadata = MetaData()

        # Check if table exists
        metadata.reflect(bind=engine)

        if 'beta_feedback' not in metadata.tables:
            db = SessionLocal()
            try:
                db.execute(text("""
                    CREATE TABLE beta_feedback (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,

                        -- Overall Experience
                        overall_rating INTEGER NOT NULL CHECK (overall_rating >= 1 AND overall_rating <= 5),
                        ease_of_use INTEGER NOT NULL CHECK (ease_of_use >= 1 AND ease_of_use <= 5),

                        -- Feature Feedback
                        most_useful_feature TEXT,
                        least_useful_feature TEXT,
                        missing_features TEXT,

                        -- Resume Tailoring Specific
                        tailoring_quality INTEGER NOT NULL CHECK (tailoring_quality >= 1 AND tailoring_quality <= 5),
                        tailoring_comments TEXT,

                        -- Future Features Interest
                        interested_cover_letter BOOLEAN DEFAULT FALSE,
                        interested_job_tracking BOOLEAN DEFAULT FALSE,
                        interested_interview_prep BOOLEAN DEFAULT FALSE,
                        interested_salary_insights BOOLEAN DEFAULT FALSE,
                        other_feature_requests TEXT,

                        -- Open Feedback
                        what_worked_well TEXT,
                        what_needs_improvement TEXT,
                        additional_comments TEXT,

                        -- Likelihood to Recommend
                        recommend_score INTEGER NOT NULL CHECK (recommend_score >= 0 AND recommend_score <= 10),

                        -- Credit Reward
                        credits_awarded INTEGER DEFAULT 10,

                        -- Metadata
                        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        user_email VARCHAR(255),

                        UNIQUE(user_id)
                    )
                """))
                db.commit()
                logger.info("✓ Created beta_feedback table")

                # Create index for faster queries
                db.execute(text("""
                    CREATE INDEX idx_beta_feedback_user_id ON beta_feedback(user_id)
                """))
                db.execute(text("""
                    CREATE INDEX idx_beta_feedback_submitted_at ON beta_feedback(submitted_at DESC)
                """))
                db.commit()
                logger.info("✓ Created indexes on beta_feedback table")

            finally:
                db.close()
        else:
            logger.info("beta_feedback table already exists")

        logger.info("\n" + "="*60)
        logger.info("✓ Migration completed successfully!")
        logger.info("="*60)

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

if __name__ == "__main__":
    print("Starting Beta Feedback Migration...")
    print("="*60)
    migrate()
