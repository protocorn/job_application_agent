from __future__ import annotations

from typing import Any, Dict, List


MIN_TAILORING_SCORE = 45


def _has_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _count_non_empty_items(values: Any) -> int:
    if not isinstance(values, list):
        return 0
    count = 0
    for item in values:
        if isinstance(item, dict):
            if any(_has_text(v) for v in item.values() if not isinstance(v, (list, dict))):
                count += 1
        elif _has_text(item):
            count += 1
    return count


def _count_skill_entries(skills: Any) -> int:
    if isinstance(skills, dict):
        total = 0
        for value in skills.values():
            if isinstance(value, list):
                total += len([v for v in value if _has_text(v)])
            elif _has_text(value):
                total += 1
        return total
    if isinstance(skills, list):
        return len([v for v in skills if _has_text(v)])
    return 0


def score_profile_strength(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = profile or {}

    project_count = _count_non_empty_items(profile.get("projects"))
    work_count = _count_non_empty_items(
        profile.get("work experience") or profile.get("work_experience")
    )
    education_count = _count_non_empty_items(profile.get("education"))
    skill_count = _count_skill_entries(profile.get("skills"))
    has_summary = _has_text(profile.get("summary"))
    has_resume = _has_text(profile.get("resume_url")) or _has_text(profile.get("resume_text"))
    has_contact = all(
        _has_text(profile.get(field)) for field in ("first name", "last name", "email")
    )

    score = 0
    score += min(25, project_count * 8)
    score += min(20, work_count * 10)
    score += min(15, education_count * 8)
    score += min(20, skill_count)
    score += 10 if has_summary else 0
    score += 5 if has_resume else 0
    score += 5 if has_contact else 0
    score = min(100, score)

    hints: List[str] = []
    if project_count < 2:
        hints.append("Add at least 2 detailed projects to improve project swapping quality.")
    if work_count < 1:
        hints.append("Add at least 1 work experience entry with measurable outcomes.")
    if skill_count < 10:
        hints.append("Expand skills with concrete tools/frameworks used in your projects.")
    if not has_summary:
        hints.append("Add a concise summary so tailoring has stronger context.")
    if not has_resume:
        hints.append("Upload a resume source (Google Doc, PDF, DOCX, or LaTeX ZIP).")

    nudges = [
        "Tip: Keep your Launchway profile projects updated to get better project swaps.",
        "Tip: Add technologies and outcomes for each project so generated bullets are precise.",
    ]

    return {
        "score": score,
        "gating_passed": score >= MIN_TAILORING_SCORE,
        "minimum_score": MIN_TAILORING_SCORE,
        "project_count": project_count,
        "work_experience_count": work_count,
        "education_count": education_count,
        "skill_entry_count": skill_count,
        "hints": hints[:4],
        "nudges": nudges,
    }
