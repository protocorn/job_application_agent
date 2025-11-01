"""
Database migration script to add cover_letter_template column to user_profiles table
"""
from database_config import engine
from sqlalchemy import text

def add_cover_letter_column():
    """Add cover_letter_template column to user_profiles table"""
    try:
        with engine.connect() as connection:
            # Check if column already exists
            check_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='user_profiles' AND column_name='cover_letter_template'
            """)
            result = connection.execute(check_query)

            if result.fetchone():
                print("Column 'cover_letter_template' already exists")
                return

            # Add the column
            alter_query = text("""
                ALTER TABLE user_profiles
                ADD COLUMN cover_letter_template TEXT
            """)
            connection.execute(alter_query)
            connection.commit()
            print("Successfully added 'cover_letter_template' column to user_profiles table")

    except Exception as e:
        print(f"Error adding column: {e}")
        raise

if __name__ == "__main__":
    print("=" * 60)
    print("Database Migration: Add Cover Letter Template Column")
    print("=" * 60)
    add_cover_letter_column()
    print("=" * 60)
    print("Migration completed successfully!")
