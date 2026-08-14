"""High-precision regex detectors with semantic validation."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

from ..config import TYPE_PRIORITIES
from ..spans import Span
from .base import DetectionContext, Detector


EMAIL_RE = re.compile(
    r"(?<![\w.+-])(?:[A-Z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Z0-9!#$%&'*+/=?^_`{|}~-]+)*)"
    r"@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}(?![\w.-])",
    re.IGNORECASE,
)
URL_TLD = r"(?:com|org|net|edu|gov|io|ai|biz|info|us|uk|in|co\.in|co\.uk)"
URL_HOST = rf"(?:[A-Z0-9](?:[A-Z0-9-]{{0,61}}[A-Z0-9])?\.)+{URL_TLD}(?![A-Za-z])"
URL_RE = re.compile(
    rf"(?<![@\w])(?:(?:https?://|www\.){URL_HOST}"
    rf"(?:/[A-Z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?|"
    rf"{URL_HOST}/[A-Z0-9._~:/?#\[\]@!$&'()*+,;=%-]+)",
    re.IGNORECASE,
)
SPLIT_URL_RE = re.compile(
    rf"(?<![@\w])(?:https?://|www\.)"
    rf"(?:[A-Z0-9](?:[A-Z0-9-]{{0,61}}[A-Z0-9])?\.)+\s+{URL_TLD}(?![A-Za-z])"
    rf"(?:/[A-Z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
    re.IGNORECASE,
)
PUBLIC_URL_HOSTS = (
    "sec.gov",
    "sebi.gov.in",
    "bseindia.com",
    "nseindia.com",
    "rbi.org.in",
    "mca.gov.in",
    "cdslindia.com",
    "nsdl.co.in",
    "fbil.org.in",
    "oanda.com",
)
SSN_RE = re.compile(r"(?<!\d)(\d{3})-(\d{2})-(\d{4})(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
IP_TOKEN_RE = re.compile(r"(?<![\w:])(?:[0-9A-F]{0,4}:){2,7}[0-9A-F]{0,4}(?![\w:])|(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", re.I)
PAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])")
AADHAAR_RE = re.compile(r"(?<!\d)(?:[2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)")
CIN_RE = re.compile(r"(?<![A-Z0-9])[LU]\d{5}[A-Z]{2}\d{4}(?:PLC|PTC)\d{6}(?![A-Z0-9])", re.I)
SEBI_RE = re.compile(r"(?<![A-Z0-9])IN[A-Z]{1,3}\d{6,12}(?![A-Z0-9])", re.I)
IFSC_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Z0-9])")
GSTIN_RE = re.compile(r"(?<![A-Z0-9])\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z](?![A-Z0-9])")
PASSPORT_RE = re.compile(r"(?<![A-Z0-9])[A-Z][1-9]\d{6}(?![A-Z0-9])")

COUNTRY_PHONE_RE = re.compile(
    r"(?<![\w\d])\+\s*91(?:[()\s.-]*\d){8,12}(?!\d)", re.I
)
LANDLINE_RE = re.compile(
    r"(?<!\d)(?:\(?0\d{2,4}\)?[\s.-]+\d{3,8}(?:[\s.-]+\d{3,5})?)(?!\d)"
)
MOBILE_RE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
PHONE_LABEL_RE = re.compile(r"(?:telephone|mobile|phone|tel\.?|contact)\s*(?:no\.?|number)?\s*[:.-]?\s*$", re.I)
NANP_PHONE_RE = re.compile(
    r"(?<!\d)(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)
NANP_CONTEXT_RE = re.compile(
    r"\b(?:tel|telephone|phone|call(?:ing)?|toll[- ]?free|fax|contact)\b[^\d]{0,40}$",
    re.I,
)
DIN_RE = re.compile(r"(?<!\d)\d{8}(?!\d)")
BANK_RE = re.compile(r"(?<![A-Z0-9])\d(?:[ -]?\d){8,17}(?![A-Z0-9])", re.I)


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def luhn_valid(value: str) -> bool:
    number = digits(value)
    if not 13 <= len(number) <= 19 or len(set(number)) == 1:
        return False
    total = 0
    parity = len(number) % 2
    for index, char in enumerate(number):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def known_card_issuer(value: str) -> bool:
    number = digits(value)
    first_two = int(number[:2])
    first_four = int(number[:4])
    first_six = int(number[:6])
    return any(
        (
            number.startswith("4"),  # Visa
            51 <= first_two <= 55 or 2221 <= first_four <= 2720,  # Mastercard
            number.startswith(("34", "37")),  # Amex
            number.startswith("6011") or number.startswith("65") or 644 <= int(number[:3]) <= 649,
            3528 <= first_four <= 3589,  # JCB
            number.startswith(("36", "38", "39")) or 300 <= int(number[:3]) <= 305,
            60 <= first_two <= 65 or 81 <= first_two <= 89,  # RuPay ranges
            508000 <= first_six <= 508999,
        )
    )


def ssn_valid(match: re.Match[str]) -> bool:
    area, group, serial = match.groups()
    return area not in {"000", "666"} and int(area) < 900 and group != "00" and serial != "0000"


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(value: str) -> bool:
    number = digits(value)
    checksum = 0
    for index, char in enumerate(reversed(number)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[index % 8][int(char)]]
    return checksum == 0


def _context_window(text: str, start: int, size: int = 48) -> str:
    return text[max(0, start - size) : start]


def _iter_valid(
    pattern: re.Pattern[str], text: str, validator: Callable[[re.Match[str]], bool] | None = None
) -> Iterator[re.Match[str]]:
    for match in pattern.finditer(text):
        if validator is None or validator(match):
            yield match


def _url_host(value: str) -> str:
    compact = re.sub(r"\s+", "", value).strip(".,;:)")
    parsed = urlsplit(compact if re.match(r"^[a-z]+://", compact, re.I) else f"https://{compact}")
    host = (parsed.hostname or "").casefold()
    return host.removeprefix("www.")


class StructuredDetector(Detector):
    """Detect all structured PII types in one pass."""

    pii_type = "STRUCTURED"
    priority = 100

    def __init__(self, enabled_types: set[str] | None = None) -> None:
        self.enabled_types = enabled_types

    def _enabled(self, pii_type: str) -> bool:
        return self.enabled_types is None or pii_type in self.enabled_types

    @staticmethod
    def _span(match: re.Match[str], pii_type: str, confidence: float = 0.99, **metadata: object) -> Span:
        return Span(
            match.start(),
            match.end(),
            pii_type,
            match.group(0),
            "structured",
            confidence,
            TYPE_PRIORITIES[pii_type],
            dict(metadata),
        )

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        found: list[Span] = []

        simple_patterns = (
            ("EMAIL", EMAIL_RE),
            ("PAN", PAN_RE),
            ("CIN", CIN_RE),
            ("SEBI_REG_NO", SEBI_RE),
            ("IFSC", IFSC_RE),
            ("GSTIN", GSTIN_RE),
        )
        for pii_type, pattern in simple_patterns:
            if self._enabled(pii_type):
                found.extend(self._span(match, pii_type) for match in pattern.finditer(text))

        if self._enabled("URL"):
            url_matches = sorted(
                [*URL_RE.finditer(text), *SPLIT_URL_RE.finditer(text)],
                key=lambda item: (item.start(), -item.end()),
            )
            seen_urls: set[tuple[int, int]] = set()
            for match in url_matches:
                start, end = match.span()
                while end > start and text[end - 1] in ".,;:)":
                    end -= 1
                if end <= start or (start, end) in seen_urls:
                    continue
                host = _url_host(text[start:end])
                if any(host == public or host.endswith(f".{public}") for public in PUBLIC_URL_HOSTS):
                    continue
                seen_urls.add((start, end))
                found.append(
                    Span(
                        start, end, "URL", text[start:end], "structured", 0.99,
                        TYPE_PRIORITIES["URL"], {"host": host},
                    )
                )

        if self._enabled("SSN"):
            found.extend(self._span(match, "SSN") for match in _iter_valid(SSN_RE, text, ssn_valid))

        if self._enabled("CREDIT_CARD"):
            for match in CARD_RE.finditer(text):
                value = match.group(0).strip()
                leading = len(match.group(0)) - len(match.group(0).lstrip())
                if luhn_valid(value) and known_card_issuer(value):
                    adjusted = re.match(re.escape(value), text[match.start() + leading :])
                    if adjusted:
                        start = match.start() + leading
                        found.append(
                            Span(start, start + len(value), "CREDIT_CARD", value, "structured", 0.995, TYPE_PRIORITIES["CREDIT_CARD"])
                        )

        if self._enabled("IP_ADDRESS"):
            for match in IP_TOKEN_RE.finditer(text):
                value = match.group(0)
                if not value or value == "::":
                    continue
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    continue
                found.append(self._span(match, "IP_ADDRESS"))

        if self._enabled("AADHAAR"):
            found.extend(
                self._span(match, "AADHAAR")
                for match in AADHAAR_RE.finditer(text)
                if verhoeff_valid(match.group(0))
            )

        if self._enabled("PHONE"):
            found.extend(self._phone_spans(text))

        if self._enabled("DIN"):
            for match in DIN_RE.finditer(text):
                before = _context_window(text, match.start(), 36)
                table_headers = " ".join((context.metadata.get("table_headers", []) if context else []))
                if re.search(r"\bDIN\b", before, re.I) or re.search(r"\bDIN\b", table_headers, re.I):
                    found.append(self._span(match, "DIN", 0.98))

        if self._enabled("PASSPORT"):
            for match in PASSPORT_RE.finditer(text):
                if re.search(r"passport(?:\s+no\.?|\s+number)?\s*[:.-]?\s*$", _context_window(text, match.start()), re.I):
                    found.append(self._span(match, "PASSPORT", 0.98))

        if self._enabled("BANK_ACCOUNT"):
            for match in BANK_RE.finditer(text):
                before = _context_window(text, match.start(), 64)
                if re.search(
                    r"(?:(?:\bbank\s+)?\ba/?c\b|\baccount(?:\s+no\.?|\s+number)?)\s*[:.-]?\s*$",
                    before,
                    re.I,
                ):
                    value = match.group(0)
                    if 9 <= len(digits(value)) <= 18:
                        found.append(self._span(match, "BANK_ACCOUNT", 0.97))

        return found

    def _phone_spans(self, text: str) -> list[Span]:
        matches: list[Span] = []
        for match in COUNTRY_PHONE_RE.finditer(text):
            value_digits = digits(match.group(0))
            national = value_digits[2:] if value_digits.startswith("91") else value_digits
            if 8 <= len(national) <= 11:
                matches.append(self._span(match, "PHONE"))
        for match in LANDLINE_RE.finditer(text):
            value_digits = digits(match.group(0))
            if 9 <= len(value_digits) <= 12:
                matches.append(self._span(match, "PHONE", 0.985))
        for match in MOBILE_RE.finditer(text):
            before = _context_window(text, match.start())
            if PHONE_LABEL_RE.search(before):
                matches.append(self._span(match, "PHONE", 0.98))
        for match in NANP_PHONE_RE.finditer(text):
            value = match.group(0)
            parenthesized = value.startswith("(") and ")" in value[:5]
            separated = bool(re.search(r"[-.]", value))
            before = _context_window(text, match.start(), 40)
            if parenthesized or separated or NANP_CONTEXT_RE.search(before):
                matches.append(self._span(match, "PHONE", 0.99))
        return matches
