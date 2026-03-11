#!/usr/bin/env python3
"""
Build-time script: encrypts all Agents/*.py files → launchway/encrypted_agents/

Run this ONCE before building the PyPI package:
    python scripts/encrypt_agents.py

What it does:
  1. Generates (or reuses) a Fernet AES-128 symmetric key
  2. Encrypts every .py file under Agents/ into launchway/encrypted_agents/**/*.enc
  3. Saves the key to .agent_build_key (gitignored — dev machine only)

After running:
  - Copy the printed AGENT_RUNTIME_KEY value to Railway → Settings → Variables
  - Build the package normally: python -m build

The .enc files are unreadable without the key. Users who pip-install launchway
receive only the encrypted blobs; the key is fetched at runtime from the server
after authentication.
"""

import shutil
import sys
import hashlib
from pathlib import Path

# ── Dependency check ────────────────────────────────────────────────────────

try:
    from cryptography.fernet import Fernet
except ImportError:
    print("ERROR: cryptography not installed. Run: pip install cryptography")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
AGENTS_DIR  = REPO_ROOT / "Agents"
OUT_DIR     = REPO_ROOT / "launchway" / "encrypted_agents"
KEY_FILE    = REPO_ROOT / ".agent_build_key"   # gitignored

# ── Guard ────────────────────────────────────────────────────────────────────

if not AGENTS_DIR.exists():
    print(f"ERROR: Agents/ directory not found at {AGENTS_DIR}")
    sys.exit(1)

# ── Key management ───────────────────────────────────────────────────────────

if KEY_FILE.exists():
    key = KEY_FILE.read_bytes()
    print(f"[OK] Reusing existing key from {KEY_FILE.name}")
else:
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    print(f"[OK] Generated new encryption key -- saved to {KEY_FILE.name}")

print()
print("=" * 65)
print("  ACTION REQUIRED -- add this to Railway -> Settings -> Variables:")
print()
print(f"  AGENT_RUNTIME_KEY={key.decode()}")
print()
print("  Keep this value secret. Anyone with this key can decrypt")
print("  the agent code. Never commit it to git.")
print("=" * 65)
print()

# ── Encrypt ──────────────────────────────────────────────────────────────────

f = Fernet(key)
key_fingerprint = hashlib.sha256(key).hexdigest()

# Wipe and recreate output directory
if OUT_DIR.exists():
    shutil.rmtree(OUT_DIR)
OUT_DIR.mkdir(parents=True)

py_files = sorted(AGENTS_DIR.rglob("*.py"))

if not py_files:
    print("WARNING: No .py files found in Agents/")
    sys.exit(0)

count = 0
for py_file in py_files:
    # Skip pycache files
    if "__pycache__" in py_file.parts:
        continue

    rel      = py_file.relative_to(AGENTS_DIR)
    out_file = OUT_DIR / rel.with_suffix(".enc")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    encrypted = f.encrypt(py_file.read_bytes())
    out_file.write_bytes(encrypted)
    count += 1
    print(f"  [OK] {rel}")

fingerprint_file = OUT_DIR / "key_fingerprint.txt"
fingerprint_file.write_text(key_fingerprint + "\n", encoding="utf-8")

print()
print(f"[DONE] Encrypted {count} agent files -> {OUT_DIR.relative_to(REPO_ROOT)}/")
print(f"[DONE] Wrote key fingerprint -> {fingerprint_file.relative_to(REPO_ROOT)}")

# ── Encrypt support files (credentials.json, etc.) ──────────────────────────

SUPPORT_FILES = [
    "credentials.json",   # Google OAuth client secrets (required for Docs/Drive)
]
SUPPORT_OUT_DIR = REPO_ROOT / "launchway" / "encrypted_support"

if SUPPORT_OUT_DIR.exists():
    shutil.rmtree(SUPPORT_OUT_DIR)
SUPPORT_OUT_DIR.mkdir(parents=True)

support_count = 0
print()
print("Encrypting support files:")
for filename in SUPPORT_FILES:
    src = REPO_ROOT / filename
    if src.exists():
        out = SUPPORT_OUT_DIR / (filename + ".enc")
        out.write_bytes(f.encrypt(src.read_bytes()))
        support_count += 1
        print(f"  [OK] {filename}")
    else:
        print(f"  [SKIP] {filename} (not found at {src})")

print()
print(f"[DONE] Encrypted {support_count} support file(s) -> {SUPPORT_OUT_DIR.relative_to(REPO_ROOT)}/")
print()
print("Next steps:")
print("  1. Set AGENT_RUNTIME_KEY on Railway (printed above)")
print("  2. Build the package:  python -m build")
print("  3. Upload to PyPI:     python -m twine upload dist/*")
print()
print("The .agent_build_key file is gitignored. Do NOT share or commit it.")
