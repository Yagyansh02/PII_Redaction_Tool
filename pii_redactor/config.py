"""Runtime policy for the redaction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


ALL_TYPES = frozenset(
    {
        "PERSON",
        "EMAIL",
        "PHONE",
        "COMPANY",
        "POSTAL_ADDRESS",
        "SSN",
        "CREDIT_CARD",
        "DATE_OF_BIRTH",
        "IP_ADDRESS",
        "URL",
        "PAN",
        "AADHAAR",
        "DIN",
        "CIN",
        "SEBI_REG_NO",
        "IFSC",
        "BANK_ACCOUNT",
        "GSTIN",
        "PASSPORT",
    }
)

TYPE_PRIORITIES = {
    "EMAIL": 100,
    "CREDIT_CARD": 100,
    "SSN": 100,
    "IP_ADDRESS": 100,
    "PHONE": 98,
    "AADHAAR": 98,
    "PAN": 98,
    "DIN": 96,
    "GSTIN": 96,
    "IFSC": 96,
    "BANK_ACCOUNT": 96,
    "PASSPORT": 96,
    "CIN": 94,
    "SEBI_REG_NO": 97,
    "URL": 92,
    "DATE_OF_BIRTH": 90,
    "POSTAL_ADDRESS": 88,
    "PERSON": 80,
    "COMPANY": 78,
}


@dataclass(slots=True)
class RedactionConfig:
    enabled_types: set[str] = field(default_factory=lambda: set(ALL_TYPES))
    company_scope: str = "all"
    seed: int = 42
    use_ner: bool = True
    image_policy: str = "sensitive"
    force: bool = False
    debug_block: str | None = None
    resources_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent / "resources"
    )

    def __post_init__(self) -> None:
        unknown = self.enabled_types - ALL_TYPES
        if unknown:
            raise ValueError(f"unknown PII types: {', '.join(sorted(unknown))}")
        if self.company_scope not in {"parties", "all", "none"}:
            raise ValueError("company_scope must be one of: parties, all, none")
        if self.image_policy not in {"sensitive", "all", "none"}:
            raise ValueError("image_policy must be one of: sensitive, all, none")
        if self.company_scope == "none":
            self.enabled_types.discard("COMPANY")

    def enabled(self, pii_type: str) -> bool:
        return pii_type in self.enabled_types
