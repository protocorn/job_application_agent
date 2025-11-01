from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from datetime import timedelta
import os
from dotenv import load_dotenv

load_dotenv()

# Database Configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Saahil@2412')

# Create database URL (URL encode the password to handle special characters)
from urllib.parse import quote_plus
encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Google OAuth fields
    google_refresh_token = Column(Text)  # Encrypted refresh token
    google_access_token = Column(Text)  # Encrypted access token
    google_token_expiry = Column(DateTime)
    google_account_email = Column(String)

    # Relationships
    job_applications = relationship("JobApplication", back_populates="user")

class JobApplication(Base):
    __tablename__ = "job_applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_id = Column(String, nullable=False)  # External job ID from job boards
    company_name = Column(String, nullable=False)
    job_title = Column(String, nullable=False)
    job_url = Column(String)
    status = Column(String, default="queued")  # queued, in_progress, completed, failed
    applied_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    error_message = Column(Text)
    resume_path = Column(String)
    cover_letter_path = Column(String)

    # Relationships
    user = relationship("User", back_populates="job_applications")

class JobListing(Base):
    __tablename__ = "job_listings"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True, nullable=False)  # ID from job board
    title = Column(String, nullable=False)
    company = Column(String, nullable=False)
    location = Column(String)
    salary = Column(String)
    description = Column(Text)
    requirements = Column(Text)
    job_url = Column(String)
    source = Column(String)  # indeed, linkedin, etc.
    posted_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Basic Information
    resume_url = Column(String)
    cover_letter_template = Column(Text)  # Cover letter template (text or Google Doc URL)
    date_of_birth = Column(String)
    gender = Column(String)
    nationality = Column(String)
    preferred_language = Column(String)
    phone = Column(String)
    address = Column(Text)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    country = Column(String)
    country_code = Column(String)
    state_code = Column(String)

    # Social Links
    linkedin = Column(String)
    github = Column(String)
    other_links = Column(JSON)  # Array of links

    # Education, Work Experience, Projects - stored as JSON
    education = Column(JSON)  # Array of education objects
    work_experience = Column(JSON)  # Array of work experience objects
    projects = Column(JSON)  # Array of project objects

    # Skills - stored as JSON object with categories
    skills = Column(JSON)  # Object with technical, programming_languages, etc.

    # Professional Summary
    summary = Column(Text)

    # Additional Info
    disabilities = Column(JSON)  # Array
    veteran_status = Column(String)
    visa_status = Column(String)
    visa_sponsorship = Column(String)
    preferred_location = Column(JSON)  # Array of preferred locations
    willing_to_relocate = Column(String)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = relationship("User", back_populates="profile")

# Update User model to include profile relationship
User.profile = relationship("UserProfile", back_populates="user", uselist=False)

# Action history for session replay per user/job with TTL
class ActionHistory(Base):
    __tablename__ = "action_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(String, nullable=False, index=True)
    action_log = Column(JSON)  # store structured actions
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)  # set to created_at + 24h
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime)

    user = relationship("User")

# Database utility functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_tables():
    """Create all tables in the database"""
    Base.metadata.create_all(bind=engine)
    print("All tables created successfully!")

def test_connection():
    """Test database connection"""
    try:
        connection = engine.connect()
        connection.close()
        print("Database connection successful!")
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False

if __name__ == "__main__":
    # Test connection and create tables
    if test_connection():
        create_tables()
    else:
        print("Please check your database configuration.")