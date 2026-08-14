"""Optional spaCy entity detector with finance-domain precision guards."""

from __future__ import annotations

import re

from ..config import TYPE_PRIORITIES
from ..spans import Span
from .base import DetectionContext, Detector
from .glossary import Allowlist


ORG_SUFFIX_RE = re.compile(
    r"\b(?:Limited|Private\s+Limited|Pvt\.?\s+Ltd\.?|Ltd\.?|LLC|LLP|L\.L\.P\.?|"
    r"Inc\.?|Incorporated|Corporation|Corp\.?|Company|plc|S\.A\.|N\.V\.|GmbH|AG|"
    r"Partners|Partnerships|Bank|Industries|Associates|&\s+Co\.?)$",
    re.I,
)
PERSON_VETO_TOKENS = {
    "account", "address", "ahilyanagar", "bank", "bhavan", "bidder", "bidders", "branch", "building", "cagr",
    "apartment", "birdewadi", "cap", "chakan", "chambers", "city", "company", "conductors", "corporation", "depository",
    "district", "facility", "floor", "fund", "house", "industrial", "language", "listing",
    "lok", "margin", "mauje", "nagar", "newspaper", "office", "parel", "park", "participant", "pat",
    "marg", "no", "price", "regional", "registration", "sabha", "scheme", "sebi", "sector", "showroom", "supa", "suraksha",
    "taluka", "transformer", "trust", "urja",
}
PERSON_SEQUENCE_RE = re.compile(
    r"[A-Z][A-Za-z'’-]+(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'’-]+)){1,4}"
)
BOX_TOKEN_RE = re.compile(r"^(?:P\.?\s*O\.?|PO)?\s*Box$", re.I)
ORG_LEADING_ROLE_RE = re.compile(
    r"^(?:(?:Chief\s+Financial\s+Officer|Director|Partner|Operating\s+Partner|"
    r"Founder\s+and\s+Chairman|Chairman)\s*,\s*|"
    r"Among\s+|S/\s*|DIRECTORS\s+AND\s+EXECUTIVE\s+OFFICERS\s+OF\s+|"
    r"Board\s+of\s+Directors\s+of\s+)",
    re.I,
)
BARE_NAME_TLD_RE = re.compile(
    r"(?<![A-Za-z0-9@./-])(?:[A-Z][A-Za-z0-9-]{2,})\."
    r"(?:com|org|net|edu|io|ai)(?![A-Za-z0-9.-])"
)
CONTEXTUAL_BRAND_RE = re.compile(
    r"(?<![\w])([A-Z][A-Za-z0-9&'’-]{2,})"
    r"(?=,\s+one\s+of\s+the\s+largest\s+(?:health\s+systems?|companies|providers|employers)\b)",
    re.I,
)
PUBLIC_BARE_DOMAINS = {"sec.gov", "sebi.gov.in", "pcaob.org"}
TABLE_FOOTNOTE_RE = re.compile(r"[ \u00A0]+\d+(?:\s*,\s*\d+)*\s*$")


