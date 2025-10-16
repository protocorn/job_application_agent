"""
Migration script to add Google OAuth columns to users table
Run this file: python migrate_add_google_oauth.py
"""

from database_config import engine
from sqlalchemy import text
import logging

def migrate():
    """Add Google OAuth columns to users table"""

    sql_commands = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_refresh_token TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_access_token TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_token_expiry TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_account_email VARCHAR"
    ]

    try:
        with engine.connect() as connection:
            for sql in sql_commands:
                logging.info(f"Executing: {sql}")
                connection.execute(text(sql))
                connection.commit()

        print("Migration completed successfully!")
        print("Added Google OAuth columns to users table:")
        print("  - google_refresh_token (TEXT)")
        print("  - google_access_token (TEXT)")
        print("  - google_token_expiry (TIMESTAMP)")
        print("  - google_account_email (VARCHAR)")

    except Exception as e:
        print(f"Migration failed: {e}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Starting migration to add Google OAuth columns...")
    migrate()
