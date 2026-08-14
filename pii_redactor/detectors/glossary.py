"""Defined-term mining and false-positive vetoes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


PUBLIC_BODY_RE = re.compile(
    r"\b(?:Act|Ministry|Department|Authority|Bureau|Directorate|Tribunal|"
    r"Commission|Council|Government|Regulations?|Rules?)\b",
    re.I,
)


def normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n:;,.()[]{}").casefold()


class Allowlist:
    def __init__(self, values: Iterable[str] = ()) -> None:
        self.values = {normalize_term(value) for value in values if normalize_term(value)}

    @classmethod
    def from_file(cls, path: Path) -> "Allowlist":
        if not path.exists():
            return cls()
        return cls(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    def update(self, values: Iterable[str]) -> None:
        self.values.update(normalize_term(value) for value in values if normalize_term(value))

    def veto_entity(self, candidate: str) -> bool:
        normalized = normalize_term(candidate)
        if normalized in self.values:
            return True
        if re.fullmatch(r"(?:chapter|section|part|schedule|regulation)\s+[ivxlcdm\d.-]+", normalized):
            return True
        if re.fullmatch(r"(?:equity|preference) shares?", normalized):
            return True
        if PUBLIC_BODY_RE.search(candidate):
            return True
        return False


def harvest_glossary_terms(table_rows: Iterable[list[str]]) -> set[str]:
    """Harvest column-one terms from two-column definition-style rows."""

    terms: set[str] = set()
    definition_signals = re.compile(
        r"\b(?:means?|refers? to|shall mean|unless the context|being|includes?)\b", re.I
    )
    for row in table_rows:
        if len(row) < 2:
            continue
        term, description = row[0].strip(), " ".join(row[1:]).strip()
        if not term or len(term) > 100 or len(description) < 8:
            continue
        # Only semantic definition language is trusted. Length alone is unsafe:
        # a two-column contact table can contain a short name and long address.
        if definition_signals.search(description):
            terms.add(term)
    return terms
