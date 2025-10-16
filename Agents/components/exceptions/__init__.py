"""
Exception hierarchy for field interaction failures.
"""
from .field_exceptions import (
    FieldInteractionStrategy,
    FieldInteractionError,
    DropdownInteractionError,
    TimeoutExceededError,
    ElementStaleError,
    VerificationFailedError,
    RequiresHumanInputError,
    ATSNotSupportedError,
    DynamicContentError
)

__all__ = [
    'FieldInteractionStrategy',
    'FieldInteractionError',
    'DropdownInteractionError',
    'TimeoutExceededError',
    'ElementStaleError',
    'VerificationFailedError',
    'RequiresHumanInputError',
    'ATSNotSupportedError',
    'DynamicContentError'
]
