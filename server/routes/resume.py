import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict

import requests
from flask import Blueprint, jsonify, request, send_file

from auth import require_auth
from google_oauth_service import GoogleOAuthService
from latex_tailoring_agent import (
    compile_latex_zip_to_pdf,
    get_main_tex_preview_from_base64,
    parse_latex_zip,
)
from profile_service import ProfileService
from rate_limiter import rate_limit


def create_resume_blueprint(
    *,
    extract_google_doc_with_oauth: Callable[[str, int], str],
    process_resume_with_llm: Callable[[str], Dict[str, Any]],
    compute_resume_text_hash: Callable[[str], str],
) -> Blueprint:
    """Create resume processing and download routes."""
    resume_bp = Blueprint("resume", __name__)

    @resume_bp.route("/api/profile/keywords/extract", methods=["POST"])
    @require_auth
    @rate_limit("profile_keyword_extract_per_user_per_day")
    @rate_limit("api_requests_per_user_per_minute")
    def extract_profile_keywords():
        try:
            from agent_profile_service import AgentProfileService
            from gemini_key_manager import AiEngineNotConfiguredError
            from resume_keyword_extractor import ResumeKeywordExtractor

            user_id = request.current_user["id"]
            body = request.json or {}

            try:
                key_manager = AgentProfileService.get_gemini_key_manager(user_id)
            except Exception:
                key_manager = None

            if key_manager is None or not key_manager.is_configured:
                return (
                    jsonify(
                        {
                            "error": "ai_engine_not_configured",
                            "message": "Please configure your AI Engine (primary key method) before using AI features.",
                        }
                    ),
                    403,
                )

            resume_text = ""
            profile_result = ProfileService.get_complete_profile(user_id)
            profile = profile_result.get("profile", {}) if profile_result.get("success") else {}
            existing_keywords = profile.get("resume_keywords") if isinstance(profile, dict) else {}
            if not isinstance(existing_keywords, dict):
                existing_keywords = {}
            existing_hash = str(existing_keywords.get("resume_text_hash") or "")

            raw_text = body.get("resume_text", "").strip()
            if raw_text:
                resume_text = raw_text

            if not resume_text:
                if not profile_result.get("success"):
                    return jsonify({"error": "Profile not found"}), 404

                resume_url = profile.get("resume_url", "")
                stored_text = profile.get("resume_text", "")

                if resume_url:
                    resume_text = extract_google_doc_with_oauth(resume_url, user_id)
                    if not resume_text:
                        return (
                            jsonify(
                                {
                                    "error": "Could not fetch resume. Make sure it is shared as 'Anyone with the link can view'."
                                }
                            ),
                            400,
                        )
                elif stored_text:
                    resume_text = stored_text
                else:
                    return (
                        jsonify(
                            {
                                "error": "No resume found in your profile. Please upload a resume first."
                            }
                        ),
                        400,
                    )

            resume_hash = compute_resume_text_hash(resume_text)
            has_cached_keywords = any(
                existing_keywords.get(k) for k in ("skills", "domains", "job_titles", "industries")
            )
            if resume_hash and existing_hash == resume_hash and has_cached_keywords:
                return (
                    jsonify(
                        {
                            "success": True,
                            "resume_keywords": existing_keywords,
                            "cached": True,
                            "message": "Resume text unchanged; using cached keywords.",
                        }
                    ),
                    200,
                )

            extractor = ResumeKeywordExtractor(key_manager=key_manager)
            keywords = extractor.extract_from_text(resume_text)

            if not keywords:
                return jsonify({"error": "Keyword extraction failed"}), 500

            if isinstance(keywords, dict):
                keywords["resume_text_hash"] = resume_hash

            ProfileService.create_or_update_profile(user_id, {"resume_keywords": keywords})
            return jsonify({"success": True, "resume_keywords": keywords, "cached": False}), 200

        except AiEngineNotConfiguredError as exc:
            return (
                jsonify({"error": "ai_engine_not_configured", "message": str(exc)}),
                403,
            )
        except Exception as exc:
            logging.error(f"Error extracting resume keywords: {exc}", exc_info=True)
            return jsonify({"error": "Keyword extraction failed"}), 500

    @resume_bp.route("/api/process-resume", methods=["POST"])
    @require_auth
    @rate_limit("resume_processing_per_user_per_day")
    @rate_limit("api_requests_per_user_per_minute")
    def process_resume():
        try:
            user_id = request.current_user["id"]
            resume_url = request.json["resume_url"]
            resume_text = extract_google_doc_with_oauth(resume_url, user_id)

            if not resume_text:
                return jsonify({"error": "Failed to extract resume text"}), 400

            logging.info(f"Processing resume with LLM (length: {len(resume_text)} chars)")
            profile_data = process_resume_with_llm(resume_text)

            if profile_data is None:
                return (
                    jsonify(
                        {
                            "error": "Failed to process resume with Gemini",
                            "success": False,
                        }
                    ),
                    500,
                )

            try:
                save_payload = {
                    **profile_data,
                    "resume_url": resume_url,
                    "resume_source_type": "google_doc",
                    "resume_text": "",
                    "resume_filename": "",
                    "resume_file_base64": "",
                }
                ProfileService.create_or_update_profile(user_id, save_payload, preserve_existing=True)
            except Exception as persist_err:
                logging.warning(f"Could not persist resume data: {persist_err}")

            return (
                jsonify(
                    {
                        "profile_data": profile_data,
                        "success": True,
                        "message": "Resume processed successfully"
                        + (
                            " (using private Google Doc access)"
                            if GoogleOAuthService.is_connected(user_id)
                            else ""
                        ),
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as exc:
            logging.error(f"Error processing resume: {exc}")
            return jsonify({"error": "Failed to process resume"}), 500

    @resume_bp.route("/api/upload-resume", methods=["POST"])
    @require_auth
    @rate_limit("resume_processing_per_user_per_day")
    @rate_limit("api_requests_per_user_per_minute")
    def upload_resume():
        try:
            import base64

            user_id = request.current_user["id"]

            if "resume" not in request.files:
                return jsonify({"error": "No file provided"}), 400

            file = request.files["resume"]
            if file.filename == "":
                return jsonify({"error": "No file selected"}), 400

            filename = file.filename.lower()
            if not (filename.endswith(".pdf") or filename.endswith(".docx")):
                return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

            file.seek(0, 2)
            file_size = file.tell()
            file.seek(0)
            if file_size > 10 * 1024 * 1024:
                return jsonify({"error": "File too large (maximum 10MB)"}), 400
            if file_size == 0:
                return jsonify({"error": "File is empty"}), 400

            file_bytes = file.read()
            resume_text = ""
            source_type = ""

            if filename.endswith(".pdf"):
                source_type = "pdf"
                try:
                    import io
                    from PyPDF2 import PdfReader

                    reader = PdfReader(io.BytesIO(file_bytes))
                    pages = [p.extract_text() or "" for p in reader.pages]
                    resume_text = "\n".join(pages).strip()
                except Exception as pdf_err:
                    logging.error(f"PDF text extraction failed: {pdf_err}")
                    return jsonify({"error": "Could not read PDF"}), 400

            elif filename.endswith(".docx"):
                source_type = "docx"
                try:
                    import io

                    try:
                        import docx
                    except ImportError:
                        return (
                            jsonify(
                                {
                                    "error": "python-docx is not installed on the server. Please contact support or upload a PDF instead."
                                }
                            ),
                            500,
                        )
                    doc = docx.Document(io.BytesIO(file_bytes))
                    resume_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
                except Exception as docx_err:
                    logging.error(f"DOCX text extraction failed: {docx_err}")
                    return jsonify({"error": "Could not read DOCX"}), 400

            if not resume_text or len(resume_text) < 50:
                return (
                    jsonify(
                        {
                            "error": (
                                "Could not extract enough text from the file. "
                                "Please ensure it contains selectable text (not a scanned image)."
                            )
                        }
                    ),
                    400,
                )

            logging.info(f"Extracted {len(resume_text)} chars from {source_type.upper()} for user {user_id}")

            profile_data = process_resume_with_llm(resume_text)
            if profile_data is None:
                return (
                    jsonify({"error": "Failed to process resume with Gemini", "success": False}),
                    500,
                )

            original_filename = file.filename or f"resume.{source_type}"
            try:
                save_payload = {
                    **profile_data,
                    "resume_url": "",
                    "resume_source_type": source_type,
                    "resume_text": resume_text,
                    "resume_filename": original_filename,
                    "resume_file_base64": base64.b64encode(file_bytes).decode("utf-8"),
                }
                ProfileService.create_or_update_profile(user_id, save_payload, preserve_existing=True)
            except Exception as persist_err:
                logging.warning(f"Could not persist resume data: {persist_err}")

            return (
                jsonify(
                    {
                        "success": True,
                        "profile_data": profile_data,
                        "resume_url": "",
                        "source_type": source_type,
                        "resume_filename": original_filename,
                        "tailoring_available": False,
                        "message": (
                            f"{source_type.upper()} resume uploaded and profile populated successfully. "
                            "⚠️ Resume tailoring is not available for PDF/DOCX uploads. "
                            "To enable tailoring, please upload your resume as a Google Doc URL."
                        ),
                    }
                ),
                200,
            )

        except Exception as exc:
            logging.error(f"Error uploading resume: {exc}")
            return jsonify({"error": "Failed to upload resume"}), 500

    @resume_bp.route("/api/upload-latex-resume", methods=["POST"])
    @require_auth
    @rate_limit("resume_processing_per_user_per_day")
    @rate_limit("api_requests_per_user_per_minute")
    def upload_latex_resume():
        try:
            user_id = request.current_user["id"]
            if "resume_zip" not in request.files:
                return jsonify({"error": "No ZIP file provided"}), 400

            file = request.files["resume_zip"]
            if file.filename == "":
                return jsonify({"error": "No ZIP file selected"}), 400

            filename = file.filename.lower()
            if not filename.endswith(".zip"):
                return jsonify({"error": "Only ZIP files are supported for LaTeX resumes"}), 400

            file.seek(0, 2)
            file_size = file.tell()
            file.seek(0)
            if file_size > 20 * 1024 * 1024:
                return jsonify({"error": "ZIP file too large (maximum 20MB)"}), 400
            if file_size == 0:
                return jsonify({"error": "ZIP file is empty"}), 400

            main_tex_file = (request.form.get("main_tex_file") or "").strip() or None
            zip_bytes = file.read()
            parsed = parse_latex_zip(zip_bytes, requested_main_tex=main_tex_file)

            profile_data = process_resume_with_llm(parsed.plain_text)
            if profile_data is None:
                return (
                    jsonify(
                        {
                            "error": "Failed to process LaTeX resume with Gemini",
                            "success": False,
                        }
                    ),
                    500,
                )

            ProfileService.create_or_update_profile(
                user_id,
                {
                    "resume_source_type": "latex_zip",
                    "resume_url": "",
                    "latex_zip_base64": parsed.zip_base64,
                    "latex_main_tex_path": parsed.main_tex_file,
                    "latex_file_manifest": parsed.file_manifest,
                    "latex_uploaded_at": datetime.utcnow(),
                },
            )

            pdf_generated = False
            pdf_path = None
            pdf_error = None
            try:
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                resumes_dir = os.path.join(project_root, "Resumes")
                os.makedirs(resumes_dir, exist_ok=True)
                pdf_path = os.path.join(resumes_dir, f"latex_resume_{user_id}.pdf")

                compile_result = compile_latex_zip_to_pdf(
                    latex_zip_base64=parsed.zip_base64,
                    main_tex_file=parsed.main_tex_file,
                    output_pdf_path=pdf_path,
                    timeout_seconds=90,
                )
                pdf_generated = bool(compile_result.get("success"))
                if not pdf_generated:
                    pdf_path = None
                    pdf_error = compile_result.get("error")
                else:
                    ProfileService.create_or_update_profile(user_id, {"resume_url": pdf_path})
            except Exception as pdf_exc:
                pdf_generated = False
                pdf_path = None
                pdf_error = str(pdf_exc)

            return (
                jsonify(
                    {
                        "success": True,
                        "message": "LaTeX ZIP uploaded successfully. Tailoring will use your stored LaTeX source.",
                        "profile_data": profile_data,
                        "resume_source_type": "latex_zip",
                        "main_tex_file": parsed.main_tex_file,
                        "tex_files": parsed.tex_files,
                        "main_tex_preview": parsed.main_tex_preview,
                        "main_plain_preview": parsed.main_plain_preview,
                        "latex_file_manifest": parsed.file_manifest,
                        "pdf_generated": pdf_generated,
                        "pdf_path": pdf_path,
                        "pdf_error": pdf_error,
                    }
                ),
                200,
            )
        except Exception as exc:
            logging.error(f"Error uploading LaTeX resume: {exc}")
            return jsonify({"error": f"Failed to upload LaTeX resume: {str(exc)}"}), 500

    @resume_bp.route("/api/latex-resume/preview", methods=["GET"])
    @require_auth
    def get_latex_resume_preview():
        try:
            user_id = request.current_user["id"]
            from database_config import SessionLocal, UserProfile

            db = SessionLocal()
            try:
                profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
                if not profile or not profile.latex_zip_base64 or not profile.latex_main_tex_path:
                    return (
                        jsonify({"success": False, "error": "No LaTeX resume source found for this user."}),
                        404,
                    )

                preview = get_main_tex_preview_from_base64(
                    latex_zip_base64=profile.latex_zip_base64,
                    main_tex_file=profile.latex_main_tex_path,
                )
                return (
                    jsonify(
                        {
                            "success": True,
                            "main_tex_file": profile.latex_main_tex_path,
                            "main_tex_preview": preview.get("main_tex_preview", ""),
                            "main_plain_preview": preview.get("main_plain_preview", ""),
                        }
                    ),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error getting LaTeX preview: {exc}")
            return jsonify({"error": f"Failed to get LaTeX preview: {str(exc)}"}), 500

    @resume_bp.route("/api/resume/pdf", methods=["GET"])
    @require_auth
    def download_resume_pdf():
        try:
            user_id = request.current_user["id"]

            resume_url = request.args.get("url", "").strip()
            if not resume_url:
                result = ProfileService.get_profile(user_id)
                resume_url = (result or {}).get("resume_url", "").strip()

            if not resume_url:
                return jsonify({"error": "No resume URL found in profile and none supplied via ?url="}), 400

            if "docs.google.com" not in resume_url and "drive.google.com" not in resume_url:
                return jsonify({"error": "URL is not a Google Docs / Drive URL"}), 400

            import io as _io
            import re as _re
            from googleapiclient.discovery import build as _build
            from googleapiclient.http import MediaIoBaseDownload as _DL

            doc_match = _re.search(r"/(?:document|file)/d/([a-zA-Z0-9-_]+)", resume_url)
            if not doc_match:
                return jsonify({"error": "Could not parse document ID from URL"}), 400
            doc_id = doc_match.group(1)

            credentials = GoogleOAuthService.get_credentials(user_id)
            if credentials:
                try:
                    drive_svc = _build("drive", "v3", credentials=credentials)
                    req = drive_svc.files().export_media(fileId=doc_id, mimeType="application/pdf")
                    buf = _io.BytesIO()
                    dl = _DL(buf, req)
                    done = False
                    while not done:
                        _, done = dl.next_chunk()

                    pdf_bytes = buf.getvalue()
                    if pdf_bytes:
                        logging.info(f"Served resume PDF via OAuth for user {user_id} ({len(pdf_bytes)} bytes)")
                        return send_file(
                            _io.BytesIO(pdf_bytes),
                            mimetype="application/pdf",
                            as_attachment=True,
                            download_name="resume.pdf",
                        )
                    logging.warning(f"OAuth export returned empty PDF for user {user_id}")
                except Exception as oauth_err:
                    logging.warning(f"OAuth PDF export failed for user {user_id}: {oauth_err}")

            export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
            resp = requests.get(export_url, timeout=30)
            if resp.status_code == 200 and "application/pdf" in resp.headers.get("Content-Type", ""):
                logging.info(f"Served resume PDF via public export for user {user_id}")
                return send_file(
                    _io.BytesIO(resp.content),
                    mimetype="application/pdf",
                    as_attachment=True,
                    download_name="resume.pdf",
                )

            if not credentials:
                return (
                    jsonify(
                        {
                            "error": "Google account not connected. Connect your Google account in the app to allow private Doc access.",
                            "google_not_connected": True,
                        }
                    ),
                    403,
                )

            return (
                jsonify({"error": "Could not export Google Doc as PDF. Check that the connected account has access."}),
                500,
            )

        except Exception as exc:
            logging.error(f"Error in download_resume_pdf: {exc}")
            return jsonify({"error": str(exc)}), 500

    @resume_bp.route("/api/latex-resume/pdf", methods=["GET"])
    @require_auth
    def download_latex_resume_pdf():
        try:
            user_id = request.current_user["id"]
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            resumes_dir = os.path.join(project_root, "Resumes")
            os.makedirs(resumes_dir, exist_ok=True)
            pdf_path = os.path.join(resumes_dir, f"latex_resume_{user_id}.pdf")

            if not os.path.exists(pdf_path):
                from database_config import SessionLocal, UserProfile

                db = SessionLocal()
                try:
                    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
                    if not profile or not profile.latex_zip_base64 or not profile.latex_main_tex_path:
                        return (
                            jsonify({"success": False, "error": "No LaTeX resume source found for this user."}),
                            404,
                        )

                    compile_result = compile_latex_zip_to_pdf(
                        latex_zip_base64=profile.latex_zip_base64,
                        main_tex_file=profile.latex_main_tex_path,
                        output_pdf_path=pdf_path,
                        timeout_seconds=90,
                    )
                    if not compile_result.get("success"):
                        return (
                            jsonify(
                                {
                                    "success": False,
                                    "error": compile_result.get("error")
                                    or "Failed to compile LaTeX to PDF.",
                                }
                            ),
                            500,
                        )
                finally:
                    db.close()

            try:
                return send_file(
                    pdf_path,
                    mimetype="application/pdf",
                    as_attachment=True,
                    download_name="resume.pdf",
                )
            except TypeError:
                return send_file(
                    pdf_path,
                    mimetype="application/pdf",
                    as_attachment=True,
                    attachment_filename="resume.pdf",
                )

        except Exception as exc:
            logging.error(f"Error downloading LaTeX PDF: {exc}")
            return jsonify({"error": f"Failed to download LaTeX PDF: {str(exc)}"}), 500

    return resume_bp

