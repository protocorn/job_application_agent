"""
Database Migration: Add Mimikree Credentials to User Profiles
Adds encrypted storage for per-user Mimikree credentials
"""

import os
import sys
import logging
from datetime import datetime
from sqlalchemy import create_engine, text, Column, String, DateTime, Boolean
from sqlalchemy.orm import sessionmaker
from urllib.parse import quote_plus

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_database_url():
    """Get database URL from environment variables"""
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    
    if not DB_PASSWORD:
        raise ValueError("DB_PASSWORD environment variable is required")
    
    encoded_password = quote_plus(DB_PASSWORD)
    return f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def check_column_exists(engine, table_name, column_name):
    """Check if a column exists in a table"""
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = '{table_name}' AND column_name = '{column_name}'
        """))
        return result.fetchone() is not None

def migrate_add_mimikree_credentials():
    """Add Mimikree credentials columns to users table"""
    
    logger.info("="*60)
    logger.info("DATABASE MIGRATION: Add Mimikree Credentials")
    logger.info("="*60)
    
    try:
        # Create database connection
        database_url = get_database_url()
        engine = create_engine(database_url)
        
        logger.info("✓ Database connection successful")
        
        # Check if columns already exist
        columns_to_add = [
            ('mimikree_email', 'VARCHAR'),
            ('mimikree_password_encrypted', 'TEXT'),
            ('mimikree_connected_at', 'TIMESTAMP'),
            ('mimikree_is_connected', 'BOOLEAN DEFAULT FALSE')
        ]
        
        with engine.connect() as conn:
            for column_name, column_type in columns_to_add:
                if check_column_exists(engine, 'users', column_name):
                    logger.info(f"Column '{column_name}' already exists, skipping")
                    continue
                
                logger.info(f"Adding column '{column_name}' to users table...")
                
                alter_query = text(f"""
                    ALTER TABLE users 
                    ADD COLUMN {column_name} {column_type}
                """)
                
                conn.execute(alter_query)
                logger.info(f"✓ Added column '{column_name}'")
            
            # Commit all changes
            conn.commit()
        
        logger.info("="*60)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY")
        logger.info("="*60)
        logger.info("Added Mimikree credentials columns to users table:")
        logger.info("  - mimikree_email: Store user's Mimikree email")
        logger.info("  - mimikree_password_encrypted: Encrypted password storage")
        logger.info("  - mimikree_connected_at: Connection timestamp")
        logger.info("  - mimikree_is_connected: Connection status flag")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        return False

def rollback_mimikree_credentials():
    """Rollback the Mimikree credentials migration"""
    
    logger.info("="*60)
    logger.info("ROLLBACK: Remove Mimikree Credentials")
    logger.info("="*60)
    
    try:
        database_url = get_database_url()
        engine = create_engine(database_url)
        
        columns_to_remove = [
            'mimikree_email',
            'mimikree_password_encrypted', 
            'mimikree_connected_at',
            'mimikree_is_connected'
        ]
        
        with engine.connect() as conn:
            for column_name in columns_to_remove:
                if not check_column_exists(engine, 'users', column_name):
                    logger.info(f"Column '{column_name}' doesn't exist, skipping")
                    continue
                
                logger.info(f"Removing column '{column_name}' from users table...")
                
                drop_query = text(f"""
                    ALTER TABLE users 
                    DROP COLUMN IF EXISTS {column_name}
                """)
                
                conn.execute(drop_query)
                logger.info(f"✓ Removed column '{column_name}'")
            
            conn.commit()
        
        logger.info("✓ Rollback completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Rollback failed: {e}")
        return False

def main():
    """Main migration function"""
    if len(sys.argv) > 1 and sys.argv[1] == '--rollback':
        success = rollback_mimikree_credentials()
    else:
        success = migrate_add_mimikree_credentials()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
