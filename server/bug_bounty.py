import hashlib
from typing import Dict, Tuple, Any


SEVERITY_REWARD_MAP = {
    "low": {"resume_bonus": 1, "job_apply_bonus": 1},
    "medium": {"resume_bonus": 2, "job_apply_bonus": 2},
    "high": {"resume_bonus": 3, "job_apply_bonus": 3},
    "critical": {"resume_bonus": 5, "job_apply_bonus": 5},
}


LIMIT_TYPE_TO_USER_BONUS_ATTR = {
    "resume_tailoring_per_user_per_day": "bonus_resume_tailoring_max",
    "job_applications_per_user_per_day": "bonus_job_applications_max",
}


def normalize_severity(value: Any) -> str:
    severity = (value or "medium").strip().lower()
    if severity not in SEVERITY_REWARD_MAP:
        return "medium"
    return severity


def get_reward_for_severity(severity: str) -> Dict[str, int]:
    return SEVERITY_REWARD_MAP[normalize_severity(severity)]


def get_user_bonus_for_limit(user_obj: Any, limit_type: str) -> int:
    attr = LIMIT_TYPE_TO_USER_BONUS_ATTR.get(limit_type)
    if not attr:
        return 0
    return int(getattr(user_obj, attr, 0) or 0)


def effective_limit(base_limit: int, bonus_limit: int) -> int:
    return max(0, int(base_limit) + int(bonus_limit or 0))


def validate_bug_report_payload(data: Dict[str, Any]) -> Tuple[bool, str]:
    required_fields = {
        "title": 8,
        "summary": 20,
        "steps_to_reproduce": 20,
        "expected_behavior": 8,
        "actual_behavior": 8,
        "environment": 8,
    }
    for field, min_len in required_fields.items():
        value = (data.get(field) or "").strip()
        if not value:
            return False, f"'{field}' is required"
        if len(value) < min_len:
            return False, f"'{field}' must be at least {min_len} characters"
    return True, ""


def build_dedupe_key(user_id: str, title: str, steps_to_reproduce: str, actual_behavior: str) -> str:
    normalized = "|".join([
        str(user_id).strip().lower(),
        (title or "").strip().lower(),
        " ".join((steps_to_reproduce or "").split()).strip().lower(),
        " ".join((actual_behavior or "").split()).strip().lower(),
    ])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
