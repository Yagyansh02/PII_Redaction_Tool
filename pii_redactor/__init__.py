"""Deterministic, format-preserving PII redaction for Word documents."""

from .pipeline import RedactionPipeline, RedactionResult

__all__ = ["RedactionPipeline", "RedactionResult"]
__version__ = "1.0.0"
