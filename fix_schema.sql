-- Drop the existing user_profiles table completely
DROP TABLE IF EXISTS user_profiles CASCADE;

-- Create the new user_profiles table with full schema
CREATE TABLE user_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),

    -- Basic Information
    resume_url VARCHAR,
    date_of_birth VARCHAR,
    gender VARCHAR,
    nationality VARCHAR,
    preferred_language VARCHAR,
    phone VARCHAR,
    address TEXT,
    city VARCHAR,
    state VARCHAR,
    zip_code VARCHAR,
    country VARCHAR,
    country_code VARCHAR,
    state_code VARCHAR,

    -- Social Links
    linkedin VARCHAR,
    github VARCHAR,
    other_links JSON,

    -- Education, Work Experience, Projects - stored as JSON
    education JSON,
    work_experience JSON,
    projects JSON,

    -- Skills - stored as JSON object with categories
    skills JSON,

    -- Professional Summary
    summary TEXT,

    -- Additional Info
    disabilities JSON,
    veteran_status VARCHAR,
    visa_status VARCHAR,
    visa_sponsorship VARCHAR,
    preferred_location JSON,
    willing_to_relocate VARCHAR,

    -- Timestamps
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- Create index on user_id
CREATE INDEX ix_user_profiles_id ON user_profiles (id);

-- Show the new table structure
\d user_profiles;