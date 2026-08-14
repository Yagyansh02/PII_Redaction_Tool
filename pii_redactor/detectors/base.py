"""The extension point for all detectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..spans import Span


@dataclass(slots=True)
class DetectionContext:
    record_id: str = ""
    part_name: str = ""
    before: str = ""
    after: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Detector(ABC):
    """Subclass this and register it in the pipeline to add a PII type."""

    pii_type = ""
    priority = 50

    @abstractmethod
    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        raise NotImplementedError
