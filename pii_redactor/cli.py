"""Command-line interface for the DOCX redactor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ALL_TYPES, RedactionConfig
from .pipeline import RedactionPipeline, RedactionResult


def _parse_types(value: str) -> set[str]:
    if value.strip().casefold() == "all":
        return set(ALL_TYPES)
    requested = {item.strip().upper() for item in value.split(",") if item.strip()}
    unknown = requested - ALL_TYPES
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown types: {', '.join(sorted(unknown))}")
    if not requested:
        raise argparse.ArgumentTypeError("at least one PII type is required")
    return requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pii_redactor",
        description="Replace PII in a DOCX with deterministic, realistic fake alternatives.",
    )
    parser.add_argument("--input", required=True, type=Path, help="source .docx")
    parser.add_argument("--output", type=Path, help="redacted .docx (required unless --dry-run)")
    parser.add_argument("--map", dest="map_path", type=Path, help="audit mapping JSON")
    parser.add_argument("--detections", type=Path, help="detection audit JSONL")
    parser.add_argument("--types", type=_parse_types, default=set(ALL_TYPES), help="all or comma-separated PII types")
    parser.add_argument("--company-scope", choices=("parties", "all", "none"), default="all")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="detect and summarize without writing")
    parser.add_argument("--no-ner", action="store_true", help="disable the optional spaCy layer")
    parser.add_argument(
        "--image-policy",
        choices=("sensitive", "all", "none"),
        default="sensitive",
        help="OCR/QR-redact sensitive images, redact all images, or leave images unchanged",
    )
    parser.add_argument("--image-audit", type=Path, help="image-redaction audit JSON")
    parser.add_argument("--force", action="store_true", help="process a document already marked as redacted")
    parser.add_argument(
        "--debug-block",
        help="print candidate accept/reject traces for a record-id or text substring",
    )
    return parser


def _print_summary(result: RedactionResult) -> None:
    if result.already_redacted:
        print("Document is already marked as redacted; no changes made.")
        return
    print(f"Detected {result.total} PII spans")
    for pii_type, count in result.counts.items():
        print(f"  {pii_type:<18} {count:>6}")
    print(f"Redacted images: {len(result.image_redactions)}")
    print(f"Skipped tab/line-break spans: {result.skipped_hard_boundaries}")
    print(f"Gazetteer entries: {result.gazetteer_entries}")
    print(f"Glossary allowlist terms: {result.glossary_terms}")
    print(f"spaCy model: {result.ner_model}")
    if result.output_path:
        print(f"Output: {result.output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is used")
    if args.input.suffix.casefold() != ".docx":
        parser.error("--input must be a .docx file")
    config = RedactionConfig(
        enabled_types=set(args.types),
        company_scope=args.company_scope,
        seed=args.seed,
        use_ner=not args.no_ner,
        image_policy=args.image_policy,
        force=args.force,
        debug_block=args.debug_block,
    )
    try:
        result = RedactionPipeline(config).run(
            args.input,
            args.output,
            args.map_path,
            args.detections,
            args.dry_run,
            args.image_audit,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    _print_summary(result)
    for trace in result.debug_traces:
        print(f"DEBUG {trace}")
    return 0
