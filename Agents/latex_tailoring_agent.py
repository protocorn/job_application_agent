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
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
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
    main_tex_preview: str
    main_plain_preview: str


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


# Common LaTeX typos introduced by the model (wrong -> correct)
_LATEX_TYPO_FIXES = [
    (r"\\addtollenglth\b", r"\\addtolength"),
    (r"\\addtolenght\b", r"\\addtolength"),
    (r"\\setlenght\b", r"\\setlength"),
    (r"\\setlenglth\b", r"\\setlength"),
    (r"\\begin\{document\}\s*\\begin\{document\}", r"\\begin{document}"),
    (r"\\end\{document\}\s*\\end\{document\}", r"\\end{document}"),
    # Gemini sometimes treats custom commands as environments.
    (r"\\end\{resumeItemListStart\}", r"\\resumeItemListEnd"),
    (r"\\begin\{resumeItemListEnd\}", r"\\resumeItemListStart"),
    (r"\\end\{resumeSubHeadingListStart\}", r"\\resumeSubHeadingListEnd"),
    (r"\\begin\{resumeSubHeadingListEnd\}", r"\\resumeSubHeadingListStart"),
]

# Extra closing brace after one-arg commands (model often adds }} instead of })
_LATEX_EXTRA_BRACE_PATTERNS = [
    (r"(\\vspace\{[^{}]*)\}\}", r"\1}"),
    (r"(\\hspace\{[^{}]*)\}\}", r"\1}"),
    (r"(\\setlength\{[^{}]*\}\{[^{}]*)\}\}", r"\1}"),
    (r"(\\addtolength\{[^{}]*\}\{[^{}]*)\}\}", r"\1}"),
]

_CUSTOM_LIST_COMMAND_NAMES = (
    "resumeItemListStart",
    "resumeItemListEnd",
    "resumeSubHeadingListStart",
    "resumeSubHeadingListEnd",
)


def _fix_latex_typos(tex: str) -> str:
    """Correct common LaTeX command typos introduced by the model so the document compiles."""
    if not tex:
        return tex
    out = tex
    for pattern, replacement in _LATEX_TYPO_FIXES:
        out = re.sub(pattern, replacement, out)
    for pattern, replacement in _LATEX_EXTRA_BRACE_PATTERNS:
        out = re.sub(pattern, replacement, out)
    # Custom resume list macros are commands, not environments.
    # Normalize accidental \begin{...}/\end{...} usage back to command calls.
    for cmd in _CUSTOM_LIST_COMMAND_NAMES:
        out = re.sub(
            rf"\\(?:begin|end)\s*\{{\s*{cmd}\s*\}}",
            rf"\\{cmd}",
            out,
        )
    return out


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


