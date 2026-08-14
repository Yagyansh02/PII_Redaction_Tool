"""India-oriented physical and mailing address detection."""

from __future__ import annotations

import re

from ..config import TYPE_PRIORITIES
from ..spans import Span
from .base import DetectionContext, Detector


ADDRESS_LABEL_RE = re.compile(
    r"(?:(?:registered|corporate|administrative|head|branch)\s+office"
    r"(?:\s+of\s+(?:our\s+)?company)?(?:\s+located)?(?:\s+at\s+|\s*[:.-]\s*)|"
    r"manufacturing\s+facility\s+located\s+at\s+|"
    r"(?:residential|postal|mailing|communication)\s+address\s*[:.-]\s*|address\s*[:.-]\s*)",
    re.I,
)
STREET_CUE_RE = re.compile(
    r"\b(?:flat|plot|house|room|floor|building|bldg|tower|apartment|road|rd\.?|street|st\.?|"
    r"lane|nagar|colony|village|taluka|tehsil|district|survey|gat|sector|industrial\s+park|"
    r"industrial\s+area|estate|chawl|society|complex|centre|center|campus|station|unit|highway|marg|avenue)\b",
    re.I,
)
PIN_RE = re.compile(r"(?<!\d)[1-9]\d{2}[ -]?\d{3}(?!\d)")
PIN_OCR_RE = re.compile(r"(?<!\d)[1-9]\d[Il][ -]?\d{3}(?!\d)")
INDIA_RE = re.compile(
    r"\b(?:India|Maharashtra|Gujarat|Karnataka|Tamil\s+Nadu|Telangana|Delhi|Haryana|Punjab|"
    r"Rajasthan|Uttar\s+Pradesh|West\s+Bengal|Kerala|Madhya\s+Pradesh|Odisha|Goa)\b",
    re.I,
)
US_ZIP_RE = re.compile(r"(?<!\d)\d{5}(?:-\d{4})?(?!\d)")
US_STATE_RE = re.compile(
    r"\b(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|"
    r"Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|"
    r"Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|"
    r"Nebraska|Nevada|New\s+Hampshire|New\s+Jersey|New\s+Mexico|New\s+York|"
    r"North\s+Carolina|North\s+Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode\s+Island|"
    r"South\s+Carolina|South\s+Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
    r"West\s+Virginia|Wisconsin|Wyoming|DC|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|"
    r"IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|"
    r"PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b",
    re.I,
)
US_STREET_KEYWORD = r"(?:Way|Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Suite|Floor)"
US_OPENING_RE = re.compile(
    rf"\bP\.?\s*O\.?\s+Box\s+\d+\b|"
    rf"\b\d+[A-Za-z-]*[ \u00A0]+(?:[A-Z][\w.'’-]*[ \u00A0]+){{0,5}}{US_STREET_KEYWORD}\b|"
    rf"\b(?:One|[A-Z][\w.'’-]*)[ \u00A0]+(?:[A-Z][\w.'’-]*[ \u00A0]+){{0,4}}{US_STREET_KEYWORD}\b",
)
US_STANDALONE_RE = re.compile(
    rf"^\s*(?:P\.?\s*O\.?\s+Box\s+\d+|"
    rf"(?:\d+[A-Za-z-]*|One)\s+(?:[A-Z][\w.'’-]*\s+){{0,5}}{US_STREET_KEYWORD})\s*$",
    re.I,
)
CONTACT_STOP_RE = re.compile(
    r"(?:\n|\s+)(?:e-?mail|telephone|tel\.?|phone|mobile|website|contact\s+person)\s*[:.-]|"
    r"\b(?:registered|corporate|administrative|head|branch)\s+office\b",
    re.I,
)
ORG_PREFIX_RE = re.compile(
    r"^(?:The\s+)?(?:[A-Z][A-Za-z0-9&.,'’()/-]*)(?:\s+(?:[A-Z][A-Za-z0-9&.,'’()/-]*|&|and|of)){0,10}\s+"
    r"(?:Private\s+Limited|Public\s+Limited|Limited|Ltd\.?|LLP|L\.L\.P\.?|Associates|N\.A\.)\s+",
)


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in ".;"):
        end -= 1
    return start, end


