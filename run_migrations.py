"""
Run all database migrations with proper encoding handling
"""
import sys
import os
import subprocess

# Force UTF-8 encoding for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

migrations = [
    'migrate_add_projects.py',
    'migrate_add_mimikree_credentials.py',
    'migrate_add_google_oauth.py',
    'migrate_add_pattern_learning.py'
]

print("=" * 60)
print("Running Database Migrations")
print("=" * 60)

for migration in migrations:
    print(f"\nRunning {migration}...")
    try:
        result = subprocess.run(
            [sys.executable, migration],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        if result.returncode != 0:
            print(f"Warning: {migration} exited with code {result.returncode}")
        else:
            print(f"✓ {migration} completed successfully")

    except Exception as e:
        print(f"Error running {migration}: {e}")

print("\n" + "=" * 60)
print("Migration process complete!")
print("=" * 60)
