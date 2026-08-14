"""Context-gated dates of birth."""

from __future__ import annotations

import re

from ..config import TYPE_PRIORITIES
from ..spans import Span
from .base import DetectionContext, Detector


MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
DATE_RE = re.compile(
    rf"(?<!\d)(?:\d{{1,2}}[./-]\d{{1,2}}[./-](?:19|20)\d{{2}}|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[,]?\s+(?:19|20)\d{{2}}|"
    rf"(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+(?:19|20)\d{{2}})(?!\d)",
    re.I,
)
DOB_CONTEXT_RE = re.compile(
    r"(?:date\s+of\s+birth|d\.?o\.?b\.?|born\s+on|birth\s+date)\s*[:.-]?\s*$", re.I
)


class DateOfBirthDetector(Detector):
    pii_type = "DATE_OF_BIRTH"
    priority = TYPE_PRIORITIES[pii_type]

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        results: list[Span] = []
        for match in DATE_RE.finditer(text):
            before = text[max(0, match.start() - 48) : match.start()]
            header = " ".join((context.metadata.get("table_headers", []) if context else []))
            column = str(context.metadata.get("column_header", "") if context else "")
            if DOB_CONTEXT_RE.search(before) or re.search(
                r"date\s+of\s+birth|d\.?o\.?b\.?", f"{header} {column}", re.I
            ):
                results.append(
                    Span(
                        match.start(), match.end(), self.pii_type, match.group(0),
                        "date_context", 0.98, self.priority,
                    )
                )
        return results
