#!/usr/bin/env python3
"""
Debug script: Recreate a PDF from the LaTeX ZIP stored in the database.

Use this to verify that:
  1. The stored latex_zip_base64 decodes correctly
  2. The ZIP contains the expected files
  3. pdflatex can compile it to PDF

Usage (PowerShell):
  # Recreate PDF for a specific user (use their UUID from the database)
  python debug_recreate_latex_pdf.py --user-id "de18962e-29c6-4227-9b0e-28287fdbef3e"

  # List all users who have a LaTeX resume and pick one
  python debug_recreate_latex_pdf.py --list

  # Recreate for first LaTeX user found (no UUID needed)
  python debug_recreate_latex_pdf.py

  # Also save the decoded ZIP to disk for inspection
  python debug_recreate_latex_pdf.py --user-id "..." --save-zip
"""

import argparse
import base64
import os
import sys
import zipfile
import io

# Project root and paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "Agents"))

# Load env before importing database_config
from dotenv import load_dotenv
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

RESUMES_DIR = os.path.join(SCRIPT_DIR, "Resumes")
DEBUG_PDF_PREFIX = "debug_recreated_from_db"
DEBUG_ZIP_PREFIX = "debug_latex_zip_from_db"


def get_latex_profiles(session):
    """Return list of (user_id, UserProfile) for profiles that have LaTeX data."""
    from database_config import UserProfile
    q = session.query(UserProfile).filter(
        UserProfile.latex_zip_base64.isnot(None),
        UserProfile.latex_zip_base64 != "",
        UserProfile.latex_main_tex_path.isnot(None),
        UserProfile.latex_main_tex_path != "",
    )
    return [(p.user_id, p) for p in q.all()]


def decode_and_inspect_zip(latex_zip_base64: str, main_tex_path: str, save_zip_path: str = None):
    """
    Decode base64 to ZIP bytes and optionally save ZIP + list contents.
    Returns (zip_bytes, list_of_filenames) or (None, error_message).
    """
    try:
        raw = latex_zip_base64.encode("ascii")
        zip_bytes = base64.b64decode(raw)
    except Exception as e:
        return None, f"Base64 decode failed: {e}"

    if not zip_bytes:
        return None, "Decoded ZIP is empty."

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        names = zf.namelist()
        zf.close()
    except Exception as e:
        return None, f"ZIP open failed: {e}"

    # Check main tex exists in ZIP
    normalized = {n.replace("\\", "/"): n for n in names}
    main_key = main_tex_path.strip().replace("\\", "/")
    if main_key not in normalized:
        return None, f"Main .tex not in ZIP: {main_tex_path}. Contents: {names}"

    if save_zip_path:
        try:
            os.makedirs(os.path.dirname(save_zip_path) or ".", exist_ok=True)
            with open(save_zip_path, "wb") as f:
                f.write(zip_bytes)
        except Exception as e:
            print(f"  [WARN] Could not save ZIP to {save_zip_path}: {e}")

    return zip_bytes, names


def main():
    parser = argparse.ArgumentParser(description="Recreate PDF from LaTeX ZIP stored in DB")
    parser.add_argument("--user-id", type=str, help="User UUID (from user_profiles.user_id)")
    parser.add_argument("--list", action="store_true", help="List users with LaTeX resume and exit")
    parser.add_argument("--save-zip", action="store_true", help="Save decoded ZIP to Resumes/ for inspection")
    parser.add_argument("--timeout", type=int, default=90, help="pdflatex timeout in seconds")
    args = parser.parse_args()

    from database_config import SessionLocal, UserProfile

    db = SessionLocal()
    try:
        if args.list:
            profiles = get_latex_profiles(db)
            if not profiles:
                print("No users with LaTeX resume found in the database.")
                return 0
            print("Users with LaTeX resume stored:")
            for uid, p in profiles:
                print(f"  user_id: {uid}")
                print(f"    latex_main_tex_path: {p.latex_main_tex_path}")
                print(f"    latex_zip_base64 length (chars): {len(p.latex_zip_base64 or '')}")
            return 0

        # Resolve user_id
        user_id = args.user_id
        if not user_id:
            profiles = get_latex_profiles(db)
            if not profiles:
                print("No users with LaTeX resume found. Use --list to check.")
                return 1
            user_id = str(profiles[0][0])
            print(f"No --user-id given. Using first LaTeX user: {user_id}")

        try:
            from uuid import UUID
            user_uuid = UUID(user_id)
        except Exception:
            print(f"Invalid user_id (must be UUID): {user_id}")
            return 1

        profile = db.query(UserProfile).filter(UserProfile.user_id == user_uuid).first()
        if not profile:
            print(f"No profile found for user_id: {user_id}")
            return 1

        latex_b64 = profile.latex_zip_base64
        main_tex = profile.latex_main_tex_path or "resume.tex"

        if not latex_b64:
            print("This user's profile has no latex_zip_base64.")
            return 1

        print("Step 1: Decode base64 and verify ZIP contents")
        print(f"  main_tex_path from DB: {main_tex}")
        save_zip_path = None
        if args.save_zip:
            os.makedirs(RESUMES_DIR, exist_ok=True)
            save_zip_path = os.path.join(RESUMES_DIR, f"{DEBUG_ZIP_PREFIX}_{user_uuid.hex[:8]}.zip")
            print(f"  save_zip_path: {save_zip_path}")
        zip_bytes, file_list = decode_and_inspect_zip(latex_b64, main_tex, save_zip_path)
        if zip_bytes is None:
            print(f"  FAILED: {file_list}")
            return 1
        print(f"  OK. ZIP contains {len(file_list)} entries: {file_list}")

        print("Step 2: Compile LaTeX ZIP to PDF")
        from latex_tailoring_agent import compile_latex_zip_to_pdf

        os.makedirs(RESUMES_DIR, exist_ok=True)
        out_pdf = os.path.join(RESUMES_DIR, f"{DEBUG_PDF_PREFIX}_{user_uuid.hex[:8]}.pdf")

        result = compile_latex_zip_to_pdf(
            latex_zip_base64=latex_b64,
            main_tex_file=main_tex,
            output_pdf_path=out_pdf,
            timeout_seconds=args.timeout,
        )

        if result.get("success"):
            print(f"  OK. PDF written to: {out_pdf}")
            if result.get("compiler"):
                print(f"  Compiler used: {result['compiler']}")
        else:
            print(f"  FAILED: {result.get('error', 'Unknown error')}")
            if result.get("compiler"):
                print(f"  Compiler: {result['compiler']}")
            # Show LaTeX output so user can see the real error (e.g. missing package, undefined control sequence)
            out = (result.get("compile_stdout") or "") + "\n" + (result.get("compile_stderr") or "")
            if out.strip():
                tail = out.strip()[-3000:]  # last 3000 chars usually has the error
                print("  LaTeX output tail:")
                print("-" * 60)
                for line in tail.splitlines():
                    print(f"  {line}")
                print("-" * 60)
            print("  Tip: If the error points to a file in the ZIP (e.g. src/achievements.tex),")
            print("  fix that file in your Overleaf/LaTeX project and re-upload the ZIP.")
            return 1

        print("Done. You can open the PDF to confirm it matches the uploaded resume.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
