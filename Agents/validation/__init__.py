"""
Validation modules for resume tailoring quality assurance.
"""

from .semantic_validator import SemanticValidator
try:
    from .hallucination_detector import HallucinationDetector
except Exception:
    # Keep semantic validation available even if optional detector deps/imports
    # are not resolvable in this runtime context.
    HallucinationDetector = None

__all__ = ['SemanticValidator', 'HallucinationDetector']
