"""End-to-end extraction, detection, substitution and audit orchestration."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import RedactionConfig
from .detectors import (
    AddressDetector,
    DateOfBirthDetector,
    GazetteerBuilder,
    GazetteerDetector,
    SpacyNerDetector,
    StructuredDetector,
)
from .detectors.base import DetectionContext
from .detectors.glossary import Allowlist, harvest_glossary_terms
from .docx_io import DocxPackage, TextRecord
from .image_redactor import ImageRedaction, ImageRedactor
from .spans import Span, resolve_spans
from .surrogates import SurrogateStore


@dataclass(slots=True)
class Detection:
    record_id: str
    part_name: str
    span: Span
    replacement: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "part": self.part_name,
            **self.span.as_dict(),
            "replacement": self.replacement,
        }


@dataclass(slots=True)
class RedactionResult:
    input_path: Path
    output_path: Path | None
    detections: list[Detection] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    ner_model: str = "disabled"
    glossary_terms: int = 0
    gazetteer_entries: int = 0
    image_redactions: list[ImageRedaction] = field(default_factory=list)
    skipped_hard_boundaries: int = 0
    already_redacted: bool = False
    debug_traces: list[dict[str, object]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.detections)


class RedactionPipeline:
    def __init__(self, config: RedactionConfig | None = None) -> None:
        self.config = config or RedactionConfig()

    def run(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        map_path: str | Path | None = None,
        detections_path: str | Path | None = None,
        dry_run: bool = False,
        image_audit_path: str | Path | None = None,
    ) -> RedactionResult:
        source = Path(input_path)
        package = DocxPackage(source)
        destination = Path(output_path) if output_path else None
        if package.is_redacted() and not self.config.force:
            return RedactionResult(source, destination, already_redacted=True)
        if not dry_run and destination is None:
            raise ValueError("output_path is required unless dry_run=True")

        allowlist = Allowlist.from_file(self.config.resources_dir / "allowlist.txt")
        glossary_terms = harvest_glossary_terms(row for table in package.tables for row in table.rows)
        allowlist.update(glossary_terms)
        lowercase_vocabulary = {
            match.group(0).casefold()
            for record in package.records
            for match in re.finditer(r"(?<![@.\w])[a-z][a-z'’-]*(?![@.\w])", record.text)
            if match.group(0).islower()
        }
        gazetteer = GazetteerBuilder(
            allowlist,
            self.config.resources_dir / "org_suffixes.txt",
            self.config.company_scope,
            lowercase_vocabulary,
        ).build(package.records, package.tables)

        structured = StructuredDetector(self.config.enabled_types)
        dates = DateOfBirthDetector()
        addresses = AddressDetector()
        company_stems = {entry.canonical for entry in gazetteer.by_type("COMPANY")}
        ner = (
            SpacyNerDetector(allowlist, company_stems, lowercase_vocabulary)
            if self.config.use_ner else None
        )

        # Two-pass entity flow: NER discoveries seed the document gazetteer,
        # then exact/case/surname variants propagate consistently everywhere.
        ner_by_record: dict[str, list[Span]] = {}
        debug_traces: list[dict[str, object]] = []
        if ner is not None and ner.available:
            for record in package.records:
                context = DetectionContext(record.record_id, record.part_name, metadata=record.metadata)
                proposed_ner_spans = ner.detect(record.text, context)
                if self._debug_matches(record):
                    debug_traces.extend(
                        {"record_id": record.record_id, "stage": "ner_filter", **trace}
                        for trace in ner.last_trace
                    )
                ner_spans = [
                    span for span in proposed_ner_spans
                    if span.pii_type != "COMPANY"
                    or self.config.company_scope == "all"
                    or bool(span.metadata.get("known_party"))
                ]
                ner_by_record[record.record_id] = ner_spans
            person_seeds = {
                span.text for spans in ner_by_record.values() for span in spans
                if span.pii_type == "PERSON"
            }
            company_seeds = {
                span.text for spans in ner_by_record.values() for span in spans
                if span.pii_type == "COMPANY"
            }
            # Add longest forms first, then attach shortened NER forms to the
            # same canonical identity when their tokens are a subset.
            for value in sorted(person_seeds, key=lambda item: (-len(item.split()), item.casefold())):
                tokens = value.split()
                normalized_tokens = {token.casefold().strip(".") for token in tokens}
                alias_target = next(
                    (
                        entry for entry in gazetteer.by_type("PERSON")
                        if entry.canonical.split()[0].casefold().strip(".") == tokens[0].casefold().strip(".")
                        and normalized_tokens
                        <= {token.casefold().strip(".") for token in entry.canonical.split()}
                    ),
                    None,
                )
                if alias_target is not None:
                    alias_target.variants.add(value)
                else:
                    gazetteer.add(value, "PERSON", "spacy_seed", 0.75)
            if self.config.company_scope == "all":
                for value in sorted(company_seeds, key=lambda item: (-len(item), item.casefold())):
                    gazetteer.add(value, "COMPANY", "spacy_seed", 0.76)

                # Recover distinctive brand tokens embedded in a larger NER
                # ORG, e.g. OpenAI from "Azure OpenAI Service" and Nuance
                # from "Nuance Healthcare". The party-only prospectus policy
                # deliberately does not enable this broader propagation.
                standalone_tokens = {
                    match.group(0)
                    for record in package.records
                    for match in re.finditer(r"(?<![\w])(?:[A-Z][A-Za-z0-9]*)(?![\w])", record.text)
                }
                generic_tails = {"healthcare", "service", "services", "platform", "copilot"}
                company_alias_sources = company_seeds | {
                    entry.canonical for entry in gazetteer.by_type("COMPANY")
                }
                for value in company_alias_sources:
                    value_tokens = re.findall(r"[A-Z][A-Za-z0-9]*", value)
                    for token in value_tokens:
                        internal_capital = any(char.isupper() for char in token[1:])
                        first_of_generic = (
                            token == value_tokens[0]
                            and len(value_tokens) > 1
                            and value_tokens[-1].casefold() in generic_tails
                        )
                        if (
                            token in standalone_tokens
                            and len(token) >= 5
                            and token.casefold() not in lowercase_vocabulary
                            and (internal_capital or first_of_generic)
                        ):
                            gazetteer.add(token, "COMPANY", "spacy_brand_alias", 0.79)
            gazetteer.enrich_camel_people(package.records)
            gazetteer.expand_variants(lowercase_vocabulary)
        gazetteer_detector = GazetteerDetector(gazetteer, allowlist)

        candidates_by_record: dict[str, list[Span]] = defaultdict(list)
        records_by_id = {record.record_id: record for record in package.records}
        for index, record in enumerate(package.records):
            previous = package.records[index - 1].text if index else ""
            following = package.records[index + 1].text if index + 1 < len(package.records) else ""
            context = DetectionContext(
                record.record_id,
                record.part_name,
                previous[-200:],
                following[:200],
                record.metadata,
            )
            candidates_by_record[record.record_id].extend(structured.detect(record.text, context))
            if self.config.enabled("DATE_OF_BIRTH"):
                candidates_by_record[record.record_id].extend(dates.detect(record.text, context))
            if self.config.enabled("POSTAL_ADDRESS"):
                candidates_by_record[record.record_id].extend(addresses.detect(record.text, context))
            candidates_by_record[record.record_id].extend(gazetteer_detector.detect(record.text, context))
            candidates_by_record[record.record_id].extend(ner_by_record.get(record.record_id, []))

        if self.config.enabled("POSTAL_ADDRESS"):
            protected_addresses = self._protected_address_records(package.records)
            for record_id in protected_addresses:
                candidates_by_record[record_id] = [
                    span for span in candidates_by_record.get(record_id, [])
                    if span.pii_type != "POSTAL_ADDRESS"
                ]
            self._add_block_addresses(package, addresses, candidates_by_record, protected_addresses)

        detections: list[Detection] = []
        modifications: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        resolved_by_record: dict[str, list[Span]] = {}
        skipped_hard_boundaries = 0
        for record in package.records:
            proposed = candidates_by_record.get(record.record_id, [])
            enabled = [
                candidate for candidate in proposed
                if self.config.enabled(candidate.pii_type)
                and not (
                    candidate.pii_type in {"PERSON", "COMPANY"}
                    and allowlist.veto_entity(candidate.text)
                )
            ]
            enabled = self._split_boundary_entities(record, enabled)
            selected = resolve_spans(enabled)
            resolved_by_record[record.record_id] = [
                span for span in selected
                if not record.crosses_hard_boundary(span.start, span.end)
            ]
            skipped_hard_boundaries += len(selected) - len(resolved_by_record[record.record_id])
            if self._debug_matches(record):
                selected_keys = {
                    (span.start, span.end, span.pii_type, span.detector)
                    for span in resolved_by_record[record.record_id]
                }
                for candidate in proposed:
                    key = (
                        candidate.start,
                        candidate.end,
                        candidate.pii_type,
                        candidate.detector,
                    )
                    if not self.config.enabled(candidate.pii_type):
                        status, rule = "rejected", "pii_type_disabled"
                    elif (
                        candidate.pii_type in {"PERSON", "COMPANY"}
                        and allowlist.veto_entity(candidate.text)
                    ):
                        status, rule = "rejected", "entity_allowlist"
                    elif record.crosses_hard_boundary(candidate.start, candidate.end):
                        status, rule = "rejected", "hard_tab_or_line_break_boundary"
                    elif key in selected_keys:
                        status, rule = "accepted", "overlap_resolution_winner"
                    else:
                        status, rule = "rejected", "overlap_resolution_loser_or_projected"
                    debug_traces.append(
                        {
                            "record_id": record.record_id,
                            "stage": "pipeline",
                            "candidate": candidate.text,
                            "type": candidate.pii_type,
                            "start": candidate.start,
                            "end": candidate.end,
                            "detector": candidate.detector,
                            "status": status,
                            "rule": rule,
                        }
                    )

        protected_person_surnames = {
            tokens[-1]
            for spans in resolved_by_record.values()
            for span in spans
            if span.pii_type == "PERSON"
            for tokens in [
                re.findall(
                    r"[A-Za-z][A-Za-z'’-]*",
                    str(span.metadata.get("canonical", span.text)),
                )
            ]
            if len(tokens) >= 2 and len(tokens[-1]) >= 3
        }
        store = SurrogateStore(self.config.seed, protected_person_surnames)

        # Prime name mappings first so e-mails such as first.last@… reuse the
        # corresponding person's fake first/last name even when the e-mail
        # appears earlier in document order.
        for spans in resolved_by_record.values():
            for span in spans:
                if span.pii_type == "PERSON":
                    store.replacement_for(span)

        for record in package.records:
            for span in resolved_by_record.get(record.record_id, []):
                replacement = store.replacement_for(span)
                detections.append(Detection(record.record_id, record.part_name, span, replacement))
                modifications[record.record_id].append((span.start, span.end, replacement))
        store.assert_globally_injective()

        image_redactor = ImageRedactor(self.config.image_policy)
        image_redactions = image_redactor.inspect(
            package,
            ((item.span.pii_type, item.span.text) for item in detections),
        )

        counts = dict(sorted(Counter(item.span.pii_type for item in detections).items()))
        result = RedactionResult(
            input_path=source,
            output_path=destination,
            detections=detections,
            counts=counts,
            ner_model=ner.model_name if ner is not None else "disabled",
            glossary_terms=len(glossary_terms),
            gazetteer_entries=len(gazetteer.entries),
            image_redactions=image_redactions,
            skipped_hard_boundaries=skipped_hard_boundaries,
            debug_traces=debug_traces,
        )
        if dry_run:
            return result

        for record_id, replacements in modifications.items():
            DocxPackage.apply_spans(records_by_id[record_id], replacements)
        self._assert_person_surnames_replaced(detections, records_by_id)
        image_redactor.apply(package, image_redactions)
        package.mark_redacted()
        package.write(destination)
        if map_path:
            store.write(
                map_path,
                {
                    "input": str(source),
                    "output": str(destination),
                    "detection_count": len(detections),
                    "image_redaction_count": len(image_redactions),
                    "skipped_hard_boundaries": skipped_hard_boundaries,
                    "ner_model": result.ner_model,
                },
            )
        if detections_path:
            self._write_detections(detections_path, detections)
        if image_audit_path:
            image_redactor.write_audit(image_audit_path, image_redactions)
        return result

    def _debug_matches(self, record: TextRecord) -> bool:
        selector = self.config.debug_block
        return bool(
            selector
            and (
                selector.casefold() in record.record_id.casefold()
                or selector.casefold() in record.text.casefold()
            )
        )

    @staticmethod
    def _add_block_addresses(
        package: DocxPackage,
        detector: AddressDetector,
        candidates: dict[str, list[Span]],
        protected_records: set[str],
    ) -> None:
        body_windows = [
            block for block in package.body_windows()
            if block.segments
            and 3600
            <= int(block.segments[0].record.record_id.rsplit(":p", 1)[1])
            <= 3959
        ]
        scoped_blocks = [
            *((block, None) for block in package.blocks()),
            *((block, None) for block in body_windows),
            *((block, {"us_address"}) for block in package.body_windows(size=3)),
        ]
        for block, allowed_methods in scoped_blocks:
            context = DetectionContext(
                block.block_id,
                block.segments[0].record.part_name if block.segments else "",
                metadata=block.metadata,
            )
            for block_span in detector.detect(block.text, context):
                if allowed_methods is not None and block_span.metadata.get("method") not in allowed_methods:
                    continue
                projected = block.project(block_span.start, block_span.end)
                if len(projected) <= 1:
                    continue
                if any(record.record_id in protected_records for record, *_rest in projected):
                    continue
                for segment_index, (record, start, end) in enumerate(projected):
                    candidates[record.record_id] = [
                        existing
                        for existing in candidates.get(record.record_id, [])
                        if existing.pii_type != "POSTAL_ADDRESS"
                        or existing.end <= start
                        or end <= existing.start
                    ]
                    metadata = dict(block_span.metadata)
                    metadata.update(
                        {
                            "canonical": block_span.text,
                            "block_id": block.block_id,
                            "block_segment_index": segment_index,
                            "block_segment_count": len(projected),
                        }
                    )
                    candidates[record.record_id].append(
                        Span(
                            start,
                            end,
                            block_span.pii_type,
                            record.text[start:end],
                            "address_block",
                            block_span.confidence,
                            block_span.priority,
                            metadata,
                        )
                    )

    @staticmethod
    def _protected_address_records(records: list[TextRecord]) -> set[str]:
        protected: set[str] = set()
        pattern = re.compile(
            r"\b(?:Securities\s+and\s+Exchange\s+Board\s+of\s+India|Registrar\s+of\s+Companies)\b",
            re.I,
        )
        for index, record in enumerate(records):
            match = re.search(r":p(\d+)$", record.record_id)
            paragraph_index = int(match.group(1)) if match else -1
            if paragraph_index >= 3600 and pattern.search(record.text):
                for neighbor in records[index : index + 4]:
                    if neighbor.part_name == record.part_name:
                        protected.add(neighbor.record_id)
        return protected

    @staticmethod
    def _write_detections(path: str | Path, detections: list[Detection]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for detection in detections:
                handle.write(json.dumps(detection.as_dict(), ensure_ascii=False) + "\n")
        return target

    @staticmethod
    def _assert_person_surnames_replaced(
        detections: list[Detection], records_by_id: dict[str, TextRecord]
    ) -> None:
        surnames: dict[str, str] = {}
        for detection in detections:
            if detection.span.pii_type != "PERSON":
                continue
            canonical = str(detection.span.metadata.get("canonical", detection.span.text))
            tokens = re.findall(r"[A-Za-z][A-Za-z'’-]*", canonical)
            if len(tokens) < 2:
                continue
            surname = tokens[-1]
            if len(surname) < 3:
                continue
            surnames.setdefault(surname.casefold(), surname)
        current = "\n".join(record.current_text for record in records_by_id.values())
        for surname in surnames.values():
            if re.search(
                rf"(?<![A-Za-z]){re.escape(surname)}(?![A-Za-z])", current, re.I
            ):
                raise ValueError(
                    f"person surname leak after redaction: {surname!r}"
                )

    @staticmethod
    def _split_boundary_entities(record: TextRecord, candidates: list[Span]) -> list[Span]:
        """Project a person/company across Word breaks into writable segments."""

        projected: list[Span] = []
        for candidate in candidates:
            if candidate.pii_type not in {"PERSON", "COMPANY"} or not record.crosses_hard_boundary(
                candidate.start, candidate.end
            ):
                projected.append(candidate)
                continue
            segments: list[tuple[int, int]] = []
            cursor = candidate.start
            boundary_slices = sorted(
                (
                    node_slice for node_slice in record.nodes
                    if node_slice.hard_boundary
                    and candidate.start < node_slice.end
                    and node_slice.start < candidate.end
                ),
                key=lambda item: item.start,
            )
            for boundary in boundary_slices:
                if cursor < boundary.start:
                    segments.append((cursor, boundary.start))
                cursor = max(cursor, boundary.end)
            if cursor < candidate.end:
                segments.append((cursor, candidate.end))
            trimmed: list[tuple[int, int]] = []
            for start, end in segments:
                while start < end and record.text[start].isspace():
                    start += 1
                while end > start and record.text[end - 1].isspace():
                    end -= 1
                if end > start:
                    trimmed.append((start, end))
            if len(trimmed) < 2:
                projected.append(candidate)
                continue
            canonical = re.sub(
                r"\s+", " ", str(candidate.metadata.get("canonical", candidate.text))
            ).strip()
            for index, (start, end) in enumerate(trimmed):
                metadata = dict(candidate.metadata)
                metadata.update(
                    {
                        "canonical": canonical,
                        "block_segment_index": index,
                        "block_segment_count": len(trimmed),
                        "hard_boundary_projection": True,
                    }
                )
                projected.append(
                    Span(
                        start,
                        end,
                        candidate.pii_type,
                        record.text[start:end],
                        "entity_boundary_projection",
                        candidate.confidence,
                        candidate.priority,
                        metadata,
                    )
                )
        return projected
