#!/usr/bin/env python3
"""Assert that output paragraph text equals only the audited replacements."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pii_redactor.docx_io import DocxPackage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--detections", required=True, type=Path)
    args = parser.parse_args()
    source = {record.record_id: record.text for record in DocxPackage(args.source).records}
    output = {record.record_id: record.text for record in DocxPackage(args.output).records}
    replacements: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for line in args.detections.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        replacements[row["record_id"]].append(
            (int(row["start"]), int(row["end"]), row["replacement"])
        )
    mismatches: list[str] = []
    changed = 0
    for record_id, original in source.items():
        expected = original
        for start, end, replacement in sorted(
            replacements.get(record_id, []), key=lambda item: item[0], reverse=True
        ):
            expected = expected[:start] + replacement + expected[end:]
        actual = output.get(record_id)
        if actual != original:
            changed += 1
        if actual != expected:
            mismatches.append(record_id)
    extra = sorted(set(output) - set(source))
    if mismatches or extra:
        print(json.dumps({"mismatched_records": mismatches, "extra_records": extra}, indent=2))
        return 1
    print(
        f"PASS: {changed} changed paragraphs exactly equal their audited replacements; "
        f"{len(source) - changed} paragraphs are text-identical."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
