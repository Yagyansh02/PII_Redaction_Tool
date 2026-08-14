"""Deterministic, format-aware fake alternatives and audit mappings."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from random import Random
from urllib.parse import urlsplit

from .spans import Span


FALLBACK_FIRST = (
    "Aarav", "Aditi", "Arjun", "Diya", "Ishaan", "Kavya", "Meera", "Neha",
    "Rohan", "Saanvi", "Siddharth", "Tanvi", "Varun", "Zoya",
)
FALLBACK_LAST = (
    "Bose", "Desai", "Gupta", "Iyer", "Joshi", "Kapoor", "Mehta", "Nair",
    "Patel", "Rao", "Shah", "Sharma", "Verma",
)
COMPANY_WORDS = (
    "Aster", "Bluepeak", "Cedar", "Crestview", "Evergreen", "Horizon", "Meridian",
    "Northstar", "Riverstone", "Silverline", "Summit", "Vertex",
)
COMPANY_NOUNS = ("Advisors", "Capital", "Enterprises", "Industries", "Partners", "Ventures")
STREETS = ("Baner Road", "C.G. Road", "Linking Road", "M.G. Road", "Park Street", "Residency Road")
CITIES = (
    ("Pune", "Maharashtra", "411045"),
    ("Mumbai", "Maharashtra", "400053"),
    ("Ahmedabad", "Gujarat", "380015"),
    ("Bengaluru", "Karnataka", "560038"),
    ("Hyderabad", "Telangana", "500034"),
    ("Chennai", "Tamil Nadu", "600028"),
)
US_CITIES = (
    ("Redmond", "Washington", "98052"),
    ("Seattle", "Washington", "98101"),
    ("Austin", "Texas", "78701"),
    ("Boston", "Massachusetts", "02108"),
    ("Denver", "Colorado", "80202"),
    ("Louisville", "Kentucky", "40202"),
)
US_STREETS = ("Cedar Way", "Lakeview Avenue", "Market Street", "Pine Road", "Sunset Boulevard")


def _canonical(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _url_parts(value: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", value).strip().rstrip(".,;:)")
    parsed = urlsplit(compact if re.match(r"^[a-z]+://", compact, re.I) else f"https://{compact}")
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    labels = host.split(".")
    multi_label_suffixes = {"co.in", "co.uk"}
    suffix = ".".join(labels[-2:]) if len(labels) >= 2 else host
    if suffix in multi_label_suffixes and len(labels) >= 3:
        registrable = ".".join(labels[-3:])
    else:
        registrable = suffix
    tail = parsed.path or ""
    if parsed.query:
        tail += f"?{parsed.query}"
    if parsed.fragment:
        tail += f"#{parsed.fragment}"
    return registrable, tail


def _case_like(value: str, replacement: str) -> str:
    letters = "".join(char for char in value if char.isalpha())
    if letters and letters.isupper():
        return replacement.upper()
    if letters and letters.islower():
        return replacement.lower()
    return replacement


def _luhn_check_digit(prefix: str) -> str:
    for candidate in "0123456789":
        number = prefix + candidate
        total = 0
        parity = len(number) % 2
        for index, char in enumerate(number):
            digit = int(char)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        if total % 10 == 0:
            return candidate
    raise AssertionError("unreachable")


@dataclass(slots=True)
class MappingEntry:
    pii_type: str
    original: str
    replacement: str
    canonical: str

    def as_dict(self) -> dict[str, str]:
        return {
            "type": self.pii_type,
            "original": self.original,
            "replacement": self.replacement,
            "canonical": self.canonical,
        }


class SurrogateStore:
    def __init__(
        self, seed: int = 42, protected_person_surnames: set[str] | None = None
    ) -> None:
        self.seed = seed
        self.protected_person_surnames = {
            value.casefold() for value in (protected_person_surnames or set())
        }
        self.base_values: dict[tuple[str, str], str] = {}
        self.surface_values: dict[tuple[str, str, str, int | None], str] = {}
        self.entries: dict[tuple[str, str], MappingEntry] = {}
        self.person_token_aliases: dict[str, str] = {}
        self.url_surrogate_owners: dict[str, str] = {}
        try:
            from faker import Faker

            self.faker_class = Faker
        except ImportError:
            self.faker_class = None

    def _rng(self, pii_type: str, canonical: str, attempt: int = 0) -> Random:
        digest = hashlib.sha256(
            f"{self.seed}\0{pii_type}\0{canonical}\0{attempt}".encode()
        ).digest()
        return Random(int.from_bytes(digest[:16], "big"))

    def _faker(self, pii_type: str, canonical: str, attempt: int = 0):
        if self.faker_class is None:
            return None
        fake = self.faker_class("en_IN")
        digest = hashlib.sha256(
            f"{self.seed}\0{pii_type}\0{canonical}\0{attempt}".encode()
        ).digest()
        fake.seed_instance(int.from_bytes(digest[:8], "big"))
        return fake

    def replacement_for(self, span: Span) -> str:
        original = span.text
        canonical_value = str(span.metadata.get("canonical", original))
        if span.pii_type == "PHONE":
            canonical_value = re.sub(r"\D", "", canonical_value)
        elif span.pii_type == "URL":
            canonical_value, _tail = _url_parts(canonical_value)
        elif span.pii_type == "EMAIL":
            canonical_value = canonical_value.casefold()
        canonical_key = _canonical(canonical_value)
        segment_index = span.metadata.get("block_segment_index")
        surface_key = (
            span.pii_type,
            canonical_key,
            original,
            int(segment_index) if segment_index is not None else None,
        )
        if surface_key in self.surface_values:
            return self.surface_values[surface_key]
        base_key = (span.pii_type, canonical_key)
        if base_key not in self.base_values:
            self.base_values[base_key] = self._generate_unique_base(
                span.pii_type, canonical_value
            )
        base = self.base_values[base_key]
        if span.pii_type == "URL":
            surrogate_host = urlsplit(base).hostname or base
            owner = self.url_surrogate_owners.setdefault(surrogate_host.casefold(), canonical_key)
            assert owner == canonical_key, (
                f"URL surrogate collision: {canonical_key!r} and {owner!r} -> {surrogate_host!r}"
            )
        replacement = self._format_surface(
            span.pii_type, original, canonical_value, base, span.metadata
        )
        self.surface_values[surface_key] = replacement
        self.entries[(span.pii_type, original)] = MappingEntry(
            span.pii_type, original, replacement, canonical_value
        )
        return replacement

    def _generate_unique_base(self, pii_type: str, canonical: str) -> str:
        if pii_type == "URL":
            base = self._generate_base(pii_type, canonical)
            used_hosts = {
                (urlsplit(value).hostname or value).casefold()
                for (existing_type, _existing), value in self.base_values.items()
                if existing_type == "URL"
            }
            parsed = urlsplit(base)
            host = (parsed.hostname or base).casefold()
            if host not in used_hosts:
                return base
            stem = host.removesuffix(".example.com")
            disambiguator = 2
            while f"{stem}-{disambiguator}.example.com" in used_hosts:
                disambiguator += 1
            return f"https://{stem}-{disambiguator}.example.com"

        if pii_type == "POSTAL_ADDRESS":
            used_lines = {
                _canonical(line)
                for (existing_type, _existing), value in self.base_values.items()
                if existing_type == "POSTAL_ADDRESS"
                for line in value.splitlines()
                if re.search(r"\d", line)
            }
            for attempt in range(100):
                candidate = self._generate_base(pii_type, canonical, attempt)
                candidate_lines = {
                    _canonical(line)
                    for line in candidate.splitlines()
                    if re.search(r"\d", line)
                }
                if not candidate_lines & used_lines:
                    return candidate
            raise AssertionError("could not generate an injective postal-address surrogate")

        if pii_type == "EMAIL":
            used = {
                _canonical(value)
                for (existing_type, _existing), value in self.base_values.items()
                if existing_type == "EMAIL"
            }
            for attempt in range(100):
                candidate = self._generate_base(pii_type, canonical, attempt)
                if _canonical(candidate) not in used:
                    return candidate
            raise AssertionError("could not generate an injective e-mail surrogate")

        if pii_type not in {"PERSON", "COMPANY"}:
            return self._generate_base(pii_type, canonical)

        used_bases = {
            _canonical(value)
            for value in self.base_values.values()
        }
        used_person_tokens = {
            token.casefold()
            for (existing_type, _existing), value in self.base_values.items()
            if existing_type == "PERSON"
            for token in (value.split()[0], value.split()[-1])
        }
        used_company_stems = {
            value.split()[0].casefold()
            for (existing_type, _existing), value in self.base_values.items()
            if existing_type == "COMPANY" and value.split()
        }
        for attempt in range(100):
            candidate = self._generate_base(pii_type, canonical, attempt)
            if _canonical(candidate) in used_bases:
                continue
            if pii_type == "PERSON" and {
                candidate.split()[0].casefold(), candidate.split()[-1].casefold()
            } & used_person_tokens:
                continue
            if pii_type == "COMPANY":
                words = candidate.split()
                if words[0].casefold() in used_company_stems:
                    disambiguator = 2
                    while f"{words[0]}{disambiguator}".casefold() in used_company_stems:
                        disambiguator += 1
                    candidate = " ".join(
                        [f"{words[0]}{disambiguator}", *words[1:]]
                    )
            return candidate
        raise AssertionError(f"could not generate an injective {pii_type} surrogate")

    def _generate_base(self, pii_type: str, canonical: str, attempt: int = 0) -> str:
        normalized = _canonical(canonical)
        rng = self._rng(pii_type, normalized, attempt)
        fake = self._faker(pii_type, normalized, attempt)
        if pii_type == "PERSON":
            if fake is not None:
                for _attempt in range(64):
                    candidate = re.sub(r"\s+", " ", fake.name()).strip()
                    if candidate.split()[-1].casefold().strip(".'’") not in self.protected_person_surnames:
                        return candidate
            safe_last = [
                value for value in FALLBACK_LAST
                if value.casefold() not in self.protected_person_surnames
            ]
            if not safe_last:
                raise ValueError("no safe fallback person surnames remain")
            return f"{rng.choice(FALLBACK_FIRST)} {rng.choice(safe_last)}"
        if pii_type == "COMPANY":
            return f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_NOUNS)}"
        if pii_type == "POSTAL_ADDRESS":
            if re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", canonical) or re.search(
                r"\b(?:Washington|Kentucky|California|Texas|New York)\s+\d{5}", canonical,
                re.I,
            ):
                city, state, zipcode_seed = rng.choice(US_CITIES)
                zipcode = f"{zipcode_seed[:3]}{rng.randint(0, 99):02d}"
                if re.search(r"\b\d{5}-\d{4}\b", canonical):
                    zipcode = f"{zipcode}-{rng.randint(1000, 9999)}"
                if re.search(r"\bP\.?\s*O\.?\s+Box\b", canonical, re.I):
                    return f"P.O. Box {rng.randint(100000, 999999)}\n{city}, {state} {zipcode}"
                return f"{rng.randint(100, 9999)} {rng.choice(US_STREETS)}\n{city}, {state} {zipcode}"
            city, state, pin_seed = rng.choice(CITIES)
            pin = f"{pin_seed[:3]}{rng.randint(0, 999):03d}"
            return f"Flat {rng.randint(101, 1204)}, {rng.choice(COMPANY_WORDS)} Heights\n{rng.choice(STREETS)}, {city} – {pin}\n{state}, India"
        if pii_type == "EMAIL":
            local = canonical.partition("@")[0]
            components = [part for part in re.split(r"[^a-zA-Z]+", local) if part]
            aliases = [self.person_token_aliases.get(part.casefold()) for part in components]
            known_aliases = [alias for alias in aliases if alias]
            first = known_aliases[0] if known_aliases else rng.choice(FALLBACK_FIRST).lower()
            safe_last = [
                value for value in FALLBACK_LAST
                if value.casefold() not in self.protected_person_surnames
            ]
            if not safe_last:
                raise ValueError("no safe fallback e-mail surnames remain")
            last = known_aliases[-1] if len(known_aliases) > 1 else rng.choice(safe_last).lower()
            return f"{first}.{last}@example.com"
        if pii_type == "URL":
            return (
                f"https://{rng.choice(COMPANY_WORDS).lower()}-"
                f"{rng.choice(COMPANY_NOUNS).lower()}.example.com"
            )
        if pii_type == "DATE_OF_BIRTH":
            year = date.today().year - rng.randint(28, 67)
            return date(year, rng.randint(1, 12), rng.randint(1, 28)).isoformat()
        return ""

    def _format_surface(
        self,
        pii_type: str,
        original: str,
        canonical: str,
        base: str,
        metadata: dict[str, object] | None = None,
    ) -> str:
        rng = self._rng(pii_type, _canonical(canonical))
        if pii_type == "PERSON":
            fake_tokens = base.split()
            segment_index = (metadata or {}).get("block_segment_index")
            segment_count = (metadata or {}).get("block_segment_count")
            if segment_index is not None and segment_count is not None:
                index, count = int(segment_index), int(segment_count)
                left = round(index * len(fake_tokens) / count)
                right = round((index + 1) * len(fake_tokens) / count)
                value = " ".join(fake_tokens[left:right])
                return _case_like(original, value)
            original_tokens = HONORIFIC_RE.sub("", original).replace("’s", "").replace("'s", "").split()
            canonical_tokens = canonical.split()
            if len(original_tokens) == 1:
                value = fake_tokens[-1]
            elif len(original_tokens) == 2 and len(canonical_tokens) > 2:
                value = f"{fake_tokens[0]} {fake_tokens[-1]}"
            else:
                value = base
            possessive = "’s" if original.endswith("’s") else "'s" if original.endswith("'s") else ""
            original_name_tokens = [token.casefold().strip(".'’") for token in canonical.split()]
            fake_name_tokens = [token.casefold().strip(".'’") for token in base.split()]
            if original_name_tokens and fake_name_tokens:
                self.person_token_aliases[original_name_tokens[0]] = fake_name_tokens[0]
                self.person_token_aliases[original_name_tokens[-1]] = fake_name_tokens[-1]
            return _case_like(original, value) + possessive
        if pii_type == "COMPANY":
            suffix_match = re.search(
                r"\b(Private\s+Limited|Public\s+Limited|Pvt\.?\s+Ltd\.?|Limited|Ltd\.?|LLC|LLP|L\.L\.P\.?|"
                r"Inc\.?|Incorporated|Corporation|Corp\.?|Company|plc|S\.A\.|N\.V\.|GmbH|AG|Partners|"
                r"Partnerships|Trust|Bank\s+N\.A\.|Bank\s+Limited|Bank\s+of\s+India|Bank|N\.A\.|Associates|&\s+Co\.?)\s*$",
                canonical,
                re.I,
            )
            suffix = suffix_match.group(1) if suffix_match else "Limited"
            full = f"{base} {suffix}"
            if re.match(r"The\s+", canonical, re.I):
                full = f"The {full}"
            segment_index = (metadata or {}).get("block_segment_index")
            segment_count = (metadata or {}).get("block_segment_count")
            if segment_index is not None and segment_count is not None:
                index, count = int(segment_index), int(segment_count)
                if count == 2:
                    parts = full.split()
                    cut = max(1, len(parts) // 2)
                    value = " ".join(parts[:cut] if index == 0 else parts[cut:])
                else:
                    parts = full.split()
                    left = round(index * len(parts) / count)
                    right = round((index + 1) * len(parts) / count)
                    value = " ".join(parts[left:right])
                return _case_like(original, value)
            return _case_like(original, full)
        if pii_type == "POSTAL_ADDRESS":
            segment_index = (metadata or {}).get("block_segment_index")
            segment_count = (metadata or {}).get("block_segment_count")
            if segment_index is not None and segment_count is not None:
                index, count = int(segment_index), int(segment_count)
                lines = base.splitlines()
                if count == 1:
                    return ", ".join(lines)
                if index >= count - 1:
                    return ", ".join(lines[index:]) if index < len(lines) else ""
                return lines[index] if index < len(lines) else ""
            line_count = max(1, original.count("\n") + 1)
            lines = base.splitlines()
            if line_count == 1:
                return ", ".join(lines)
            if line_count == 2:
                if len(lines) == 2:
                    return "\n".join(lines)
                return f"{lines[0]}, {lines[1]}\n{lines[2]}"
            if line_count > 3:
                lines.extend([""] * (line_count - 3))
            return "\n".join(lines[:line_count])
        if pii_type == "PHONE":
            original_digits = re.sub(r"\D", "", original)
            generated = [str(rng.randint(0, 9)) for _ in original_digits]
            if original_digits.startswith("91") and original.lstrip().startswith("+"):
                generated[:2] = ["9", "1"]
            national_start = 2 if original_digits.startswith("91") and original.lstrip().startswith("+") else 0
            if national_start < len(generated) and original_digits[national_start] == "0":
                generated[national_start] = "0"
            elif len(generated) - national_start == 10:
                generated[national_start] = str(rng.randint(6, 9))
            # Preserve recognizable Indian landline area codes (20, 22, 011,
            # etc.) while changing the subscriber number.
            national = original_digits[national_start:]
            if national and national[0] in "012345" and len(national) >= 9:
                area_length = 3 if national.startswith("0") else 2
                generated[national_start : national_start + area_length] = list(national[:area_length])
            return self._replace_digits(original, "".join(generated))
        if pii_type == "SSN":
            area = rng.choice([number for number in range(1, 900) if number != 666])
            return f"{area:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"
        if pii_type == "CREDIT_CARD":
            original_digits = re.sub(r"\D", "", original)
            first = original_digits[0]
            prefix = first + "".join(str(rng.randint(0, 9)) for _ in range(len(original_digits) - 2))
            replacement_digits = prefix + _luhn_check_digit(prefix)
            return self._replace_digits(original, replacement_digits)
        if pii_type == "IP_ADDRESS":
            if ":" in original:
                groups = [f"{rng.randint(0, 65535):x}" for _ in range(8)]
                return ":".join(groups)
            return f"203.0.113.{rng.randint(1, 254)}"
        if pii_type == "DATE_OF_BIRTH":
            generated = date.fromisoformat(base)
            if re.search(r"[A-Za-z]", original):
                month = generated.strftime("%B")
                if re.match(r"[A-Za-z]", original):
                    return f"{month} {generated.day}, {generated.year}"
                return f"{generated.day} {month} {generated.year}"
            separator = next((char for char in original if char in "/.-"), "/")
            return f"{generated.day:02d}{separator}{generated.month:02d}{separator}{generated.year}"
        if pii_type == "URL":
            _domain, tail = _url_parts(original)
            return base.rstrip("/") + tail
        if pii_type == "EMAIL":
            return base
        if pii_type == "PAN":
            return "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5)) + f"{rng.randint(0, 9999):04d}" + rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        if pii_type == "AADHAAR":
            return self._fake_verhoeff(original, rng)
        if pii_type == "DIN":
            return f"{rng.randint(1, 99999999):08d}"
        if pii_type == "CIN":
            match = re.fullmatch(r"([LU])(\d{5})([A-Z]{2})(\d{4})(PLC|PTC)(\d{6})", original.upper())
            if match:
                listing, _industry, _state, _year, ownership, _serial = match.groups()
                state = rng.choice(("MH", "GJ", "KA", "TN", "DL", "TG"))
                year = rng.randint(1980, date.today().year)
                return f"{listing}{rng.randint(0, 99999):05d}{state}{year:04d}{ownership}{rng.randint(0, 999999):06d}"
            return self._replace_alnum(original, rng, preserve_prefix=1)
        if pii_type == "SEBI_REG_NO":
            return self._replace_alnum(original, rng, preserve_prefix=2)
        if pii_type == "IFSC":
            return "FAKE0" + "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(6))
        if pii_type == "BANK_ACCOUNT":
            return self._replace_digits(original, "".join(str(rng.randint(0, 9)) for _ in re.sub(r"\D", "", original)))
        if pii_type == "GSTIN":
            return self._replace_alnum(original, rng, preserve_prefix=0)
        if pii_type == "PASSPORT":
            return rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + str(rng.randint(1000000, 9999999))
        return base or _case_like(original, "Synthetic Value")

    @staticmethod
    def _replace_digits(template: str, replacement_digits: str) -> str:
        iterator = iter(replacement_digits)
        return "".join(next(iterator) if char.isdigit() else char for char in template)

    @staticmethod
    def _replace_alnum(template: str, rng: Random, preserve_prefix: int = 0) -> str:
        output: list[str] = []
        alnum_index = 0
        for char in template:
            if not char.isalnum():
                output.append(char)
                continue
            if alnum_index < preserve_prefix:
                output.append(char)
            elif char.isdigit():
                output.append(str(rng.randint(0, 9)))
            else:
                output.append(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
            alnum_index += 1
        return "".join(output)

    @staticmethod
    def _fake_verhoeff(template: str, rng: Random) -> str:
        from .detectors.structured import _VERHOEFF_D, _VERHOEFF_P

        inverse = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)
        body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
        checksum = 0
        for index, char in enumerate(reversed(body)):
            checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[(index + 1) % 8][int(char)]]
        number = body + str(inverse[checksum])
        return SurrogateStore._replace_digits(template, number)

    def write(self, path: str | Path, metadata: dict[str, object] | None = None) -> Path:
        self.assert_globally_injective()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": {"seed": self.seed, **(metadata or {})},
            "mappings": [entry.as_dict() for entry in sorted(self.entries.values(), key=lambda item: (item.pii_type, item.original.casefold()))],
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    def assert_url_injective(self) -> None:
        reverse: dict[str, str] = {}
        for (pii_type, canonical), surrogate in self.base_values.items():
            if pii_type != "URL":
                continue
            host = (urlsplit(surrogate).hostname or surrogate).casefold()
            incumbent = reverse.setdefault(host, canonical)
            assert incumbent == canonical, (
                f"URL surrogate collision: {canonical!r} and {incumbent!r} -> {host!r}"
            )

    def assert_globally_injective(self) -> None:
        """Require distinct canonical entities to have distinct final surrogates."""

        self.assert_url_injective()
        reverse: dict[str, tuple[str, str]] = {}
        for entry in self.entries.values():
            source = (entry.pii_type, _canonical(entry.canonical))
            surrogate = _canonical(entry.replacement)
            incumbent = reverse.setdefault(surrogate, source)
            assert incumbent == source, (
                "global surrogate collision: "
                f"{source[0]} {source[1]!r} and {incumbent[0]} {incumbent[1]!r} "
                f"-> {entry.replacement!r}"
            )


HONORIFIC_RE = re.compile(r"^(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Smt\.?)\s+", re.I)
