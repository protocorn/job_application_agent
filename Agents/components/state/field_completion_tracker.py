"""
Field Completion Tracker for Job Application Agent
Tracks which fields have been successfully filled to avoid redundant work.
"""

import time
from typing import Dict, Set, List, Any, Optional
from loguru import logger


class FieldCompletionTracker:
    """Tracks completed fields and form sections to avoid redundant processing."""
    
    def __init__(self):
        self.completed_fields: Dict[str, Dict[str, Any]] = {}
        self.attempted_fields: Dict[str, Dict[str, Any]] = {}
        self.page_sections: Dict[str, Set[str]] = {}
        self.current_page_url: str = ""
        self.session_start: float = time.time()
        
    def set_current_page(self, url: str) -> None:
        """Set the current page URL and initialize tracking for this page."""
        if url != self.current_page_url:
            logger.info(f"📍 Tracking new page: {url}")
            self.current_page_url = url
            if url not in self.page_sections:
                self.page_sections[url] = set()
    
    def mark_field_completed(self, field_id: str, field_label: str, value: str, 
                           field_type: str = "unknown") -> None:
        """Mark a field as successfully completed."""
        page_key = self._get_page_key()
        
        if page_key not in self.completed_fields:
            self.completed_fields[page_key] = {}
            
        self.completed_fields[page_key][field_id] = {
            "label": field_label,
            "value": value,
            "field_type": field_type,
            "completed_at": time.time(),
            "session_time": time.time() - self.session_start
        }
        
        logger.info(f"✅ Marked field completed: '{field_label}' = '{value}' (ID: {field_id})")
    
    def mark_field_attempted(self, field_id: str, field_label: str, 
                           success: bool = False, error: str = None) -> None:
        """Mark a field as attempted (may or may not have succeeded)."""
        page_key = self._get_page_key()
        
        if page_key not in self.attempted_fields:
            self.attempted_fields[page_key] = {}
            
        self.attempted_fields[page_key][field_id] = {
            "label": field_label,
            "success": success,
            "error": error,
            "attempted_at": time.time(),
            "session_time": time.time() - self.session_start
        }
        
        status = "✅" if success else "❌"
        logger.debug(f"{status} Attempted field: '{field_label}' (ID: {field_id}) - Success: {success}")
    
    def is_field_completed(self, field_id: str) -> bool:
        """Check if a field has been successfully completed."""
        page_key = self._get_page_key()
        return (page_key in self.completed_fields and 
                field_id in self.completed_fields[page_key])
    
    def is_field_attempted(self, field_id: str) -> bool:
        """Check if a field has been attempted (regardless of success)."""
        page_key = self._get_page_key()
        return (page_key in self.attempted_fields and 
                field_id in self.attempted_fields[page_key])
    
    def get_completed_field_count(self) -> int:
        """Get count of completed fields on current page."""
        page_key = self._get_page_key()
        return len(self.completed_fields.get(page_key, {}))
    
    def get_attempted_field_count(self) -> int:
        """Get count of attempted fields on current page."""
        page_key = self._get_page_key()
        return len(self.attempted_fields.get(page_key, {}))
    
    def get_incomplete_fields(self, all_field_ids: List[str]) -> List[str]:
        """Get list of field IDs that haven't been completed yet."""
        return [field_id for field_id in all_field_ids 
                if not self.is_field_completed(field_id)]
    
    def get_completion_summary(self) -> Dict[str, Any]:
        """Get summary of completion status for current page."""
        page_key = self._get_page_key()
        completed = self.completed_fields.get(page_key, {})
        attempted = self.attempted_fields.get(page_key, {})
        
        successful_attempts = sum(1 for attempt in attempted.values() if attempt["success"])
        failed_attempts = sum(1 for attempt in attempted.values() if not attempt["success"])
        
        return {
            "page_url": self.current_page_url,
            "completed_fields": len(completed),
            "successful_attempts": successful_attempts,
            "failed_attempts": failed_attempts,
            "total_attempts": len(attempted),
            "completed_field_details": completed,
            "session_duration": time.time() - self.session_start
        }
    
    def log_progress(self) -> None:
        """Log current progress summary."""
        summary = self.get_completion_summary()
        logger.info(f"📊 Form Progress: {summary['completed_fields']} completed, "
                   f"{summary['successful_attempts']} successful attempts, "
                   f"{summary['failed_attempts']} failed attempts")
        
        if summary['completed_fields'] > 0:
            logger.info("✅ Completed fields:")
            for field_id, details in summary['completed_field_details'].items():
                logger.info(f"   • {details['label']}: {details['value']}")
    
    def should_skip_field(self, field_id: str, field_label: str) -> bool:
        """Determine if a field should be skipped because it's already completed."""
        if self.is_field_completed(field_id):
            completed_details = self.completed_fields[self._get_page_key()][field_id]
            logger.info(f"⏭️ Skipping already completed field: '{field_label}' = '{completed_details['value']}'")
            return True
        return False
    
    def mark_section_processed(self, section_name: str) -> None:
        """Mark a form section as processed."""
        page_key = self._get_page_key()
        if page_key not in self.page_sections:
            self.page_sections[page_key] = set()
        
        self.page_sections[page_key].add(section_name)
        logger.info(f"📝 Marked section as processed: {section_name}")
    
    def is_section_processed(self, section_name: str) -> bool:
        """Check if a section has been processed."""
        page_key = self._get_page_key()
        return (page_key in self.page_sections and 
                section_name in self.page_sections[page_key])
    
    def reset_page_tracking(self) -> None:
        """Reset tracking for current page (useful when page structure changes)."""
        page_key = self._get_page_key()
        if page_key in self.completed_fields:
            del self.completed_fields[page_key]
        if page_key in self.attempted_fields:
            del self.attempted_fields[page_key]
        if page_key in self.page_sections:
            del self.page_sections[page_key]
        logger.info(f"🔄 Reset tracking for page: {self.current_page_url}")
    
    def _get_page_key(self) -> str:
        """Get a consistent key for the current page."""
        # Remove query parameters for more stable page identification
        if "?" in self.current_page_url:
            base_url = self.current_page_url.split("?")[0]
        else:
            base_url = self.current_page_url
        return base_url
    
    def export_completion_data(self) -> Dict[str, Any]:
        """Export all completion data for debugging or persistence."""
        return {
            "session_start": self.session_start,
            "current_page": self.current_page_url,
            "completed_fields": self.completed_fields,
            "attempted_fields": self.attempted_fields,
            "page_sections": {k: list(v) for k, v in self.page_sections.items()},
            "session_duration": time.time() - self.session_start
        }
