"""
Comprehensive exception hierarchy for field interaction failures.
Enables precise error handling and intelligent retry strategies.
"""
from typing import Optional, Dict, Any
from enum import Enum


class FieldInteractionStrategy(Enum):
    """Strategies attempted for field interaction."""
    STANDARD_CLICK = "standard_click"
    GREENHOUSE_DROPDOWN = "greenhouse_dropdown"
    WORKDAY_DROPDOWN = "workday_dropdown"
    LEVER_DROPDOWN = "lever_dropdown"
    ASHBY_BUTTON_GROUP = "ashby_button_group"
    KEYBOARD_NAVIGATION = "keyboard_navigation"
    JAVASCRIPT_INJECTION = "javascript_injection"
    PRESS_ENTER = "press_enter"
    TYPE_AND_SELECT = "type_and_select"


class FieldInteractionError(Exception):
    """Base exception for all field interaction failures."""

    def __init__(
        self,
        field_label: str,
        field_type: str,
        reason: str,
        failed_strategy: Optional[FieldInteractionStrategy] = None,
        suggested_strategy: Optional[FieldInteractionStrategy] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        self.field_label = field_label
        self.field_type = field_type
        self.reason = reason
        self.failed_strategy = failed_strategy
        self.suggested_strategy = suggested_strategy
        self.context = context or {}

        super().__init__(
            f"Field '{field_label}' ({field_type}): {reason}"
            f"{f' | Failed: {failed_strategy.value}' if failed_strategy else ''}"
            f"{f' | Try: {suggested_strategy.value}' if suggested_strategy else ''}"
        )


class DropdownInteractionError(FieldInteractionError):
    """Specific error for dropdown field failures."""

    def __init__(self, field_label: str, value: str, dropdown_type: str, **kwargs):
        self.value = value
        self.dropdown_type = dropdown_type
        super().__init__(
            field_label=field_label,
            field_type=dropdown_type,
            **kwargs
        )


class TimeoutExceededError(FieldInteractionError):
    """Raised when a field interaction times out."""

    def __init__(self, field_label: str, timeout_ms: int, strategy: FieldInteractionStrategy, **kwargs):
        self.timeout_ms = timeout_ms
        # Extract field_type from kwargs to avoid duplicate keyword argument
        field_type = kwargs.pop('field_type', 'unknown')
        super().__init__(
            field_label=field_label,
            field_type=field_type,
            reason=f"Timeout after {timeout_ms}ms",
            failed_strategy=strategy,
            **kwargs
        )


class ElementStaleError(FieldInteractionError):
    """Raised when element reference becomes stale (detached from DOM)."""

    def __init__(self, field_label: str, stable_id: str, **kwargs):
        self.stable_id = stable_id
        # Extract field_type from kwargs to avoid duplicate keyword argument
        field_type = kwargs.pop('field_type', 'unknown')
        super().__init__(
            field_label=field_label,
            field_type=field_type,
            reason=f"Element detached from DOM (stable_id: {stable_id})",
            **kwargs
        )


class VerificationFailedError(FieldInteractionError):
    """Raised when field value verification fails after filling."""

    def __init__(self, field_label: str, expected: str, actual: str, **kwargs):
        self.expected = expected
        self.actual = actual
        # Extract field_type from kwargs to avoid duplicate keyword argument
        field_type = kwargs.pop('field_type', 'unknown')
        super().__init__(
            field_label=field_label,
            field_type=field_type,
            reason=f"Verification failed: expected '{expected}', got '{actual}'",
            **kwargs
        )


class RequiresHumanInputError(FieldInteractionError):
    """Raised when field requires human intervention (all strategies exhausted)."""

    def __init__(
        self,
        field_label: str,
        field_type: str,
        attempted_strategies: list[FieldInteractionStrategy],
        **kwargs
    ):
        self.attempted_strategies = attempted_strategies
        strategies_str = ", ".join([s.value for s in attempted_strategies])
        super().__init__(
            field_label=field_label,
            field_type=field_type,
            reason=f"All strategies exhausted: {strategies_str}",
            **kwargs
        )


class ATSNotSupportedError(FieldInteractionError):
    """Raised when ATS system is not recognized or supported."""

    def __init__(self, ats_name: str, field_label: str, **kwargs):
        self.ats_name = ats_name
        # Extract field_type from kwargs to avoid duplicate keyword argument
        field_type = kwargs.pop('field_type', 'unknown')
        super().__init__(
            field_label=field_label,
            field_type=field_type,
            reason=f"ATS '{ats_name}' not supported for this field type",
            **kwargs
        )


class DynamicContentError(FieldInteractionError):
    """Raised when dynamic content loading causes issues."""

    def __init__(self, field_label: str, reason: str, **kwargs):
        # Extract field_type from kwargs to avoid duplicate keyword argument
        field_type = kwargs.pop('field_type', 'unknown')
        super().__init__(
            field_label=field_label,
            field_type=field_type,
            reason=f"Dynamic content issue: {reason}",
            **kwargs
        )
