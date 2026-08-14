"""Span primitives and deterministic overlap arbitration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    pii_type: str
    text: str
    detector: str
    confidence: float
    priority: int = 50
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid span [{self.start}, {self.end})")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def with_text_from(self, source: str) -> "Span":
        return replace(self, text=source[self.start : self.end])

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "type": self.pii_type,
            "text": self.text,
            "detector": self.detector,
            "confidence": round(self.confidence, 4),
            "priority": self.priority,
            "metadata": self.metadata,
        }


def resolve_spans(candidates: Iterable[Span]) -> list[Span]:
    """Choose non-overlapping spans by priority, confidence, length, then position.

    Higher priority values win. Stable tie-breaking makes audit output reproducible.
    Exact duplicate spans are collapsed before arbitration.
    """

    unique: dict[tuple[int, int, str], Span] = {}
    for candidate in candidates:
        key = (candidate.start, candidate.end, candidate.pii_type)
        incumbent = unique.get(key)
        if incumbent is None or (candidate.priority, candidate.confidence) > (
            incumbent.priority,
            incumbent.confidence,
        ):
            unique[key] = candidate

    ranked = sorted(
        unique.values(),
        key=lambda span: (
            -span.priority,
            -span.confidence,
            -span.length,
            span.start,
            span.pii_type,
        ),
    )
    selected: list[Span] = []
    for candidate in ranked:
        if not any(candidate.overlaps(chosen) for chosen in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda span: (span.start, span.end))