class SpacyNerDetector(Detector):
    pii_type = "NER"
    priority = 75

    def __init__(
        self,
        allowlist: Allowlist,
        known_company_stems: set[str] | None = None,
        lowercase_vocabulary: set[str] | None = None,
    ) -> None:
        self.allowlist = allowlist
        self.known_company_stems = {
            re.sub(
                r"\s+(?:Private\s+Limited|Public\s+Limited|Limited|Ltd\.?|LLP|L\.L\.P\.?)$",
                "",
                item,
                flags=re.I,
            ).casefold()
            for item in (known_company_stems or set())
        }
        self.lowercase_vocabulary = lowercase_vocabulary or set()
        self.last_trace: list[dict[str, object]] = []
        self.nlp = None
        self.model_name = "unavailable"
        try:
            import spacy

            for model_name in ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"):
                try:
                    self.nlp = spacy.load(model_name, disable=["tagger", "parser", "lemmatizer"])
                    self.model_name = model_name
                    break
                except OSError:
                    continue
        except ImportError:
            self.nlp = None

    @property
    def available(self) -> bool:
        return self.nlp is not None

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        self.last_trace = []
        if self.nlp is None or not text.strip():
            return []
        doc = self.nlp(text)
        results: list[Span] = []

        def trace(
            candidate: str,
            label: str,
            start: int,
            end: int,
            status: str,
            rule: str,
        ) -> None:
            self.last_trace.append(
                {
                    "candidate": candidate,
                    "entity_label": label,
                    "start": start,
                    "end": end,
                    "detector": "spacy",
                    "status": status,
                    "rule": rule,
                }
            )

        for entity in doc.ents:
            raw = entity.text
            value = raw.strip(" \t\r\n/&,")
            leading = len(raw) - len(raw.lstrip(" \t\r\n/&,"))
            start, end = entity.start_char + leading, entity.start_char + leading + len(value)
            if entity.label_ == "PERSON" and context and context.metadata.get("cell_key"):
                footnote = TABLE_FOOTNOTE_RE.search(value)
                if footnote:
                    untrimmed = value
                    value = value[: footnote.start()].rstrip()
                    end = start + len(value)
                    trace(
                        untrimmed,
                        entity.label_,
                        start,
                        entity.start_char + leading + len(untrimmed),
                        "normalized",
                        "trailing_table_footnote_trimmed",
                    )
            if len(value) < 3:
                trace(value, entity.label_, start, end, "rejected", "minimum_span_length")
                continue
            if self.allowlist.veto_entity(value):
                trace(value, entity.label_, start, end, "rejected", "entity_allowlist")
                continue
            if entity.label_ == "PERSON":
                if re.fullmatch(r"[A-Z][A-Za-z0-9'’-]{2,}", value):
                    following = text[end : end + 32]
                    if re.match(
                        r"\s+(?:has|provides?|offers?|administers?|connects?|develops?|operates?)\b",
                        following,
                        re.I,
                    ) and value.casefold() not in self.lowercase_vocabulary:
                        results.append(
                            Span(
                                start, end, "COMPANY", value, "spacy_context_org", 0.78,
                                TYPE_PRIORITIES["COMPANY"],
                                {"model": self.model_name, "known_party": False},
                            )
                        )
                        trace(value, entity.label_, start, end, "accepted", "person_context_org")
                    else:
                        trace(value, entity.label_, start, end, "rejected", "single_token_person")
                    continue
                expanded = PERSON_SEQUENCE_RE.match(text, start)
                may_expand_surname = bool(re.search(r"\b[A-Z]\.\s*$", value))
                if may_expand_surname and expanded is not None and expanded.end() > end:
                    candidate = expanded.group(0)
                    candidate_start, candidate_end = expanded.span()
                else:
                    full = PERSON_SEQUENCE_RE.fullmatch(value)
                    if full is None:
                        trace(
                            value,
                            entity.label_,
                            start,
                            end,
                            "rejected",
                            "person_shape_capitalization_or_length",
                        )
                        continue
                    candidate = value
                    candidate_start, candidate_end = start, end
                tokens = {
                    token.casefold().strip(".'’")
                    for token in candidate.split()
                    if token.strip(".'’")
                }
                if tokens & PERSON_VETO_TOKENS:
                    trace(candidate, entity.label_, candidate_start, candidate_end, "rejected", "person_veto_token")
                    continue
                if candidate.casefold() == "deen dayal":
                    trace(candidate, entity.label_, candidate_start, candidate_end, "rejected", "known_nonperson_phrase")
                    continue
                if any(BOX_TOKEN_RE.fullmatch(token) for token in candidate.split()):
                    trace(candidate, entity.label_, candidate_start, candidate_end, "rejected", "po_box_token")
                    continue
                if tokens and tokens <= self.lowercase_vocabulary:
                    trace(candidate, entity.label_, candidate_start, candidate_end, "rejected", "all_tokens_in_lowercase_vocabulary")
                    continue
                if self.allowlist.veto_entity(candidate):
                    trace(candidate, entity.label_, candidate_start, candidate_end, "rejected", "entity_allowlist")
                    continue
                results.append(
                    Span(
                        candidate_start, candidate_end, "PERSON", candidate,
                        "spacy", 0.75, TYPE_PRIORITIES["PERSON"], {"model": self.model_name},
                    )
                )
                trace(candidate, entity.label_, candidate_start, candidate_end, "accepted", "person_candidate")
            elif entity.label_ == "ORG":
                cleaned = re.sub(
                    r"^(?:the|our|company|registered\s+office\s+of\s+our\s+company)\s+",
                    "",
                    value,
                    flags=re.I,
                )
                cleaned = ORG_LEADING_ROLE_RE.sub("", cleaned).strip()
                cleaned_start = start + (len(value) - len(cleaned))
                if self.allowlist.veto_entity(cleaned):
                    trace(cleaned, entity.label_, cleaned_start, end, "rejected", "entity_allowlist")
                    continue
                normalized = cleaned.casefold()
                tokens = {token.casefold().strip(".()") for token in cleaned.split()}
                generic = tokens <= {
                    "family", "trust", "private", "public", "limited", "ltd", "llc", "llp", "bank",
                    "company", "corporation", "inc", "plc", "partners", "partnerships",
                }
                known = any(normalized == stem or stem.startswith(normalized) for stem in self.known_company_stems)
                lowercase_only = bool(tokens) and tokens <= self.lowercase_vocabulary
                left_context = text[max(0, cleaned_start - 140) : cleaned_start]
                right_context = text[end : min(len(text), end + 100)]
                context_supported = bool(
                    re.search(
                        r"(?:partners?\s+like|investor\s+in|partnership\s+with|"
                        r"acquir(?:ed|ing)|acquisition\s+of)[^.;]{0,100}$",
                        left_context,
                        re.I,
                    )
                    or re.match(
                        r"\s*(?:,\s*our\s+transfer\s+agent|(?:also\s+)?administers?\s+a\s+direct\s+stock|"
                        r"connects\s+the\s+world|provides?\s+(?:a\s+collaboration\s+platform|AI\s+solutions)|"
                        r"has\s+reduced|is\s+now\s+home\s+to|maintain\s+a\s+long-term\s+strategic\s+partnership)",
                        right_context,
                        re.I,
                    )
                )
                if (
                    not generic
                    and not lowercase_only
                    and ")" not in cleaned
                    and re.search(r"[A-Za-z]", cleaned)
                    and (ORG_SUFFIX_RE.search(cleaned) or known or context_supported)
                ):
                    results.append(
                        Span(
                            cleaned_start, end, "COMPANY", cleaned, "spacy", 0.72,
                            TYPE_PRIORITIES["COMPANY"],
                            {"model": self.model_name, "known_party": known},
                        )
                    )
                    trace(cleaned, entity.label_, cleaned_start, end, "accepted", "organization_candidate")
                else:
                    reason = (
                        "generic_organization_tokens" if generic else
                        "all_tokens_in_lowercase_vocabulary" if lowercase_only else
                        "invalid_organization_boundary" if ")" in cleaned else
                        "missing_organization_evidence"
                    )
                    trace(cleaned, entity.label_, cleaned_start, end, "rejected", reason)

        occupied = {(span.start, span.end, span.pii_type) for span in results}
        for match in BARE_NAME_TLD_RE.finditer(text):
            value = match.group(0)
            host = value.casefold()
            base = value.rsplit(".", 1)[0]
            key = (match.start(), match.end(), "COMPANY")
            if host in PUBLIC_BARE_DOMAINS or self.allowlist.veto_entity(base):
                trace(value, "ORG_CANDIDATE", match.start(), match.end(), "rejected", "public_domain_or_allowlist")
                continue
            if key not in occupied:
                results.append(
                    Span(
                        match.start(), match.end(), "COMPANY", value,
                        "bare_domain_org", 0.82, TYPE_PRIORITIES["COMPANY"],
                        {"model": self.model_name, "known_party": False},
                    )
                )
                occupied.add(key)
            trace(value, "ORG_CANDIDATE", match.start(), match.end(), "accepted", "bare_name_tld_organization")

        for match in CONTEXTUAL_BRAND_RE.finditer(text):
            value = match.group(1)
            key = (match.start(1), match.end(1), "COMPANY")
            if value.casefold() in self.lowercase_vocabulary or self.allowlist.veto_entity(value):
                trace(value, "ORG_CANDIDATE", match.start(1), match.end(1), "rejected", "lowercase_vocabulary_or_allowlist")
                continue
            if key not in occupied:
                results.append(
                    Span(
                        match.start(1), match.end(1), "COMPANY", value,
                        "spacy_context_org", 0.78, TYPE_PRIORITIES["COMPANY"],
                        {"model": self.model_name, "known_party": False},
                    )
                )
                occupied.add(key)
            trace(value, "ORG_CANDIDATE", match.start(1), match.end(1), "accepted", "descriptive_organization_context")
        return results