def _safe_extract_zip_bytes(zip_bytes: bytes, dest_dir: str) -> None:
    """
    Safely extract zip bytes to dest_dir, preventing path traversal.
    """
    if not zip_bytes:
        raise ValueError("ZIP is empty.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid ZIP file: {e}") from e

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if not _is_safe_member(name):
                continue

            target_path = os.path.join(dest_dir, name.replace("/", os.sep))
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)

            with zf.open(info, "r") as src, open(target_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _collect_tex_files_from_project(project_dir: str) -> Dict[str, str]:
    """Return {relative_tex_path: content} for all .tex files in project_dir."""
    result: Dict[str, str] = {}
    for root, _, files in os.walk(project_dir):
        for name in files:
            if not name.lower().endswith(".tex"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, project_dir).replace("\\", "/")
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    result[rel] = f.read()
            except Exception:
                logger.warning("Could not read tex file while collecting: %s", rel)
    return dict(sorted(result.items(), key=lambda kv: kv[0]))


def _extract_json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse first JSON object from model text (supports fenced JSON responses).
    Returns None if parsing fails.
    """
    if not text:
        return None

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # Try whole payload first.
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    # Fallback: parse first {...} block.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end + 1]
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _write_debug_artifact(path: str, content: str) -> Optional[str]:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(content or "")
        return path
    except Exception:
        return None


def _apply_updated_tex_files(
    project_dir: str,
    known_tex_paths: Dict[str, str],
    updated_files: Any,
) -> int:
    """
    Apply model-provided file updates to project_dir.
    Returns count of files successfully written.
    """
    replacements_applied = 0
    if not isinstance(updated_files, list):
        return replacements_applied

    for entry in updated_files:
        if not isinstance(entry, dict):
            continue
        path = (entry.get("path") or "").strip().replace("\\", "/")
        content = entry.get("content")
        if not path or not isinstance(content, str):
            continue
        if path not in known_tex_paths:
            logger.warning("Ignoring model update for unknown path: %s", path)
            continue

        fixed_content = _fix_latex_typos(_clean_markdown_artifacts(content))
        target_path = os.path.join(project_dir, path.replace("/", os.sep))
        try:
            with open(target_path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(fixed_content)
            replacements_applied += 1
        except Exception:
            logger.warning("Failed writing model-updated file: %s", path)
    return replacements_applied


def _fix_empty_lists_in_tex(tex: str) -> str:
    """
    Insert a placeholder \\item {} in list environments that have no \\item,
    so pdflatex does not raise "perhaps a missing \\item". Modifies in place
    only when the list content is effectively empty (no \\item between start/end).
    """
    if not tex:
        return tex

    # Some resumes place \resumeItemListStart directly under \resumeSubHeadingListStart
    # without an outer \item. Keep the original structure and add a zero-width outer
    # list item so pdflatex does not drop subsequent bullet content.
    tex = re.sub(
        r"(\\resumeSubHeadingListStart\s*)(?!\\item)(\\resumeItemListStart)",
        r"\1\\item[]\n\2",
        tex,
    )

    def _is_effectively_empty_list_block(block: str) -> bool:
        """
        Treat a list block as empty only if it has no real content
        (ignoring whitespace and comment lines).
        """
        if not block:
            return True
        # Strip LaTeX comments and whitespace.
        no_comments = re.sub(r"(?<!\\)%.*", "", block)
        no_space = re.sub(r"\s+", "", no_comments)
        return no_space == ""

    def fix_block(pattern: str, insert: str) -> str:
        def repl(m):
            between = m.group(2)
            # Only inject a placeholder item for truly empty list bodies.
            # Non-empty blocks may use custom macros like \resumeItem{...}
            # and should not be modified.
            if not _is_effectively_empty_list_block(between):
                return m.group(0)
            return m.group(1) + insert + m.group(3)
        return re.sub(pattern, repl, tex, flags=re.DOTALL)

    # Custom resume list commands (e.g. resumeItemListStart / resumeItemListEnd)
    tex = fix_block(
        r"(\\resumeItemListStart)(.*?)(\\resumeItemListEnd)",
        "\n  \\item {}\n  ",
    )
    # Standard itemize
    tex = fix_block(
        r"(\\begin\{itemize\})(.*?)(\\end\{itemize\})",
        "\n  \\item {}\n  ",
    )
    # Standard enumerate
    tex = fix_block(
        r"(\\begin\{enumerate\})(.*?)(\\end\{enumerate\})",
        "\n  \\item {}\n  ",
    )
    return tex


# Do not apply empty-list fix to these files (macro/definition files, not content).
_FIX_EMPTY_LISTS_SKIP_FILES = frozenset({"custom-commands.tex", "custom-commands"})


def _fix_empty_latex_lists_in_project(project_dir: str) -> None:
    """Walk all .tex files in project_dir and fix empty list blocks so they compile.
    Skips definition files (e.g. custom-commands.tex) where \\item would end up inside a macro.
    """
    for root, _, files in os.walk(project_dir):
        for name in files:
            if not name.lower().endswith(".tex"):
                continue
            base = os.path.splitext(name)[0].lower()
            if base in _FIX_EMPTY_LISTS_SKIP_FILES:
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                # Always normalize common command/environment confusions first.
                new_content = _fix_latex_typos(content)
                new_content = _fix_empty_lists_in_tex(new_content)
                if new_content != content:
                    with open(path, "w", encoding="utf-8", errors="ignore") as f:
                        f.write(new_content)
                    logger.debug("Fixed empty list(s) in %s", path)
            except Exception as e:
                logger.warning("Could not fix empty lists in %s: %s", path, e)


def _compile_latex_project_to_pdf_bytes(
    project_dir: str,
    main_tex_file: str,
    timeout_seconds: int = 90,
) -> Tuple[Optional[bytes], Dict[str, Any]]:
    """
    Compile a LaTeX project in `project_dir` and return compiled PDF bytes.
    Returns (pdf_bytes|None, meta).
    """
    meta: Dict[str, Any] = {
        "success": False,
        "compiler": None,
        "error": None,
        "stdout": "",
        "stderr": "",
        "duration_ms": 0,
    }

    main_rel = (main_tex_file or "").strip().replace("\\", "/")
    if not main_rel:
        meta["error"] = "Missing main .tex file path."
        return None, meta

    main_on_disk = os.path.join(project_dir, main_rel.replace("/", os.sep))
    if not os.path.exists(main_on_disk):
        meta["error"] = f"Main .tex file not found at: {main_rel}"
        return None, meta

    start = datetime.utcnow()

    # Use pdflatex (two passes for refs/TOC). Avoids latexmk behavior differences on Windows/MiKTeX.
    pdflatex_cmd = [
        "pdflatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        main_rel,
    ]

    def _run(cmd: List[str], compiler_name: str) -> subprocess.CompletedProcess:
        meta["compiler"] = compiler_name
        return subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=True,
        )

    try:
        result = _run(pdflatex_cmd, "pdflatex")
        # Second pass for cross-references / TOC
        result = _run(pdflatex_cmd, "pdflatex")

        meta["stdout"] = (result.stdout or "")[-20000:]
        meta["stderr"] = (result.stderr or "")[-20000:]

        pdf_rel = os.path.splitext(main_rel)[0] + ".pdf"
        pdf_on_disk = os.path.join(project_dir, pdf_rel.replace("/", os.sep))
        if not os.path.exists(pdf_on_disk):
            meta["error"] = f"Compilation completed but PDF not found: {pdf_rel}"
            return None, meta

        with open(pdf_on_disk, "rb") as f:
            pdf_bytes = f.read()

        meta["success"] = True
        return pdf_bytes, meta

    except subprocess.TimeoutExpired:
        meta["error"] = f"LaTeX compilation timed out after {timeout_seconds}s."
        return None, meta
    except FileNotFoundError:
        meta["error"] = "pdflatex not found on PATH (install MiKTeX or TeX Live)."
        return None, meta
    except subprocess.CalledProcessError as e:
        meta["stdout"] = (e.stdout or "")[-20000:]
        meta["stderr"] = (e.stderr or "")[-20000:]
        meta["error"] = f"LaTeX compilation failed (compiler={meta.get('compiler')})."
        return None, meta
    finally:
        meta["duration_ms"] = int((datetime.utcnow() - start).total_seconds() * 1000)


def compile_latex_zip_to_pdf(
    latex_zip_base64: str,
    main_tex_file: str,
    output_pdf_path: Optional[str] = None,
    timeout_seconds: int = 90,
) -> Dict[str, Any]:
    """
    Compile a stored LaTeX ZIP (base64) into a PDF.

    Returns:
      {
        success: bool,
        pdf_bytes_base64: str|None,
        pdf_path: str|None,
        pdf_filename: str|None,
        compiler: str|None,
        error: str|None,
      }
    """
    if not latex_zip_base64:
        return {
            "success": False,
            "pdf_bytes_base64": None,
            "pdf_path": None,
            "pdf_filename": None,
            "compiler": None,
            "error": "Missing stored LaTeX ZIP data.",
        }

    zip_bytes = base64.b64decode(latex_zip_base64.encode("ascii"))
    temp_dir = tempfile.mkdtemp(prefix="latex_compile_")
    try:
        _safe_extract_zip_bytes(zip_bytes, temp_dir)
        _fix_empty_latex_lists_in_project(temp_dir)
        pdf_bytes, meta = _compile_latex_project_to_pdf_bytes(
            project_dir=temp_dir,
            main_tex_file=main_tex_file,
            timeout_seconds=timeout_seconds,
        )
        if not pdf_bytes:
            return {
                "success": False,
                "pdf_bytes_base64": None,
                "pdf_path": None,
                "pdf_filename": None,
                "compiler": meta.get("compiler"),
                "error": meta.get("error"),
                "compile_stdout": meta.get("stdout"),
                "compile_stderr": meta.get("stderr"),
            }

        pdf_filename = os.path.splitext(os.path.basename(main_tex_file.replace("\\", "/")))[0] + ".pdf"
        saved_path = None
        if output_pdf_path:
            os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
            with open(output_pdf_path, "wb") as f:
                f.write(pdf_bytes)
            saved_path = output_pdf_path

        return {
            "success": True,
            "pdf_bytes_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "pdf_path": saved_path,
            "pdf_filename": pdf_filename,
            "compiler": meta.get("compiler"),
            "error": None,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_main_tex_preview_from_base64(
    latex_zip_base64: str,
    main_tex_file: str,
    max_chars: int = 4000,
) -> Dict[str, str]:
    """
    Return previews of main .tex file (raw + plain-text) from stored base64 zip.
    """
    if not latex_zip_base64 or not main_tex_file:
        return {"main_tex_preview": "", "main_plain_preview": ""}

    try:
        zip_bytes = base64.b64decode(latex_zip_base64.encode("ascii"))
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            rel = main_tex_file.replace("\\", "/")
            raw = zf.read(rel).decode("utf-8", errors="ignore")
            plain = _strip_latex_to_text(raw)
            return {
                "main_tex_preview": (raw or "")[:max_chars],
                "main_plain_preview": (plain or "")[:max_chars],
            }
    except Exception:
        return {"main_tex_preview": "", "main_plain_preview": ""}


def parse_latex_zip(file_bytes: bytes, requested_main_tex: Optional[str] = None) -> LatexZipData:
    if not file_bytes:
        raise ValueError("LaTeX ZIP is empty.")
    if len(file_bytes) > MAX_ZIP_SIZE_BYTES:
        raise ValueError("LaTeX ZIP is too large (maximum 20MB).")

    temp_dir = tempfile.mkdtemp(prefix="latex_parse_")
    tex_files: List[str] = []
    file_manifest: List[Dict[str, Any]] = []
    merged_text_chunks: List[str] = []
    main_tex_preview = ""
    main_plain_preview = ""

    try:
        # Use the exact same extraction/normalization path as compile/tailor.
        _safe_extract_zip_bytes(file_bytes, temp_dir)
        _fix_empty_latex_lists_in_project(temp_dir)

        for root, _, files in os.walk(temp_dir):
            for name in files:
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, temp_dir).replace("\\", "/")
                ext = os.path.splitext(rel_path)[1].lower()
                size = os.path.getsize(full_path)
                file_manifest.append(
                    {
                        "path": rel_path,
                        "size": size,
                        "extension": ext,
                    }
                )
                if ext == ".tex":
                    tex_files.append(rel_path)
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        if content.strip():
                            merged_text_chunks.append(_strip_latex_to_text(content))
                    except Exception:
                        logger.warning("Could not decode tex file: %s", rel_path)

        main_tex_file = _select_main_tex(tex_files, requested_main_tex)
        plain_text = " ".join(chunk for chunk in merged_text_chunks if chunk).strip()
        if not plain_text:
            plain_text = "LaTeX resume detected. Text extraction produced no content."

        try:
            main_full = os.path.join(temp_dir, main_tex_file.replace("/", os.sep))
            with open(main_full, "r", encoding="utf-8", errors="ignore") as f:
                raw_main = f.read()
            main_tex_preview = (raw_main or "")[:4000]
            main_plain_preview = (_strip_latex_to_text(raw_main) or "")[:4000]
        except Exception:
            pass

        return LatexZipData(
            zip_base64=base64.b64encode(file_bytes).decode("ascii"),
            tex_files=sorted(tex_files),
            main_tex_file=main_tex_file,
            plain_text=plain_text[:25000],
            file_manifest=sorted(file_manifest, key=lambda x: x.get("path", "")),
            main_tex_preview=main_tex_preview,
            main_plain_preview=main_plain_preview,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _count_pdf_pages(pdf_bytes: bytes) -> Optional[int]:
    """Count pages in a PDF from raw bytes. Returns None if counting fails."""
    if not pdf_bytes:
        return None
    try:
        try:
            from pypdf import PdfReader
            return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        except ImportError:
            pass
        try:
            from PyPDF2 import PdfReader  # type: ignore
            return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        except ImportError:
            pass
        # Raw-bytes fallback: count unique /Type /Page dictionary entries.
        count = len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))
        return count if count > 0 else None
    except Exception:
        return None


def _get_effective_page_count(pdf_bytes: Optional[bytes]) -> Optional[int]:
    """
    Return the effective page count by subtracting the trailing blank page
    that pdflatex commonly appends.  Returns at least 1 if any pages exist.
    """
    raw = _count_pdf_pages(pdf_bytes)
    if raw is None:
        return None
    return max(1, raw - 1)


def _collect_bullet_items(project_dir: str, min_length: int = 60) -> List[Dict[str, Any]]:
    """
    Collect all bullet-point items from .tex files in project_dir.
    Handles both \\resumeItem{...} (brace-balanced) and \\item <text> patterns.
    Returns items sorted by text length descending (longest first).
    """
    items: List[Dict[str, Any]] = []
    for root, _, files in os.walk(project_dir):
        for name in files:
            if not name.lower().endswith(".tex"):
                continue
            base = os.path.splitext(name)[0].lower()
            if base in _FIX_EMPTY_LISTS_SKIP_FILES:
                continue
            full_path = os.path.join(root, name)
            rel = os.path.relpath(full_path, project_dir).replace("\\", "/")
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue

            # \resumeItem{...} — brace-balanced extraction
            for m in re.finditer(r"\\resumeItem\{", content):
                start = m.end()
                depth, pos = 1, start
                while pos < len(content) and depth > 0:
                    ch = content[pos]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                    pos += 1
                text = content[start : pos - 1].strip()
                full_match = content[m.start() : pos]
                if len(text) >= min_length:
                    items.append(
                        {
                            "file": rel,
                            "full_path": full_path,
                            "full_match": full_match,
                            "text": text,
                            "length": len(text),
                        }
                    )

            # \item <text> — capture to end of line
            for m in re.finditer(r"\\item\s+(.+)", content):
                text = m.group(1).strip()
                if len(text) >= min_length:
                    items.append(
                        {
                            "file": rel,
                            "full_path": full_path,
                            "full_match": m.group(0),
                            "text": text,
                            "length": len(text),
                        }
                    )

    # Deduplicate by (file, full_match) and sort longest first
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for item in items:
        key = (item["file"], item["full_match"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return sorted(unique, key=lambda x: -x["length"])


def _shorten_bullet_batch(
    client: Any,
    items: List[Dict[str, Any]],
    debug_dir: str,
    debug_prefix: str,
    batch_num: int,
) -> int:
    """
    Ask Gemini to shorten a batch of bullet-point texts to one concise line each.
    Applies the changes directly to the files on disk.
    Returns the number of bullets successfully shortened and written.
    """
    if not items:
        return 0

    items_json = json.dumps(
        [{"id": i, "text": item["text"]} for i, item in enumerate(items)],
        ensure_ascii=False,
    )

    prompt = f"""Shorten these LaTeX resume bullet points so each fits on ONE line (aim for ≤110 chars).
Return STRICT JSON ONLY (no markdown, no explanations):
{{
  "shortened": [
    {{"id": 0, "shortened_text": "..."}}
  ]
}}

Rules:
- Preserve ALL LaTeX commands (\\textbf, \\textit, \\href, etc.) intact in output.
- Keep the core achievement/action and any numbers or metrics.
- Remove filler phrases ("responsible for", "helped to", "worked on", etc.).
- Do NOT use markdown symbols (*, **, #) anywhere in output.
- Return an entry for every id provided, even if unchanged.

BULLETS TO SHORTEN:
{items_json}
"""

    response = generate_content_with_retry(
        client=client,
        model="gemini-2.5-flash",
        contents=prompt,
    )
    _write_debug_artifact(
        os.path.join(debug_dir, f"{debug_prefix}_shorten_batch_{batch_num}.txt"),
        response.text or "",
    )

    obj = _extract_json_object_from_text(response.text or "")
    shortened_list = obj.get("shortened") if isinstance(obj, dict) else []
    if not isinstance(shortened_list, list):
        return 0

    changed = 0
    for entry in shortened_list:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("id")
        new_text = (entry.get("shortened_text") or "").strip()
        if idx is None or not new_text or not isinstance(idx, int) or idx >= len(items):
            continue
        item = items[idx]
        if new_text == item["text"]:
            continue
        try:
            with open(item["full_path"], "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if item["text"] in content:
                new_content = content.replace(item["text"], new_text, 1)
                with open(item["full_path"], "w", encoding="utf-8", errors="ignore") as f:
                    f.write(new_content)
                changed += 1
                logger.debug("Shortened bullet (id=%d, file=%s): %d→%d chars", idx, item["file"], item["length"], len(new_text))
        except Exception as exc:
            logger.warning("Could not apply bullet shortening (id=%s): %s", idx, exc)

    return changed


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
            # Use safe extraction (avoid zip slip)
            for info in zin.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                if not _is_safe_member(name):
                    continue
                target_path = os.path.join(temp_dir, name.replace("/", os.sep))
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zin.open(info, "r") as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            main_path = os.path.join(temp_dir, main_tex_file.replace("/", os.sep))
            if not os.path.exists(main_path):
                raise ValueError(f"Main tex file not found in archive: {main_tex_file}")

            with open(main_path, "r", encoding="utf-8", errors="ignore") as f:
                original_tex = f.read()

        # Compile the untouched original to get a reliable page-count baseline.
        _orig_pdf_bytes, _orig_meta = _compile_latex_project_to_pdf_bytes(
            project_dir=temp_dir,
            main_tex_file=main_tex_file,
            timeout_seconds=90,
        )
        original_page_count = _get_effective_page_count(_orig_pdf_bytes)
        if original_page_count is not None:
            logger.info(
                "Original resume effective page count: %d (raw=%s)",
                original_page_count,
                _count_pdf_pages(_orig_pdf_bytes),
            )
        else:
            logger.warning(
                "Could not determine original page count — page overflow check will be skipped. "
                "Compile error: %s",
                _orig_meta.get("error"),
            )

        project_tex_files = _collect_tex_files_from_project(temp_dir)
        # Keep prompt bounded but preserve full structure.
        project_payload = {
            "main_tex_file": main_tex_file,
            "files": [{"path": p, "content": c} for p, c in project_tex_files.items()],
        }
        project_payload_json = json.dumps(project_payload, ensure_ascii=False)[:120000]

        prompt = f"""You are tailoring a LaTeX resume project for a specific job. The project contains multiple .tex files and custom commands.
Your output will be compiled with pdflatex. Any syntax error will fail the build.

Return STRICT JSON ONLY (no markdown, no explanations):
{{
  "updated_files": [
    {{"path": "relative/path/file.tex", "content": "full updated file content"}}
  ],
  "notes": "short optional note"
}}

Rules for updated_files:
- Include only files that need edits (can be one or many files).
- Paths must match existing .tex file paths from the provided project payload.
- Each content must be complete file content (not partial snippets).
- If no changes are required, return "updated_files": [].

JOB TITLE: {job_title}
COMPANY: {company}
PRIORITY KEYWORDS: {', '.join(prioritized_keywords)}
JOB DESCRIPTION:
{job_description[:12000]}

CRITICAL RULES (must follow exactly):

1) OUTPUT MUST COMPILE: Copy LaTeX command names exactly from the original. Do not misspell or invent commands. Common commands to spell correctly: \\addtolength, \\setlength, \\vspace, \\hspace, \\usepackage, \\begin, \\end, \\section, \\subsection, \\item, \\textbf, \\textit.
   IMPORTANT: resumeItemListStart/resumeItemListEnd and resumeSubHeadingListStart/resumeSubHeadingListEnd are COMMANDS, not environments.
   Never output \\begin{{resumeItemListStart}} or \\end{{resumeItemListStart}} or similar forms.

2) BRACE MATCHING: Every opening brace {{ must have exactly one closing }}. Do not add extra }} after commands like \\vspace{{...}} or \\setlength{{...}}{{...}}. Do not drop closing braces.

