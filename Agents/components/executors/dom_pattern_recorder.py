"""
DomPatternRecorder — learns the structural relationship between form labels and
their associated input fields on a given website.

This is DIFFERENT from field_label_patterns (which maps label text → profile field).
DomPatternRecorder maps:

    (site_domain, normalized_label_text) → DOM relationship type + selectors

So next time we visit the same site, we can locate the input element directly
without any Gemini call, using the already-proven DOM navigation strategy.

Relationship types recorded:
  label_for         — <label for="X"> → <input id="X">
  aria_labelledby   — <input aria-labelledby="Y"> → found by #Y text
  aria_label        — field has aria-label="label_text" directly
  ancestor_walk     — field inside closest container that has a visible label
  parent_label      — field is a direct child of <label>
  placeholder       — field identified by placeholder text
  custom_class      — site-specific class pattern (e.g. Ashby, Greenhouse)

Storage: JSON file at ~/.launchway/dom_patterns.json
Format:
{
  "greenhouse.io": {
    "first name": {
      "relationship": "label_for",
      "label_selector": "label[for]",
      "field_selector_template": "input#{label_for_value}",
      "field_type": "text",
      "success_count": 4,
      "failure_count": 0,
      "last_seen": "2026-04-20T12:00:00"
    },
    ...
  }
}
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from loguru import logger


_STORAGE_PATH = Path.home() / ".launchway" / "dom_patterns.json"
_RELATIONSHIP_TYPES = {
    "label_for",
    "aria_labelledby",
    "aria_label",
    "ancestor_walk",
    "parent_label",
    "placeholder",
    "custom_class",
}


class DomPatternRecorder:
    """Records DOM structural patterns between form labels and input fields.

    Usage:
        recorder = DomPatternRecorder()
        recorder.record(
            site_domain="greenhouse.io",
            label_text="First Name",
            relationship="label_for",
            field_type="text",
            extra={"label_for_value": "first_name_field"}
        )
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self._path = storage_path or _STORAGE_PATH
        self._data: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                self._data = json.loads(raw)
                total = sum(len(v) for v in self._data.values())
                logger.debug(f"DomPatternRecorder: loaded {total} patterns from {self._path}")
        except Exception as e:
            logger.warning(f"DomPatternRecorder: could not load patterns ({e}), starting fresh")
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            self._dirty = False
        except Exception as e:
            logger.warning(f"DomPatternRecorder: could not save patterns: {e}")

    @staticmethod
    def _normalize_label(label: str) -> str:
        """Lowercase, strip punctuation/asterisks, collapse whitespace."""
        s = label.lower()
        s = re.sub(r"[^a-z0-9\s]", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    @staticmethod
    def _normalize_domain(url_or_domain: str) -> str:
        """Extract bare domain from URL or domain string."""
        s = url_or_domain.lower()
        # Remove protocol
        s = re.sub(r"^https?://", "", s)
        # Remove path
        s = s.split("/")[0]
        # Remove www.
        s = re.sub(r"^www\.", "", s)
        return s

    def record(
        self,
        site_domain: str,
        label_text: str,
        relationship: str,
        field_type: str = "text",
        success: bool = True,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Record or update a DOM structural pattern.

        Args:
            site_domain:  URL or bare domain (e.g. "greenhouse.io" or "https://boards.greenhouse.io/company")
            label_text:   Visible label text (will be normalised)
            relationship: One of _RELATIONSHIP_TYPES
            field_type:   HTML input type or "select", "textarea", etc.
            success:      Whether the pattern successfully located the field
            extra:        Optional dict with additional selector hints, e.g.
                          {"label_for_value": "input_id", "css_selector": "input#foo"}

        Returns:
            True if recorded, False on error.
        """
        if relationship not in _RELATIONSHIP_TYPES:
            logger.debug(f"DomPatternRecorder: unknown relationship '{relationship}', skipping")
            return False

        domain = self._normalize_domain(site_domain)
        norm_label = self._normalize_label(label_text)

        if not domain or not norm_label:
            return False

        site_patterns = self._data.setdefault(domain, {})
        entry = site_patterns.get(norm_label)

        if entry is None:
            entry = {
                "relationship": relationship,
                "field_type": field_type,
                "success_count": 0,
                "failure_count": 0,
                "last_seen": None,
                "extra": extra or {},
            }
            site_patterns[norm_label] = entry

        if success:
            entry["success_count"] += 1
            entry["relationship"] = relationship
            entry["field_type"] = field_type
            if extra:
                entry["extra"].update(extra)
        else:
            entry["failure_count"] += 1

        entry["last_seen"] = datetime.utcnow().isoformat()
        self._dirty = True

        total_s = entry["success_count"]
        total_f = entry["failure_count"]
        logger.info(
            f"DomPatternRecorder: [{domain}] '{label_text}' → {relationship} "
            f"(success={total_s}, failure={total_f})"
        )

        self._save()
        return True

    def get(self, site_domain: str, label_text: str) -> Optional[Dict[str, Any]]:
        """Retrieve a DOM pattern entry (or None if not found / not reliable)."""
        domain = self._normalize_domain(site_domain)
        norm_label = self._normalize_label(label_text)
        entry = self._data.get(domain, {}).get(norm_label)
        if entry is None:
            return None
        # Only return patterns that have worked more than they've failed
        if entry["success_count"] < 1:
            return None
        return entry

    def get_all_for_site(self, site_domain: str) -> Dict[str, Any]:
        """Return all recorded patterns for a site domain."""
        domain = self._normalize_domain(site_domain)
        return dict(self._data.get(domain, {}))

    def stats(self) -> Dict[str, Any]:
        """Summary statistics."""
        sites = len(self._data)
        total = sum(len(v) for v in self._data.values())
        relationships: Dict[str, int] = {}
        for site_data in self._data.values():
            for entry in site_data.values():
                r = entry.get("relationship", "unknown")
                relationships[r] = relationships.get(r, 0) + 1
        return {
            "sites": sites,
            "total_patterns": total,
            "by_relationship": relationships,
            "storage_path": str(self._path),
        }
