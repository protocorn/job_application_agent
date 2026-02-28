"""
Resume Keyword Extractor

Extracts structured professional keywords from a user's resume using a
lightweight Gemini model. Keywords are stored in the user's profile under
'resume_keywords' and reused for job matching and resume tailoring — no
domain-specific assumptions are made, so the extractor works for any profession.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Lightweight model for fast, cheap keyword extraction.
# gemini-2.0-flash-lite is the stable default; swap to gemini-2.5-flash-lite
# when it becomes generally available via the API.
_DEFAULT_MODEL = os.getenv("KEYWORD_EXTRACTION_MODEL", "gemini-2.0-flash-lite")
_FALLBACK_MODEL = "gemini-1.5-flash"

_EXTRACT_PROMPT = """\
You are a professional resume parser. Extract structured keywords from the resume below.

Return ONLY a valid JSON object with EXACTLY these keys — no extra text, no markdown fences:
{
  "skills": ["specific technical/professional skills, tools, software, platforms, technologies, methodologies"],
  "job_titles": ["job titles this person has held or is targeting"],
  "industries": ["industries or sectors (e.g. fintech, healthcare, SaaS, manufacturing, education, logistics)"],
  "domains": ["professional domains (e.g. machine learning, web development, data engineering, DevOps, accounting, supply chain, mechanical design, clinical research)"],
  "education_fields": ["academic fields of study (e.g. Computer Science, Mechanical Engineering, Finance, Nursing)"],
  "experience_level": "one of: entry / mid / senior / lead / executive",
  "years_of_experience": 0
}

Rules:
- Be comprehensive: include ALL technologies, frameworks, languages, tools, methodologies, certifications
- This tool is used across ALL professions — include domain-specific terms for engineering, finance, medicine, law, sales, design, etc.
- Do NOT include company names, personal information (name, email, phone), or generic filler words like "team", "project", "experience"
- For years_of_experience: integer estimate based on work history (0 if unclear)

Resume text:
"""


def _gdoc_id_from_url(url: str) -> Optional[str]:
    """Extract Google Docs document ID from various URL formats."""
    match = re.search(r'/document/d/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None


def _fetch_gdoc_as_text(url: str) -> Optional[str]:
    """
    Download a Google Doc as plain text.
    Works for docs shared with 'Anyone with the link can view'.
    """
    try:
        import requests as req
        doc_id = _gdoc_id_from_url(url)
        if not doc_id:
            logger.warning(f"Could not extract doc ID from URL: {url}")
            return None
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        resp = req.get(export_url, timeout=15, allow_redirects=True)
        if resp.ok and len(resp.text.strip()) > 50:
            return resp.text
        logger.warning(f"Public export returned status {resp.status_code} for doc {doc_id}")
    except Exception as e:
        logger.warning(f"Failed to fetch Google Doc text: {e}")
    return None


class ResumeKeywordExtractor:
    """
    Extracts structured professional keywords from a resume using Gemini.

    The extracted keywords are domain-agnostic and work for any profession.
    Results include: skills, job_titles, industries, domains, education_fields,
    experience_level, years_of_experience, and an extracted_at timestamp.

    Usage::

        extractor = ResumeKeywordExtractor()  # uses GOOGLE_API_KEY env var
        keywords = extractor.extract_from_url("https://docs.google.com/document/d/...")
        # or, if you already have the text:
        keywords = extractor.extract_from_text("John Doe, Software Engineer...")
    """

    def __init__(
        self,
        api_key: str = None,
        model_name: str = _DEFAULT_MODEL,
    ):
        self.api_key = (
            api_key
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("GEMINI_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "A Gemini API key is required. Set GOOGLE_API_KEY or pass api_key=..."
            )
        self.model_name = model_name

    # ── public interface ───────────────────────────────────────────────────

    def extract_from_url(self, resume_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a Google Doc by URL and extract keywords from it.

        Returns a keyword dict, or None if the document cannot be fetched.
        Call extract_from_text() directly if you already have the resume text.
        """
        text = _fetch_gdoc_as_text(resume_url)
        if not text:
            logger.warning(
                "Could not fetch resume text from URL — "
                "make sure the doc is shared as 'Anyone with the link can view'."
            )
            return None
        return self.extract_from_text(text)

    def extract_from_text(self, resume_text: str) -> Dict[str, Any]:
        """
        Extract keywords from raw resume text.

        Returns a dict with keys: skills, job_titles, industries, domains,
        education_fields, experience_level, years_of_experience, extracted_at.
        Never raises — returns an empty-but-valid result on extraction failure.
        """
        if not resume_text or not resume_text.strip():
            logger.error("extract_from_text called with empty text")
            return self._empty_result()

        # Cap at ~12 000 chars to keep the prompt cheap and fast
        prompt = _EXTRACT_PROMPT + resume_text[:12_000]

        raw = self._call_gemini(prompt)
        if raw is None:
            return self._empty_result()
        return self._parse_response(raw)

    # ── internal ───────────────────────────────────────────────────────────

    def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini and return raw text, trying fallback model on failure."""
        for model_name in [self.model_name, _FALLBACK_MODEL]:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                model = genai.GenerativeModel(model_name)
                resp = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.1,
                        max_output_tokens=1024,
                    ),
                )
                logger.debug(f"Gemini keyword extraction succeeded (model={model_name})")
                return resp.text.strip()
            except Exception as e:
                logger.warning(f"Model {model_name!r} failed: {e}")
        logger.error("All Gemini models failed during keyword extraction")
        return None

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """Parse JSON from Gemini's response, stripping any markdown fences."""
        clean = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        clean = re.sub(r'\s*```\s*$', '', clean, flags=re.MULTILINE).strip()

        data: Dict[str, Any] = {}
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            # Try to extract a JSON object substring
            m = re.search(r'\{.*\}', clean, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    logger.error("Could not parse JSON from Gemini response")
                    return self._empty_result()
            else:
                logger.error("No JSON object found in Gemini response")
                return self._empty_result()

        result: Dict[str, Any] = {
            "skills":            [str(s) for s in data.get("skills", []) if s],
            "job_titles":        [str(t) for t in data.get("job_titles", []) if t],
            "industries":        [str(i) for i in data.get("industries", []) if i],
            "domains":           [str(d) for d in data.get("domains", []) if d],
            "education_fields":  [str(f) for f in data.get("education_fields", []) if f],
            "experience_level":  (str(data.get("experience_level", "") or "mid").lower()),
            "years_of_experience": int(data.get("years_of_experience") or 0),
            "extracted_at":      datetime.now(timezone.utc).isoformat(),
        }

        total_kw = sum(
            len(result[k]) for k in ["skills", "job_titles", "industries", "domains"]
        )
        logger.info(
            f"Extracted {total_kw} keywords from resume "
            f"({len(result['skills'])} skills, {len(result['domains'])} domains, "
            f"model={self.model_name})"
        )
        return result

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {
            "skills": [],
            "job_titles": [],
            "industries": [],
            "domains": [],
            "education_fields": [],
            "experience_level": "mid",
            "years_of_experience": 0,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