3) PRESERVE THE PREAMBLE: Do not change the preamble (everything before \\begin{{document}}) except to add content. Keep all \\addtolength, \\setlength, geometry, and custom command definitions exactly as in the original. Only change body content (sections, bullets, text).

4) CONTENT ONLY: Prefer changing only section content—experience bullets, skills list, summary, project descriptions—to match the job. Keep the same structure, section order, and formatting macros.

5) NO MARKDOWN: Do not use *, **, #, backticks, or markdown wrappers.

6) NO NEW PACKAGES: Do not add \\usepackage unless the original already uses that package.

7) SAME LENGTH: Keep the resume roughly the same length; refine wording and emphasis, do not add long new sections.

LATEX PROJECT PAYLOAD (structured files to tailor):
{project_payload_json}
"""

        # Save what is fed to Gemini for debugging/tracing.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        debug_dir = os.path.join(project_root, "Resumes", "latex_debug")
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_company_dbg = re.sub(r"[^a-zA-Z0-9_-]+", "_", (company or "resume")).strip("_") or "resume"
        debug_prefix = f"{safe_company_dbg}_{ts}"
        debug_input_snapshot_path = _write_debug_artifact(
            os.path.join(debug_dir, f"{debug_prefix}_input_files.json"),
            json.dumps(project_payload, ensure_ascii=False, indent=2),
        )
        debug_prompt_path = _write_debug_artifact(
            os.path.join(debug_dir, f"{debug_prefix}_prompt.txt"),
            prompt,
        )

        response = generate_content_with_retry(
            client=client,
            model="gemini-2.5-flash",
            contents=prompt,
        )
        debug_model_response_path = _write_debug_artifact(
            os.path.join(debug_dir, f"{debug_prefix}_model_response.txt"),
            response.text or "",
        )

        model_obj = _extract_json_object_from_text(response.text or "")
        updated_files = model_obj.get("updated_files") if isinstance(model_obj, dict) else []
        replacements_applied = _apply_updated_tex_files(
            project_dir=temp_dir,
            known_tex_paths=project_tex_files,
            updated_files=updated_files,
        )

        # Backward-compatible fallback: treat response as full main tex if JSON parsing fails.
        if replacements_applied == 0:
            tailored_tex = _clean_markdown_artifacts(response.text)
            tailored_tex = _fix_latex_typos(tailored_tex)

            # Safety fallback if model returns non-LaTeX content.
            if "\\begin{document}" not in tailored_tex or "\\end{document}" not in tailored_tex:
                logger.warning("Model output not valid full LaTeX document; keeping original main tex.")
                tailored_tex = original_tex

            with open(main_path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(tailored_tex)
            replacements_applied = 1

        _fix_empty_latex_lists_in_project(temp_dir)

        # Iterative compile+repair: if compile fails, feed compiler error + current
        # project files back to Gemini and retry up to 3 times.
        compile_attempts = 0
        max_repair_attempts = 3
        tailored_pdf_bytes: Optional[bytes] = None
        pdf_meta: Dict[str, Any] = {"error": "PDF compilation failed."}
        debug_repair_response_paths: List[str] = []
        while compile_attempts <= max_repair_attempts:
            tailored_pdf_bytes, pdf_meta = _compile_latex_project_to_pdf_bytes(
                project_dir=temp_dir,
                main_tex_file=main_tex_file,
                timeout_seconds=90,
            )
            if tailored_pdf_bytes:
                break

            if compile_attempts == max_repair_attempts:
                break

            compile_attempts += 1
            current_files = _collect_tex_files_from_project(temp_dir)
            repair_payload = {
                "main_tex_file": main_tex_file,
                "compile_error": {
                    "error": pdf_meta.get("error"),
                    "stdout_tail": (pdf_meta.get("stdout") or "")[-12000:],
                    "stderr_tail": (pdf_meta.get("stderr") or "")[-12000:],
                },
                "files": [{"path": p, "content": c} for p, c in current_files.items()],
            }
            repair_payload_json = json.dumps(repair_payload, ensure_ascii=False)[:160000]
            repair_prompt = f"""You are fixing a LaTeX resume project that failed compilation.
