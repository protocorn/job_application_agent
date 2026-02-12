"""
LaTeX Resume Tailoring Utilities

Supports:
- ZIP ingestion of multi-file LaTeX resumes (Overleaf exports)
- Main .tex detection
- Plain-text extraction for profile parsing / keyword analysis
- Tailoring main .tex with Gemini while preserving LaTeX syntax
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from google import genai

from gemini_rate_limiter import generate_content_with_retry
from resume_tailoring_agent import extract_job_keywords

logger = logging.getLogger(__name__)

MAX_ZIP_SIZE_BYTES = 20 * 1024 * 1024


@dataclass
class LatexZipData:
    zip_base64: str
    tex_files: List[str]
    main_tex_file: str
    plain_text: str
    file_manifest: List[Dict[str, Any]]


def _is_safe_member(member_name: str) -> bool:
    normalized = member_name.replace("\\", "/")
    return not (
        normalized.startswith("/")
        or normalized.startswith("../")
        or "/../" in normalized
    )


def _clean_markdown_artifacts(text: str) -> str:
    """Remove markdown wrappers/symbols that occasionally leak from model outputs."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"```(?:\w+)?\s*", "", cleaned)
    cleaned = cleaned.replace("```", "").replace("`", "")
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*]\s+", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def _select_main_tex(tex_files: List[str], requested_main: Optional[str] = None) -> str:
    if not tex_files:
        raise ValueError("No .tex files found in ZIP archive.")

    normalized_lookup = {p.lower(): p for p in tex_files}
    if requested_main:
        key = requested_main.strip().replace("\\", "/").lower()
        if key in normalized_lookup:
            return normalized_lookup[key]

    preferred_names = ("main.tex", "resume.tex", "cv.tex")
    for candidate in preferred_names:
        for tex_file in tex_files:
            if tex_file.lower().endswith(candidate):
                return tex_file

    # Deterministic fallback
    return sorted(tex_files)[0]


def _strip_latex_to_text(tex_content: str) -> str:
    """Best-effort text extraction from LaTeX content for profile parsing."""
    text = tex_content
    text = re.sub(r"(?<!\\)%.*", "", text)  # comments
    text = re.sub(r"\\begin\{[^}]+\}", " ", text)
    text = re.sub(r"\\end\{[^}]+\}", " ", text)
    text = re.sub(r"\\item\s+", " - ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r" \1 ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_latex_zip(file_bytes: bytes, requested_main_tex: Optional[str] = None) -> LatexZipData:
    if not file_bytes:
        raise ValueError("LaTeX ZIP is empty.")
    if len(file_bytes) > MAX_ZIP_SIZE_BYTES:
        raise ValueError("LaTeX ZIP is too large (maximum 20MB).")

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes), "r")
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid ZIP file: {e}") from e

    tex_files: List[str] = []
    file_manifest: List[Dict[str, Any]] = []
    merged_text_chunks: List[str] = []

    with zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if not _is_safe_member(name):
                continue

            ext = os.path.splitext(name)[1].lower()
            file_manifest.append(
                {
                    "path": name,
                    "size": info.file_size,
                    "extension": ext,
                }
            )

            if ext == ".tex":
                tex_files.append(name)
                try:
                    content = zf.read(info).decode("utf-8", errors="ignore")
                    if content.strip():
                        merged_text_chunks.append(_strip_latex_to_text(content))
                except Exception:
                    logger.warning("Could not decode tex file: %s", name)

    main_tex_file = _select_main_tex(tex_files, requested_main_tex)
    plain_text = " ".join(chunk for chunk in merged_text_chunks if chunk).strip()
    if not plain_text:
        plain_text = "LaTeX resume detected. Text extraction produced no content."

    return LatexZipData(
        zip_base64=base64.b64encode(file_bytes).decode("ascii"),
        tex_files=sorted(tex_files),
        main_tex_file=main_tex_file,
        plain_text=plain_text[:25000],
        file_manifest=file_manifest,
    )


def tailor_latex_resume_from_base64(
    latex_zip_base64: str,
    main_tex_file: str,
    job_description: str,
    job_title: str = "Unknown Position",
    company: str = "Unknown Company",
) -> Dict[str, Any]:
    if not latex_zip_base64:
        raise ValueError("Missing stored LaTeX ZIP data.")
    if not main_tex_file:
        raise ValueError("Missing main .tex file path.")
    if not job_description:
        raise ValueError("Job description is required.")

    zip_bytes = base64.b64decode(latex_zip_base64.encode("ascii"))
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY environment variable required.")

    client = genai.Client(api_key=api_key)
    keywords = extract_job_keywords(job_description) or {}
    prioritized_keywords = (keywords.get("prioritized_keywords") or [])[:15]

    temp_dir = tempfile.mkdtemp(prefix="latex_tailor_")
    output_zip_path = ""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zin:
            zin.extractall(temp_dir)
            main_path = os.path.join(temp_dir, main_tex_file.replace("/", os.sep))
            if not os.path.exists(main_path):
                raise ValueError(f"Main tex file not found in archive: {main_tex_file}")

            with open(main_path, "r", encoding="utf-8", errors="ignore") as f:
                original_tex = f.read()

        prompt = f"""You are tailoring a LaTeX resume for a specific job.

Return ONLY valid LaTeX content for the full main .tex file.

JOB TITLE: {job_title}
COMPANY: {company}
PRIORITY KEYWORDS: {', '.join(prioritized_keywords)}
JOB DESCRIPTION:
{job_description[:12000]}

CRITICAL RULES:
1) Preserve compilable LaTeX syntax.
2) Do not add markdown symbols (*, **, #, backticks).
3) Do not add new package dependencies unless absolutely required.
4) Keep the resume concise and approximately same length.
5) Prefer updating content/bullets over changing layout macros.

ORIGINAL MAIN TEX:
{original_tex[:45000]}
"""

        response = generate_content_with_retry(
            client=client,
            model="gemini-2.5-flash",
            contents=prompt,
        )
        tailored_tex = _clean_markdown_artifacts(response.text)

        # Safety fallback if model returns non-LaTeX content.
        if "\\begin{document}" not in tailored_tex or "\\end{document}" not in tailored_tex:
            logger.warning("Model output not valid full LaTeX document; keeping original main tex.")
            tailored_tex = original_tex

        with open(main_path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(tailored_tex)

        output_zip_path = os.path.join(temp_dir, "tailored_resume_latex.zip")
        with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(temp_dir):
                for name in files:
                    if name == os.path.basename(output_zip_path):
                        continue
                    full_path = os.path.join(root, name)
                    arcname = os.path.relpath(full_path, temp_dir).replace("\\", "/")
                    zout.write(full_path, arcname)

        with open(output_zip_path, "rb") as f:
            tailored_zip_base64 = base64.b64encode(f.read()).decode("ascii")

        return {
            "source_type": "latex_zip",
            "main_tex_file": main_tex_file,
            "tailored_zip_base64": tailored_zip_base64,
            "tailored_zip_filename": f"tailored_{company.replace(' ', '_') or 'resume'}.zip",
            "url": None,
            "pdf_path": None,
            "keywords": {
                "job_required": prioritized_keywords,
                "already_present": [],
                "newly_added": [],
                "could_not_add": [],
                "total_extracted": len(prioritized_keywords),
            },
            "match_stats": {
                "match_percentage": 0.0,
            },
            "sections_modified": {
                "profile": True,
                "skills": True,
                "projects": True,
            },
            "replacements_applied": 0,
            "message": "LaTeX resume tailored and packaged as ZIP.",
        }
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