class AddressDetector(Detector):
    pii_type = "POSTAL_ADDRESS"
    priority = TYPE_PRIORITIES[pii_type]

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        if not text.strip():
            return []
        results: list[Span] = []
        organization_prefix = ORG_PREFIX_RE.match(text)
        address_floor = organization_prefix.end() if organization_prefix else 0
        column = str(context.metadata.get("column_header", "") if context else "")
        address_column = bool(re.search(r"\b(?:residential\s+)?address\b", column, re.I))

        results.extend(self._us_addresses(text))

        if address_column and (STREET_CUE_RE.search(text) or PIN_RE.search(text)):
            start = 0
            label = ADDRESS_LABEL_RE.match(text)
            if label:
                start = label.end()
            if "\n" in text:
                line_matches = list(re.finditer(r"[^\n]+", text))
                first_address_line = next(
                    (
                        line for line in line_matches
                        if STREET_CUE_RE.search(line.group(0)) or PIN_RE.search(line.group(0))
                    ),
                    line_matches[0],
                )
                start = max(start, first_address_line.start())
            start, end = _trim_span(text, start, len(text))
            if end > start:
                results.append(self._make(text, start, end, 0.99, "address_column"))
                return results

        for label in ADDRESS_LABEL_RE.finditer(text):
            start = label.end()
            while start < len(text) and text[start] in " \t:.-":
                start += 1
            limit = min(len(text), start + 500)
            stop = CONTACT_STOP_RE.search(text, start, limit)
            end = stop.start() if stop else limit
            candidate = text[start:end]
            pin = PIN_RE.search(candidate)
            country_matches = list(INDIA_RE.finditer(candidate))
            country = country_matches[-1] if country_matches else None
            street = STREET_CUE_RE.search(candidate)
            if street and (pin or country):
                if country:
                    end = start + country.end()
                elif pin:
                    end = start + pin.end()
                start, end = _trim_span(text, start, end)
                results.append(self._make(text, start, end, 0.97, "address_label"))

        # Compact contact-directory rows sometimes omit a country or a PIN.
        street = STREET_CUE_RE.search(text)
        pin = PIN_RE.search(text)
        contact_stop = CONTACT_STOP_RE.search(text)
        if "\n" not in text and not results and street and pin and contact_stop and street.start() < pin.start() < contact_stop.start():
            start, end = _trim_span(text, address_floor, contact_stop.start())
            results.append(self._make(text, start, end, 0.90, "contact_address"))
        elif "\n" not in text and not results and street and INDIA_RE.search(text):
            # An unlabelled address must itself look like an address record.  A
            # previous broad search for ``Unit <number>`` anywhere in the text
            # incorrectly consumed long business prose mentioning facilities
            # such as "Unit 2 in Chakan, Pune in Maharashtra".
            candidate_start = address_floor
            candidate = text[candidate_start:].lstrip()
            leading_space = len(text[candidate_start:]) - len(candidate)
            address_shape = re.match(
                r"(?:unit|flat|plot|house)\s*(?:no\.?)?\s*[A-Z]?-?\d[\w/-]*\b",
                candidate,
                re.I,
            )
            if len(candidate) <= 240 and address_shape:
                start, end = _trim_span(
                    text, candidate_start + leading_space, len(text)
                )
                results.append(self._make(text, start, end, 0.88, "unpinned_address"))

        # Multi-line and standalone postal blocks. Choose the smallest line window
        # containing both a street cue and either a PIN or an Indian state/country.
        lines = list(re.finditer(r"[^\n]+", text))
        windows: list[tuple[int, int, int]] = []
        for left in range(len(lines)):
            for right in range(left, min(len(lines), left + 5)):
                start, end = max(lines[left].start(), address_floor), lines[right].end()
                candidate = text[start:end]
                if len(candidate) > 500:
                    break
                street_match = STREET_CUE_RE.search(candidate)
                pin_match = (PIN_RE.search(candidate, street_match.start()) if street_match else None) or (
                    PIN_OCR_RE.search(candidate, street_match.start()) if street_match else None
                )
                country_match = INDIA_RE.search(candidate, pin_match.end()) if pin_match else None
                if street_match and pin_match and country_match:
                    windows.append((right - left + 1, start, end))
                    break
        if windows:
            _line_count, start, end = min(windows, key=lambda item: (item[0], item[2] - item[1]))
            first_line = next((index for index, line in enumerate(lines) if line.start() <= start < line.end()), 0)
            extensions = 0
            while first_line > 0 and extensions < 3:
                previous = lines[first_line - 1]
                previous_text = previous.group(0).strip()
                address_marker = re.search(
                    r"\b(?:(?:Gat|Plot|Flat|Unit|House)\s*(?:No\.?)?\s*[A-Z]?-?\d|[A-Z]-\d{2,})",
                    previous_text,
                    re.I,
                )
                if address_marker:
                    start = previous.start() + address_marker.start()
                    first_line -= 1
                    extensions += 1
                    continue
                contact = CONTACT_STOP_RE.search("\n" + previous_text)
                if contact:
                    street_tail = STREET_CUE_RE.search(previous_text, max(0, contact.end() - 1))
                    if street_tail:
                        start = previous.start() + street_tail.start()
                        first_line -= 1
                        extensions += 1
                        continue
                if (
                    len(previous_text) <= 240
                    and (STREET_CUE_RE.search(previous_text) or re.search(r"\d", previous_text))
                    and not ADDRESS_LABEL_RE.search(previous_text)
                    and not contact
                    and not re.search(r"\b(?:Bank|Limited|LLP|Associates|Company|Registrar|SEBI)\b", previous_text, re.I)
                ):
                    start = max(previous.start(), address_floor)
                    first_line -= 1
                    extensions += 1
                    continue
                if re.search(r"\bBank\b", previous_text, re.I) and "," in previous_text:
                    comma = previous_text.index(",") + 1
                    tail = previous_text[comma:]
                    if STREET_CUE_RE.search(tail) and re.search(r"\d", tail):
                        start = previous.start() + comma
                        first_line -= 1
                        extensions += 1
                        continue
                break
            inline_prefix = ORG_PREFIX_RE.match(text[start:end])
            if inline_prefix:
                start += inline_prefix.end()
            start, end = _trim_span(text, start, end)
            results.append(self._make(text, start, end, 0.91, "india_address"))
        return self._deduplicate(results)

    def _us_addresses(self, text: str) -> list[Span]:
        results: list[Span] = []
        standalone = US_STANDALONE_RE.fullmatch(text)
        if standalone:
            start, end = _trim_span(text, standalone.start(), standalone.end())
            results.append(self._make(text, start, end, 0.91, "us_street_line", country="US"))

        for zipcode in US_ZIP_RE.finditer(text):
            lookback_start = max(0, zipcode.start() - 120)
            prefix = text[lookback_start : zipcode.start()]
            state_matches = [
                match for match in US_STATE_RE.finditer(prefix)
                if len(match.group(0)) > 2 or match.group(0).isupper()
            ]
            if not state_matches or zipcode.start() - (lookback_start + state_matches[-1].end()) > 12:
                continue

            hard_starts = [
                match.end()
                for match in re.finditer(r"[;|]|(?<![A-Z])\.\s+", prefix)
            ]
            bounded_start = lookback_start + (hard_starts[-1] if hard_starts else 0)
            bounded = text[bounded_start : zipcode.start()]
            openings = list(US_OPENING_RE.finditer(bounded))
            if openings:
                start = bounded_start + openings[-1].start()
            else:
                # ZIP-bearing city/state line is useful PII even when its
                # street line is stored in a separate Word paragraph.
                last_newline = text.rfind("\n", bounded_start, zipcode.start())
                start = last_newline + 1 if last_newline >= bounded_start else bounded_start
            start, end = _trim_span(text, start, zipcode.end())
            if end > start:
                results.append(self._make(text, start, end, 0.96, "us_address", country="US"))
        return results

    def _make(
        self,
        text: str,
        start: int,
        end: int,
        confidence: float,
        method: str,
        **metadata: object,
    ) -> Span:
        return Span(
            start, end, self.pii_type, text[start:end], "address", confidence,
            self.priority, {"method": method, **metadata},
        )

    @staticmethod
    def _deduplicate(spans: list[Span]) -> list[Span]:
        unique: dict[tuple[int, int], Span] = {}
        for span in spans:
            key = (span.start, span.end)
            if key not in unique or span.confidence > unique[key].confidence:
                unique[key] = span
        return list(unique.values())
