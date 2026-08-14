"""Document-seeded entity discovery and variant propagation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..config import TYPE_PRIORITIES
from ..docx_io import TableData, TextRecord
from ..spans import Span
from .base import DetectionContext, Detector
from .glossary import Allowlist


HONORIFIC_RE = re.compile(r"^(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+", re.I)
PERSON_SHAPE_RE = re.compile(
    r"\b(?:(?i:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+)?"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,4})\b"
)
LABEL_PERSON_RE = re.compile(
    r"(?i:contact\s+person|employee|customer|client|company\s+secretary(?:\s+and\s+compliance\s+officer)?|"
    r"chief\s+(?:executive|financial)\s+officer|managing\s+director|compliance\s+officer)"
    r"\s*[:.-]\s*(?:(?i:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+)?"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,4})",
)
LABEL_HEAD_RE = re.compile(
    r"(?:contact\s+person|company\s+secretary(?:\s+and\s+compliance\s+officer)?|"
    r"chief\s+(?:executive|financial)\s+officer|managing\s+director|compliance\s+officer)"
    r"\s*[:.-]\s*",
    re.I,
)
LABEL_STOP_RE = re.compile(
    r"\b(?:website|e-?mail|telephone|mobile|phone|SEBI\s+registration)\b\s*[:.-]", re.I
)
BEING_PERSON_RE = re.compile(
    r"(?i:\bbeing)\s+(?:(?i:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+)?"
    r"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,4})",
)
ROLE_WORDS = {
    "chief", "executive", "financial", "officer", "company", "secretary", "compliance",
    "managing", "director", "contact", "person", "registered", "office", "promoter",
    "regional", "language", "village", "taluka", "district", "details", "below",
}
TRANSACTION_NAME_RE = re.compile(
    r"(?:\bto|\bfrom|\band|,)\s+"
    r"([A-Z][A-Za-z'’-]+(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'’-]+)){1,3})"
    r"(?=\s*(?:,|\band\b|\bfrom\b|\bto\b|\.|$))"
)
NON_PERSON_TRANSACTION_TOKENS = {
    "family", "trust", "huf", "limited", "ltd", "llp", "llc", "bank", "company",
    "portion", "qib", "nii", "rii",
}
PARTY_SUFFIXES = {
    "Private Limited", "Public Limited", "Pvt. Ltd.", "Pvt Ltd", "Limited", "Ltd.",
    "LLP", "L.L.P.", "Trust", "Bank N.A.", "Bank Limited", "Bank of India", "N.A.",
    "Associates", "& Co.", "and Co.",
}


@dataclass(slots=True)
class GazetteerEntry:
    canonical: str
    pii_type: str
    variants: set[str] = field(default_factory=set)
    source: str = "seed"
    confidence: float = 0.97


class EntityGazetteer:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], GazetteerEntry] = {}

    def add(
        self,
        value: str,
        pii_type: str,
        source: str,
        confidence: float = 0.97,
        variant: str | None = None,
    ) -> None:
        value = re.sub(r"\s+", " ", value).strip(" \t\r\n,;:()[]")
        if pii_type == "PERSON":
            value = HONORIFIC_RE.sub("", value).strip()
        if len(value) < 3:
            return
        key = (pii_type, value.casefold())
        entry = self.entries.get(key)
        if entry is None:
            entry = GazetteerEntry(value, pii_type, {variant or value, value}, source, confidence)
            self.entries[key] = entry
        else:
            entry.variants.add(value)
            if variant:
                entry.variants.add(variant)
            entry.confidence = max(entry.confidence, confidence)

    def expand_variants(self, lowercase_vocabulary: set[str] | None = None) -> None:
        lowercase_vocabulary = lowercase_vocabulary or set()
        surname_owners: dict[str, set[str]] = {}
        for entry in self.entries.values():
            if entry.pii_type != "PERSON":
                continue
            tokens = entry.canonical.split()
            if len(tokens) >= 2:
                surname_owners.setdefault(tokens[-1].casefold(), set()).add(entry.canonical.casefold())

        for entry in self.entries.values():
            entry.variants.add(entry.canonical.upper())
            if entry.pii_type == "PERSON":
                tokens = entry.canonical.split()
                if len(tokens) > 2:
                    entry.variants.add(f"{tokens[0]} {tokens[-1]}")
                    entry.variants.add(f"{tokens[0]} {tokens[-1]}".upper())
                surname = tokens[-1]
                if (
                    len(surname) >= 4
                    and surname.casefold() not in lowercase_vocabulary
                    and len(surname_owners.get(surname.casefold(), ())) == 1
                ):
                    entry.variants.add(surname)
                    entry.variants.add(surname.upper())
            elif entry.pii_type == "COMPANY":
                entry.variants.add(re.sub(r"\bPrivate\s+Limited\b", "Pvt. Ltd.", entry.canonical, flags=re.I))
                short = re.sub(
                    r"(?:,?\s+)(?:Private\s+Limited|Public\s+Limited|Limited|Ltd\.?|LLC|LLP|L\.L\.P\.?|"
                    r"Inc\.?|Incorporated|Corporation|Corp\.?|Company|plc|S\.A\.|N\.V\.|GmbH|AG|"
                    r"Partners|Partnerships|Bank)$",
                    "",
                    entry.canonical,
                    flags=re.I,
                )
                if (
                    short
                    and short.casefold() not in {"private", "public", "india", "the"}
                    and not (
                        len(short.split()) == 1
                        and short.casefold().strip(".,") in lowercase_vocabulary
                    )
                ):
                    entry.variants.add(short)

    def merge_equivalent_companies(self) -> None:
        groups: dict[str, list[tuple[tuple[str, str], GazetteerEntry]]] = {}
        for key, entry in self.entries.items():
            if entry.pii_type != "COMPANY":
                continue
            compact = re.sub(r"[^a-z0-9]", "", entry.canonical.casefold())
            groups.setdefault(compact, []).append((key, entry))
        for group in groups.values():
            if len(group) < 2:
                continue
            winner_key, winner = max(group, key=lambda item: (item[1].canonical.count(" "), len(item[1].canonical)))
            for key, entry in group:
                if key == winner_key:
                    continue
                winner.variants.update(entry.variants)
                winner.variants.add(entry.canonical)
                winner.confidence = max(winner.confidence, entry.confidence)
                self.entries.pop(key, None)

    def by_type(self, pii_type: str) -> list[GazetteerEntry]:
        return [entry for entry in self.entries.values() if entry.pii_type == pii_type]

    def enrich_camel_people(self, records: Iterable[TextRecord]) -> None:
        """Recover names whose Word runs lost spaces (``PushpaKushal Hegde``)."""

        surnames = {entry.canonical.split()[-1].casefold() for entry in self.by_type("PERSON")}
        camel_name = re.compile(r"(?<![\w])([A-Z][a-z]{2,})([A-Z][a-z]{2,})\s+([A-Z][A-Za-z'’-]{2,})(?![\w])")
        for record in records:
            for match in camel_name.finditer(record.text):
                first, middle, last = match.groups()
                if last.casefold() not in surnames:
                    continue
                self.add(
                    f"{first} {middle} {last}",
                    "PERSON",
                    "camel_recovery",
                    0.995,
                    variant=match.group(0),
                )

    def enrich_boundary_people(self, records: Iterable[TextRecord]) -> None:
        """Recover names separated by Word tabs/line breaks."""

        known_surnames = {
            entry.canonical.split()[-1].casefold().strip(".")
            for entry in self.by_type("PERSON")
            if len(entry.canonical.split()) >= 2
        }
        for record in records:
            if not any(node_slice.hard_boundary for node_slice in record.nodes):
                continue
            for match in re.finditer(
                r"(?<![A-Za-z])([A-Z][A-Za-z'’-]+(?:[\t\n ]+"
                r"(?:[A-Z]\.|[A-Z][A-Za-z'’-]+)){1,4})(?![A-Za-z])",
                record.text,
            ):
                candidate = re.sub(r"[\t\n ]+", " ", match.group(1)).strip()
                tokens = candidate.split()
                token_set = {
                    token.casefold().strip(".") for token in tokens
                }
                if (
                    len(tokens) < 3
                    or tokens[-1].casefold().strip(".") not in known_surnames
                    or token_set & ROLE_WORDS
                    or token_set & {"family", "trust", "limited", "llp", "llc", "bank", "company"}
                ):
                    continue
                self.add(
                    candidate,
                    "PERSON",
                    "boundary_recovery",
                    0.99,
                    variant=match.group(1),
                )


class GazetteerBuilder:
    def __init__(
        self,
        allowlist: Allowlist,
        suffixes_file: Path,
        company_scope: str = "parties",
        lowercase_vocabulary: set[str] | None = None,
    ) -> None:
        self.allowlist = allowlist
        self.company_scope = company_scope
        self.lowercase_vocabulary = lowercase_vocabulary or set()
        suffixes = [
            line.strip() for line in suffixes_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if self.company_scope == "parties":
            suffixes = [suffix for suffix in suffixes if suffix in PARTY_SUFFIXES]
        suffix_expression = "|".join(sorted((re.escape(item) for item in suffixes), key=len, reverse=True))
        if self.company_scope == "parties":
            token = r"(?:[A-Z][A-Za-z0-9'’&()./-]*|and|of|the|&|N\.A\.)"
            expression = rf"(?<![\w])({token}(?:\s+{token}){{0,11}}\s+(?i:{suffix_expression}))(?![\w])"
        else:
            token = r"(?:[A-Z][A-Za-z0-9'’()./-]*,?|of|the|&|N\.A\.)"
            expression = rf"(?<![A-Za-z0-9&'-])({token}(?:\s+{token}){{0,5}}\s+(?i:{suffix_expression}))(?![A-Za-z0-9&'-])"
        self.company_re = re.compile(expression)

    def build(self, records: Iterable[TextRecord], tables: Iterable[TableData]) -> EntityGazetteer:
        records = list(records)
        tables = list(tables)
        gazetteer = EntityGazetteer()
        self._seed_tables(gazetteer, tables)
        for record in records:
            self._seed_labeled_people(gazetteer, record.text)
            self._seed_transaction_people(gazetteer, record.text)
            self._seed_role_organizations(gazetteer, record.text)
            if self.company_scope == "all" or (
                self.company_scope == "parties" and self._is_party_directory_record(record)
            ):
                self._seed_companies(gazetteer, record.text)
        gazetteer.merge_equivalent_companies()
        self._seed_company_table_aliases(gazetteer, tables)
        gazetteer.enrich_boundary_people(records)
        gazetteer.expand_variants(self.lowercase_vocabulary)
        return gazetteer

    @staticmethod
    def _seed_company_table_aliases(
        gazetteer: EntityGazetteer, tables: Iterable[TableData]
    ) -> None:
        """Link a definitions-table short term to its full legal company name."""

        for table in tables:
            if not table.rows:
                continue
            headers = [re.sub(r"\s+", " ", cell).strip().casefold() for cell in table.rows[0]]
            term_columns = [index for index, value in enumerate(headers) if value == "term"]
            description_columns = [index for index, value in enumerate(headers) if value == "description"]
            for row in table.rows[1:]:
                for term_column in term_columns:
                    if term_column >= len(row):
                        continue
                    alias = re.sub(r"\s+", " ", row[term_column]).strip()
                    if not re.fullmatch(r"[A-Z][A-Za-z0-9&.'’-]{2,30}", alias):
                        continue
                    descriptions = " ".join(
                        row[index] for index in description_columns if index < len(row)
                    )
                    matches = [
                        entry for entry in gazetteer.by_type("COMPANY")
                        if re.search(
                            rf"(?<![\w]){re.escape(entry.canonical)}(?![\w])",
                            descriptions,
                            re.I,
                        )
                    ]
                    if len(matches) == 1 and matches[0].canonical.casefold().startswith(
                        alias.casefold() + " "
                    ):
                        matches[0].variants.add(alias)

    @staticmethod
    def _is_party_directory_record(record: TextRecord) -> bool:
        if record.part_name != "word/document.xml":
            return False
        match = re.search(r":p(\d+)$", record.record_id)
        if not match:
            return False
        paragraph_index = int(match.group(1))
        # Cover/offer definitions and the General Information directory. These
        # are structural party lists; the intervening risk/business prose also
        # names customers, peers and government corporations that are not PII.
        return paragraph_index < 624 or 3626 <= paragraph_index <= 3955

    def _seed_tables(self, gazetteer: EntityGazetteer, tables: Iterable[TableData]) -> None:
        for table in tables:
            if not table.rows:
                continue
            headers = [re.sub(r"\s+", " ", cell).strip().casefold() for cell in table.rows[0]]
            name_columns = [
                index for index, header in enumerate(headers)
                if re.search(r"\bname\b", header) and not re.search(r"bank|company|firm|issuer", header)
            ]
            has_din = any(re.fullmatch(r"(?:din|director identification number)", header) for header in headers)
            has_address = any(re.fullmatch(r"(?:residential\s+)?address", header) for header in headers)
            has_designation = any(re.fullmatch(r"designation", header) for header in headers)
            structured_people = has_din and (has_address or has_designation)
            contact_table = any(re.fullmatch(r"contact\s+person", header) for header in headers)
            if not (structured_people or contact_table):
                continue
            for row in table.rows[1:]:
                for column in name_columns:
                    if column >= len(row):
                        continue
                    candidate = HONORIFIC_RE.sub("", row[column]).strip()
                    match = PERSON_SHAPE_RE.fullmatch(candidate)
                    if match and not self._veto_person(match.group(1)):
                        gazetteer.add(match.group(1), "PERSON", "table", 0.99)

    def _seed_labeled_people(self, gazetteer: EntityGazetteer, text: str) -> None:
        for label in LABEL_HEAD_RE.finditer(text):
            stop = LABEL_STOP_RE.search(text, label.end())
            tail_end = stop.start() if stop else min(len(text), label.end() + 240)
            tail = text[label.end() : tail_end]
            for piece in re.split(r"\s*/\s*|\s*,\s*|\s+\band\b\s+", tail, flags=re.I):
                candidate = HONORIFIC_RE.sub("", piece).strip(" .;:-")
                match = PERSON_SHAPE_RE.fullmatch(candidate)
                if not match:
                    continue
                value = match.group(1)
                words = {word.casefold() for word in value.split()}
                if words & ROLE_WORDS or self._veto_person(value):
                    continue
                gazetteer.add(value, "PERSON", "label_list", 0.98)
        for pattern, source in ((LABEL_PERSON_RE, "label"), (BEING_PERSON_RE, "glossary_role")):
            for match in pattern.finditer(text):
                candidate = re.sub(r"\s+", " ", match.group(1)).strip()
                tokens = candidate.split()
                stop = next(
                    (index for index, token in enumerate(tokens) if token.casefold().strip(".:") in {
                        "website", "email", "telephone", "mobile", "phone", "sebi", "registration",
                    }),
                    len(tokens),
                )
                candidate = " ".join(tokens[:stop])
                if len(candidate.split()) < 2 or re.search(r"\b(?:Limited|LLP|Bank|Trust)\b", candidate, re.I):
                    continue
                words = {word.casefold() for word in candidate.split()}
                if words & ROLE_WORDS or self._veto_person(candidate):
                    continue
                gazetteer.add(candidate, "PERSON", source, 0.98)

    def _seed_transaction_people(self, gazetteer: EntityGazetteer, text: str) -> None:
        for trigger in re.finditer(
            r"\b(?:transfer\s+of\s+shares|allotted\s+to|initial\s+subscription)\b", text, re.I
        ):
            window = text[trigger.end() : trigger.end() + 280]
            for match in re.finditer(r"(?<![A-Z])\b[A-Z]{1,3}\s+[A-Z][a-z]{3,}\b", window):
                candidate = match.group(0)
                if candidate.casefold() not in {"qib portion", "nii portion", "rii portion"}:
                    gazetteer.add(candidate, "PERSON", "transaction", 0.97)
            # Full first-name/surname parties in these transfer/allotment lists
            # (e.g. "transfer of shares to Kushal Hegde from Jayaram Shetty")
            # are frequently mislabelled as ORG, or missed outright, by the
            # smaller spaCy model used in the deployed web runtime. Recover
            # them directly from the list structure instead of relying on NER.
            for match in TRANSACTION_NAME_RE.finditer(window):
                candidate = re.sub(r"\s+", " ", match.group(1)).strip()
                tokens = {token.casefold().strip(".") for token in candidate.split()}
                if tokens & NON_PERSON_TRANSACTION_TOKENS or self._veto_person(candidate):
                    continue
                gazetteer.add(candidate, "PERSON", "transaction", 0.97)

    def _seed_companies(self, gazetteer: EntityGazetteer, text: str) -> None:
        for match in self.company_re.finditer(text):
            candidate = re.sub(r"\s+", " ", match.group(1)).strip()
            candidate = re.sub(r"^(?:[ivxlcdm]+\)|\(?\d+\)?[.)])\s*", "", candidate, flags=re.I)
            candidate = re.sub(r"^(?:Formerly|Offer\s+Escrow\s+Collection\s+Bank)\s+", "", candidate)
            candidate = re.sub(
                r"^(?:(?:Chief\s+Financial\s+Officer|Director|Partner|Operating\s+Partner|"
                r"Founder\s+and\s+Chairman|Chairman)\s*,\s*|"
                r"Among\s+|S/\s*|DIRECTORS\s+AND\s+EXECUTIVE\s+OFFICERS\s+OF\s+|"
                r"Board\s+of\s+Directors\s+of\s+)",
                "",
                candidate,
                flags=re.I,
            )
            words = candidate.split()
            while len(words) > 2 and words[0].casefold() in {
                "our", "issuer", "company", "and", "of", "by", "to", "for", "with",
            }:
                words.pop(0)
            candidate = " ".join(words)
            candidate = re.sub(
                r"^(?:The\s+)?(?:(?:Chief\s+Financial\s+Officer|Director|Partner|Operating\s+Partner|"
                r"Founder\s+and\s+Chairman|Chairman)\s*,\s*|"
                r"Among\s+|S/\s*|DIRECTORS\s+AND\s+EXECUTIVE\s+OFFICERS\s+OF\s+|"
                r"Board\s+of\s+Directors\s+of\s+)",
                "",
                candidate,
                flags=re.I,
            )
            candidate = re.sub(r"^Offer\s+Escrow\s+Collection\s+Bank\s+", "", candidate)
            if (
                len(candidate.split()) < 2
                or ")" in candidate
                or candidate.casefold() in {
                    "private limited", "public limited", "india limited", "family trust", "the trust",
                }
            ):
                continue
            stem_tokens = {
                token.casefold().strip(".,()") for token in candidate.split()[:-1]
                if token not in {"&"}
            }
            if not (
                stem_tokens
                - {"a", "the", "of", "our", "private", "public", "india", "across", "while"}
            ):
                continue
            # Split adjacent legal entities ("X Limited and Y Limited") before
            # propagation so public bodies can be independently allowlisted.
            parts = re.split(
                r"(?<=Limited),?\s+and\s+(?=[A-Z])|(?<=Limited),\s+(?=[A-Z])|"
                r"(?<=Trust),?\s+and\s+(?=[A-Z])|(?<=Trust),\s+(?=[A-Z])|"
                r"(?<=LLP),?\s+and\s+(?=[A-Z])|(?<=LLP),\s+(?=[A-Z])|"
                r"(?<=LLC),?\s+and\s+(?=[A-Z])|(?<=LLC),\s+(?=[A-Z])|"
                r"(?<=Company),?\s+and\s+(?=[A-Z])|(?<=Company),\s+(?=[A-Z])|"
                r"(?<=Partners),?\s+and\s+(?=[A-Z])|(?<=Partners),\s+(?=[A-Z])|"
                r"(?<=Partnerships),?\s+and\s+(?=[A-Z])|(?<=Partnerships),\s+(?=[A-Z])|"
                r"(?<=Inc\.),?\s+and\s+(?=[A-Z])|(?<=Inc\.),\s+(?=[A-Z])",
                candidate,
                flags=re.I,
            )
            for part in parts:
                without_article = re.sub(r"^The\s+", "", part, flags=re.I)
                if not self.allowlist.veto_entity(part) and not self.allowlist.veto_entity(without_article):
                    gazetteer.add(part, "COMPANY", "suffix", 0.96)

    def _veto_person(self, candidate: str) -> bool:
        tokens = {
            token.casefold().strip(".'’")
            for token in candidate.split()
            if token.strip(".'’")
        }
        return (
            self.allowlist.veto_entity(candidate)
            or bool(tokens and tokens <= self.lowercase_vocabulary)
            or "box" in tokens
        )

    def _seed_role_organizations(self, gazetteer: EntityGazetteer, text: str) -> None:
        legal_counsel = re.search(
            r"Legal\s+Counsel\b.*?\bLaw\s+([A-Z][A-Za-z&.'’-]*(?:\s+[A-Z][A-Za-z&.'’-]*){0,5})\s*$",
            text,
            re.I,
        )
        if legal_counsel:
            candidate = legal_counsel.group(1).strip()
            if not self.allowlist.veto_entity(candidate):
                gazetteer.add(candidate, "COMPANY", "role_label", 0.98)


class GazetteerDetector(Detector):
    pii_type = "GAZETTEER"
    priority = TYPE_PRIORITIES["PERSON"]

    def __init__(self, gazetteer: EntityGazetteer, allowlist: Allowlist) -> None:
        self.gazetteer = gazetteer
        self.allowlist = allowlist
        variants: list[tuple[re.Pattern[str], GazetteerEntry, str]] = []
        for entry in gazetteer.entries.values():
            for variant in entry.variants:
                if len(variant) < 3:
                    continue
                tokens = re.split(r"\s+", variant.strip())
                expression = r"\s+".join(re.escape(token) for token in tokens)
                pattern = re.compile(rf"(?<![\w]){expression}(?:'s|’s)?(?![\w])", re.I)
                variants.append((pattern, entry, variant))
        self.patterns = sorted(variants, key=lambda item: len(item[2]), reverse=True)

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        results: list[Span] = []
        seen: set[tuple[int, int, str]] = set()
        for pattern, entry, _variant in self.patterns:
            for match in pattern.finditer(text):
                key = (match.start(), match.end(), entry.pii_type)
                if key in seen or self.allowlist.veto_entity(match.group(0)):
                    continue
                if entry.pii_type == "PERSON" and len(match.group(0).split()) == 1:
                    following = text[match.end() : match.end() + 32]
                    if re.match(
                        r"\s+(?:Metal|Hospital|Road|Street|Marg|Bank|Company|Limited|LLP|Trust|"
                        r"Industries|Enterprises|Associates|Apartment|Building|Tower)\b",
                        following,
                        re.I,
                    ):
                        continue
                seen.add(key)
                confidence = entry.confidence
                if entry.pii_type == "PERSON" and len(match.group(0).split()) >= 2:
                    confidence = max(confidence, 0.995)
                results.append(
                    Span(
                        match.start(), match.end(), entry.pii_type, match.group(0),
                        "gazetteer", confidence, TYPE_PRIORITIES[entry.pii_type],
                        {"canonical": entry.canonical, "source": entry.source},
                    )
                )
        return results
