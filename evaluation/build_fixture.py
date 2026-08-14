#!/usr/bin/env python3
"""Generate the synthetic DOCX and independently specified ground truth."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from docx import Document  # noqa: E402

from pii_redactor.docx_io import DocxPackage  # noqa: E402


LINES = [
    ("Employee: Priya Sharma", [("Priya Sharma", "PERSON")]),
    ("Employer: Orchid Systems Private Limited", [("Orchid Systems Private Limited", "COMPANY")]),
    ("E-mail: priya.sharma@orchid.example.org", [("priya.sharma@orchid.example.org", "EMAIL")]),
    ("Telephone: +91 98765 43210", [("+91 98765 43210", "PHONE")]),
    ("Mailing address: Flat 8, Lotus Heights, Baner Road, Pune – 411 045, Maharashtra, India", [("Flat 8, Lotus Heights, Baner Road, Pune – 411 045, Maharashtra, India", "POSTAL_ADDRESS")]),
    ("Date of birth: 14/09/1987", [("14/09/1987", "DATE_OF_BIRTH")]),
    ("US SSN: 123-45-6789", [("123-45-6789", "SSN")]),
    ("Visa card: 4111 1111 1111 1111", [("4111 1111 1111 1111", "CREDIT_CARD")]),
    ("Client IPv4: 192.168.10.24", [("192.168.10.24", "IP_ADDRESS")]),
    ("Client IPv6: 2001:db8::8a2e:370:7334", [("2001:db8::8a2e:370:7334", "IP_ADDRESS")]),
    ("PAN: ABCDE1234F", [("ABCDE1234F", "PAN")]),
    ("Order 6350960932; Ticket 1094535838831; board meeting 14/09/2024.", []),
    ("Invalid SSN 000-12-1234; invalid IP 999.10.10.10; non-Luhn card 4111 1111 1111 1112.", []),
]


def build_fixture(docx_path: Path, gold_path: Path, corpus_path: Path) -> None:
    document = Document()
    for text, _annotations in LINES:
        document.add_paragraph(text)
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(docx_path)

    package = DocxPackage(docx_path)
    records = [record for record in package.records if record.part_name == "word/document.xml" and record.text.strip()]
    if len(records) != len(LINES):
        raise RuntimeError(f"fixture record mismatch: {len(records)} != {len(LINES)}")
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    with gold_path.open("w", encoding="utf-8") as gold, corpus_path.open("w", encoding="utf-8") as corpus:
        for record, (expected_text, annotations) in zip(records, LINES, strict=True):
            if record.text != expected_text:
                raise RuntimeError(f"fixture text mismatch: {record.text!r}")
            corpus.write(json.dumps({"record_id": record.record_id, "stratum": "synthetic", "text": record.text}) + "\n")
            search_from = 0
            for value, pii_type in annotations:
                start = record.text.index(value, search_from)
                end = start + len(value)
                gold.write(json.dumps({
                    "record_id": record.record_id,
                    "part": record.part_name,
                    "start": start,
                    "end": end,
                    "type": pii_type,
                    "text": value,
                    "stratum": "synthetic",
                }) + "\n")
                search_from = end


def main() -> int:
    fixture_dir = PROJECT_ROOT / "evaluation" / "fixtures"
    gold_dir = PROJECT_ROOT / "evaluation" / "gold"
    build_fixture(
        fixture_dir / "synthetic_pii.docx",
        gold_dir / "synthetic_gold.jsonl",
        gold_dir / "synthetic_corpus.jsonl",
    )
    print("Synthetic fixture and gold data generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