Return STRICT JSON ONLY:
{{
  "updated_files": [
    {{"path": "relative/path/file.tex", "content": "full updated file content"}}
  ],
  "notes": "short optional note"
}}

Goal:
- Fix ONLY compilation issues from the compiler error.
- Preserve original content as much as possible.
- Do not rewrite unrelated sections.
- Keep custom commands as commands (not environments).
- Escape special chars in text where needed (e.g., use \\& instead of & in normal text).

REPAIR PAYLOAD:
{repair_payload_json}
"""
            repair_response = generate_content_with_retry(
                client=client,
                model="gemini-2.5-flash",
                contents=repair_prompt,
            )
            repair_resp_path = _write_debug_artifact(
                os.path.join(debug_dir, f"{debug_prefix}_repair_attempt_{compile_attempts}.txt"),
                repair_response.text or "",
            )
            if repair_resp_path:
                debug_repair_response_paths.append(repair_resp_path)

            repair_obj = _extract_json_object_from_text(repair_response.text or "")
            repair_updates = repair_obj.get("updated_files") if isinstance(repair_obj, dict) else []
            applied = _apply_updated_tex_files(
                project_dir=temp_dir,
                known_tex_paths=current_files,
                updated_files=repair_updates,
            )
            replacements_applied += applied

            # If no structured updates returned, apply fallback to main tex only.
            if applied == 0 and repair_response.text:
                fallback_tex = _fix_latex_typos(_clean_markdown_artifacts(repair_response.text))
                if "\\begin{document}" in fallback_tex and "\\end{document}" in fallback_tex:
                    with open(main_path, "w", encoding="utf-8", errors="ignore") as f:
                        f.write(fallback_tex)
                    replacements_applied += 1

            _fix_empty_latex_lists_in_project(temp_dir)

        # ── PAGE COUNT CHECK & ITERATIVE BULLET SHRINK ───────────────────────
        # Use effective page counts (raw - 1) to ignore the trailing blank page
        # that pdflatex commonly appends.  Iteratively shorten the longest
        # bullet points one batch at a time, recompiling after each batch, until
        # the effective page count no longer exceeds the original.
        tailored_page_count: Optional[int] = _get_effective_page_count(tailored_pdf_bytes)
        shrink_attempts_log: List[str] = []
        if tailored_pdf_bytes and original_page_count is not None:
            logger.info(
                "Effective page counts — original: %s  tailored: %s",
                original_page_count, tailored_page_count,
            )
            already_shortened: set = set()
            batch_num = 0
            max_shrink_batches = 15  # safety cap

            while (
                tailored_page_count is not None
                and tailored_page_count > original_page_count
                and batch_num < max_shrink_batches
            ):
                batch_num += 1
                # Collect bullet items not yet processed, longest first
                all_items = _collect_bullet_items(temp_dir, min_length=60)
                candidates = [it for it in all_items if it["full_match"] not in already_shortened]
                if not candidates:
                    logger.warning("No more bullet items available to shorten — stopping.")
                    break

                batch = candidates[:5]
                logger.info(
                    "Shrink batch %d/%d: shortening %d bullet(s) "
                    "(longest = %d chars, pages = %s > %s target)",
                    batch_num, max_shrink_batches,
                    len(batch), batch[0]["length"],
                    tailored_page_count, original_page_count,
                )

                applied = _shorten_bullet_batch(
                    client=client,
                    items=batch,
                    debug_dir=debug_dir,
                    debug_prefix=debug_prefix,
                    batch_num=batch_num,
                )
                # Mark these bullets as processed so we don't re-send them
                for it in batch:
                    already_shortened.add(it["full_match"])
                shrink_attempts_log.append(
                    os.path.join(debug_dir, f"{debug_prefix}_shorten_batch_{batch_num}.txt")
                )

                if applied == 0:
                    logger.info("Batch %d produced no changes — skipping recompile.", batch_num)
                    continue

                _fix_empty_latex_lists_in_project(temp_dir)
                shrunk_pdf_bytes, shrunk_meta = _compile_latex_project_to_pdf_bytes(
                    project_dir=temp_dir,
                    main_tex_file=main_tex_file,
                    timeout_seconds=90,
                )
                if shrunk_pdf_bytes:
                    tailored_pdf_bytes = shrunk_pdf_bytes
                    tailored_page_count = _get_effective_page_count(shrunk_pdf_bytes)
                    logger.info(
                        "After shrink batch %d: effective pages = %s",
                        batch_num, tailored_page_count,
                    )
                else:
                    logger.warning(
                        "Shrink batch %d produced a compile error — keeping previous version. "
                        "Error: %s",
                        batch_num, shrunk_meta.get("error"),
                    )
                    break

            if tailored_page_count is not None and tailored_page_count <= original_page_count:
                logger.info("Page count resolved: %s page(s)", tailored_page_count)
            elif tailored_page_count is not None and tailored_page_count > original_page_count:
                logger.warning(
                    "Could not reduce to %d page(s) after %d batch(es) — final: %d page(s)",
                    original_page_count, batch_num, tailored_page_count,
                )
        # ─────────────────────────────────────────────────────────────────────

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

        # Save the already-compiled PDF bytes to disk (avoids a redundant recompile).
        tailored_pdf_path = None
        tailored_pdf_filename = None
        tailored_pdf_base64 = None
        pdf_error = None
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            resumes_dir = os.path.join(project_root, "Resumes")
            os.makedirs(resumes_dir, exist_ok=True)
            safe_company = re.sub(r"[^a-zA-Z0-9_-]+", "_", (company or "resume")).strip("_") or "resume"
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            tailored_pdf_path = os.path.join(resumes_dir, f"tailored_latex_{safe_company}_{ts}.pdf")

            if tailored_pdf_bytes:
                with open(tailored_pdf_path, "wb") as f:
                    f.write(tailored_pdf_bytes)
                tailored_pdf_filename = os.path.basename(tailored_pdf_path)
                tailored_pdf_base64 = base64.b64encode(tailored_pdf_bytes).decode("ascii")
            else:
                # No compiled bytes available — fall back to compiling from the zip
                compile_result = compile_latex_zip_to_pdf(
                    latex_zip_base64=tailored_zip_base64,
                    main_tex_file=main_tex_file,
                    output_pdf_path=tailored_pdf_path,
                    timeout_seconds=90,
                )
                if compile_result.get("success"):
                    tailored_pdf_filename = os.path.basename(tailored_pdf_path)
                    tailored_pdf_base64 = compile_result.get("pdf_bytes_base64")
                else:
                    pdf_error = compile_result.get("error") or "PDF compilation failed."
                    tailored_pdf_path = None
        except Exception as e:
            pdf_error = str(e)
            tailored_pdf_path = None

        return {
            "source_type": "latex_zip",
            "main_tex_file": main_tex_file,
            "tailored_zip_base64": tailored_zip_base64,
            "tailored_zip_filename": f"tailored_{company.replace(' ', '_') or 'resume'}.zip",
            "url": None,
            "pdf_path": tailored_pdf_path,
            "pdf_base64": tailored_pdf_base64,
            "pdf_filename": tailored_pdf_filename or (os.path.splitext(os.path.basename(main_tex_file.replace("\\", "/")))[0] + ".pdf"),
            "pdf_error": pdf_error,
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
            "replacements_applied": replacements_applied,
            "debug_input_snapshot_path": debug_input_snapshot_path,
            "debug_prompt_path": debug_prompt_path,
            "debug_model_response_path": debug_model_response_path,
            "debug_repair_response_paths": debug_repair_response_paths,
            "debug_shrink_attempt_paths": shrink_attempts_log,
            "original_pages": original_page_count,
            "final_pages": tailored_page_count if tailored_page_count is not None else _get_effective_page_count(tailored_pdf_bytes),
            "message": "LaTeX resume tailored and packaged as ZIP.",
        }
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
