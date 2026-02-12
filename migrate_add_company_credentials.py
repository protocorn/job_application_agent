"""
Migration: Add company_credentials table for storing auto-generated passwords
"""
from sqlalchemy import create_engine, text
import os
import sys
from dotenv import load_dotenv
from loguru import logger

# Load environment variables
load_dotenv()

# Database Configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')

if not DB_PASSWORD:
    logger.error("DB_PASSWORD environment variable is required")
    sys.exit(1)

from urllib.parse import quote_plus
encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)


def check_table_exists(table_name: str) -> bool:
    """Check if a table exists in the database"""
    with engine.connect() as connection:
        result = connection.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public'
                AND table_name = :table_name
            );
        """), {"table_name": table_name})
        return result.fetchone()[0]


def migrate():
    """Add company_credentials table"""
    logger.info("🚀 Starting migration: add company_credentials table")
    
    try:
        # Check if table already exists
        if check_table_exists('company_credentials'):
            logger.info("✅ Table 'company_credentials' already exists, skipping creation")
            return
        
        with engine.connect() as connection:
            trans = connection.begin()
            
            try:
                logger.info("📝 Creating company_credentials table...")
                connection.execute(text("""
                    CREATE TABLE company_credentials (
                        id SERIAL PRIMARY KEY,
                        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        company_name VARCHAR NOT NULL,
                        company_domain VARCHAR NOT NULL,
                        email VARCHAR NOT NULL,
                        password_encrypted TEXT NOT NULL,
                        ats_type VARCHAR DEFAULT 'workday',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_used_at TIMESTAMP,
                        CONSTRAINT unique_user_company UNIQUE (user_id, company_domain)
                    );
                """))
                logger.info("✅ Table created successfully")
                
                # Create indexes
                logger.info("📝 Creating indexes...")
                connection.execute(text("""
                    CREATE INDEX idx_company_credentials_user_id ON company_credentials(user_id);
                """))
                connection.execute(text("""
                    CREATE INDEX idx_company_credentials_domain ON company_credentials(company_domain);
                """))
                logger.info("✅ Indexes created successfully")
                
                trans.commit()
                logger.info("✅ Migration completed successfully!")
                
            except Exception as e:
                trans.rollback()
                logger.error(f"❌ Migration failed: {e}")
                raise
                
    except Exception as e:
        logger.error(f"❌ Migration error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    migrate()


