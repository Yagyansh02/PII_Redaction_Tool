#!/usr/bin/env python3
"""Build a reproducible stratified corpus for independent annotation."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pii_redactor.docx_io import DocxPackage  # noqa: E402


STRUCTURED_SWEEP_RE = re.compile(
    r"@|https?://|www\.|\b(?:e-?mail|telephone|mobile|phone|tel\.?|DIN|PAN|CIN|IFSC|GSTIN|"
    r"passport|account\s+no|date\s+of\s+birth|d\.?o\.?b\.?)\b",
    re.I,
)


def build_sample(input_path: Path, output_path: Path, seed: int = 42, prose_size: int = 150) -> dict[str, int]:
    package = DocxPackage(input_path)
    body = [record for record in package.records if record.part_name == "word/document.xml" and record.text.strip()]
    headings: dict[str, int] = {}
    for index, record in enumerate(body):
        normalized = re.sub(r"\s+", " ", record.text).strip().upper()
        for heading in (
            "DEFINITIONS AND ABBREVIATIONS",
            "CERTAIN CONVENTIONS, USE OF FINANCIAL INFORMATION AND MARKET DATA AND CURRENCY OF PRESENTATION",
            "GENERAL INFORMATION",
            "CAPITAL STRUCTURE",
        ):
            if normalized == heading and heading not in headings:
                headings[heading] = index

    dense_indexes = set(range(min(180, len(body))))
    for start_name, end_name in (
        (
            "DEFINITIONS AND ABBREVIATIONS",
            "CERTAIN CONVENTIONS, USE OF FINANCIAL INFORMATION AND MARKET DATA AND CURRENCY OF PRESENTATION",
        ),
        ("GENERAL INFORMATION", "CAPITAL STRUCTURE"),
    ):
        start = headings.get(start_name)
        end = headings.get(end_name)
        if start is not None:
            dense_indexes.update(range(start, min(end if end is not None else start + 500, len(body))))

    structured_indexes = {
        index for index, record in enumerate(body) if STRUCTURED_SWEEP_RE.search(record.text)
    }
    remainder = [
        index for index in range(len(body))
        if index not in dense_indexes and index not in structured_indexes
    ]
    rng = random.Random(seed)
    prose_indexes = set(rng.sample(remainder, min(prose_size, len(remainder))))

    selected: list[tuple[int, str]] = []
    for index in sorted(dense_indexes | prose_indexes | structured_indexes):
        strata = []
        if index in dense_indexes:
            strata.append("A_dense")
        if index in prose_indexes:
            strata.append("B_prose")
        if index in structured_indexes:
            strata.append("C_structured")
        selected.append((index, "+".join(strata)))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for index, stratum in selected:
            record = body[index]
            handle.write(
                json.dumps(
                    {
                        "record_id": record.record_id,
                        "part": record.part_name,
                        "document_index": index,
                        "stratum": stratum,
                        "text": record.text,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return {
        "A_dense": len(dense_indexes),
        "B_prose": len(prose_indexes),
        "C_structured": len(structured_indexes),
        "unique_records": len(selected),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation/gold/rhp_sample.jsonl"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prose-size", type=int, default=150)
    args = parser.parse_args()
    counts = build_sample(args.input, args.output, args.seed, args.prose_size)
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
