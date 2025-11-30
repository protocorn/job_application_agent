"""Quick verification that pattern learning migration succeeded"""
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os
from urllib.parse import quote_plus

load_dotenv()

# Database connection
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)
conn = engine.connect()

print("="*60)
print("PATTERN LEARNING DATABASE VERIFICATION")
print("="*60)

# Get total count and average confidence
result = conn.execute(text('SELECT COUNT(*) as total, AVG(confidence_score) as avg_conf FROM field_label_patterns'))
row = result.first()
print(f"\nDatabase Stats:")
print(f"  Total patterns: {row[0]}")
print(f"  Average confidence: {row[1]:.2f}")

# Get seed vs learned patterns
result = conn.execute(text("SELECT source, COUNT(*) as count FROM field_label_patterns GROUP BY source"))
print(f"\nPatterns by source:")
for r in result:
    print(f"  {r[0]:15}: {r[1]}")

# Show top 10 patterns
result = conn.execute(text("""
    SELECT field_label_raw, profile_field, confidence_score, occurrence_count
    FROM field_label_patterns
    ORDER BY occurrence_count DESC
    LIMIT 10
"""))

print(f"\nTop 10 Patterns:")
for i, r in enumerate(result, 1):
    label = r[0][:35] if len(r[0]) > 35 else r[0]
    print(f"  {i:2}. {label:35} -> {r[1]:20} (conf: {r[2]:.2f}, uses: {r[3]})")

conn.close()

print("\n" + "="*60)
print("[OK] Migration verified successfully!")
print("="*60)
print("\nNext steps:")
print("  1. Run tests: python test_pattern_learning.py")
print("  2. Run E2E test: python test_pattern_learning_e2e.py")
print("  3. Use agent normally - learning is automatic!")
