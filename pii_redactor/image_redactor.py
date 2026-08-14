"""Deterministic inspection and removal of sensitive DOCX raster images."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .docx_io import DocxPackage, ImageReference


IDENTITY_MARKERS = re.compile(
    r"\b(?:income\s+tax\s+department|permanent\s+account\s+number|"
    r"unique\s+identification\s+authority|aadhaar|aadhar|father'?s\s+name)\b",
    re.I,
)
PAN_SHAPE = re.compile(r"\b[A-Z]{5}\s*\d{4}\s*[A-Z]\b", re.I)
AADHAAR_SHAPE = re.compile(r"(?<!\d)[2-9]\d{3}\s+\d{4}\s+\d{4}(?!\d)")
GENERIC_COMPANY_TOKENS = {
    "company", "corporation", "private", "public", "limited", "ltd", "llp",
    "bank", "india", "international", "industries", "management", "securities",
    "services", "financial", "finance", "capital", "wealth", "group", "trust",
}


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


@dataclass(frozen=True, slots=True)
class ImageRedaction:
    media_path: str
    reasons: tuple[str, ...]
    source_sha256: str
    occurrences: int

    def as_dict(self) -> dict[str, object]:
        return {
            "media_path": self.media_path,
            "reasons": list(self.reasons),
            "source_sha256": self.source_sha256,
            "occurrences": self.occurrences,
        }


@dataclass(slots=True)
class _Inspection:
    media_path: str
    payload: bytes
    references: list[ImageReference]
    ocr_text: str = ""
    perceptual_hash: int | None = None
    reasons: set[str] | None = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = set()


class ImageInspectionError(ValueError):
    """Raised when the configured privacy policy cannot inspect images safely."""


class ImageRedactor:
    """Classify sensitive images with OCR/context and replace them in place.

    The image dimensions and media format are retained, so Word drawing boxes
    and page layout remain stable.  OCR text and QR payloads are never written
    to the audit log.
    """

    def __init__(self, policy: str = "sensitive") -> None:
        if policy not in {"sensitive", "all", "none"}:
            raise ValueError("image policy must be one of: sensitive, all, none")
        self.policy = policy

    def inspect(
        self,
        package: DocxPackage,
        sensitive_values: Iterable[tuple[str, str]],
    ) -> list[ImageRedaction]:
        if self.policy == "none":
            return []
        references_by_path: dict[str, list[ImageReference]] = defaultdict(list)
        for reference in package.image_references():
            references_by_path[reference.media_path].append(reference)
        if not references_by_path:
            return []

        company_values = {
            value.strip() for pii_type, value in sensitive_values
            if pii_type == "COMPANY" and value.strip()
        }
        other_values = {
            value.strip() for pii_type, value in sensitive_values
            if pii_type != "COMPANY" and len(_normalize(value)) >= 5
        }
        inspections = [
            _Inspection(media_path, package.entries[media_path], references)
            for media_path, references in sorted(references_by_path.items())
        ]

        if self.policy == "all":
            for inspection in inspections:
                inspection.reasons.add("image-policy-all")
        else:
            tesseract = shutil.which("tesseract")
            zbar = shutil.which("zbarimg")
            missing = [name for name, executable in (("tesseract", tesseract), ("zbarimg", zbar)) if not executable]
            if missing:
                raise ImageInspectionError(
                    "sensitive image inspection requires the system commands: "
                    + ", ".join(missing)
                    + "; use --image-policy all to redact every image or --image-policy none to opt out"
                )
            with tempfile.TemporaryDirectory(prefix="pii-image-scan-") as temporary:
                temp_dir = Path(temporary)
                for index, inspection in enumerate(inspections):
                    suffix = Path(inspection.media_path).suffix or ".img"
                    image_path = temp_dir / f"image-{index:04d}{suffix}"
                    image_path.write_bytes(inspection.payload)
                    inspection.ocr_text = self._ocr(tesseract, image_path)
                    inspection.perceptual_hash = self._dhash(inspection.payload)
                    if self._has_qr(zbar, image_path):
                        inspection.reasons.add("qr-code")
                    if self._is_identity_document(inspection.ocr_text):
                        inspection.reasons.add("identity-document")
                    self._match_sensitive_text(
                        inspection, company_values, other_values
                    )
            self._propagate_similar_party_logos(inspections)

        return [
            ImageRedaction(
                inspection.media_path,
                tuple(sorted(inspection.reasons)),
                hashlib.sha256(inspection.payload).hexdigest(),
                len(inspection.references),
            )
            for inspection in inspections
            if inspection.reasons
        ]

    @staticmethod
    def apply(package: DocxPackage, redactions: Iterable[ImageRedaction]) -> None:
        for redaction in redactions:
            payload = package.entries.get(redaction.media_path)
            if payload is None:
                raise ImageInspectionError(f"missing media entry: {redaction.media_path}")
            package.entries[redaction.media_path] = ImageRedactor._placeholder(
                payload, Path(redaction.media_path).suffix
            )

    @staticmethod
    def write_audit(path: str | Path, redactions: Iterable[ImageRedaction]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [redaction.as_dict() for redaction in redactions]
        target.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        return target

    @staticmethod
    def _ocr(tesseract: str, image_path: Path) -> str:
        outputs: list[str] = []
        for mode in ("6", "11"):
            try:
                completed = subprocess.run(
                    [tesseract, str(image_path), "stdout", "--psm", mode],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
            except subprocess.TimeoutExpired as error:
                raise ImageInspectionError(f"OCR timed out for {image_path.name}") from error
            if completed.returncode == 0:
                outputs.append(completed.stdout)
        return "\n".join(outputs)

    @staticmethod
    def _has_qr(zbar: str, image_path: Path) -> bool:
        try:
            completed = subprocess.run(
                [zbar, "--quiet", "--raw", str(image_path)],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired as error:
            raise ImageInspectionError(f"QR inspection timed out for {image_path.name}") from error
        return completed.returncode == 0 and bool(completed.stdout.strip())

    @staticmethod
    def _is_identity_document(ocr_text: str) -> bool:
        normalized = re.sub(r"\s+", " ", ocr_text)
        return bool(
            IDENTITY_MARKERS.search(normalized)
            or PAN_SHAPE.search(normalized)
            or AADHAAR_SHAPE.search(normalized)
        )

    @staticmethod
    def _match_sensitive_text(
        inspection: _Inspection,
        company_values: set[str],
        other_values: set[str],
    ) -> None:
        ocr_normalized = _normalize(inspection.ocr_text)
        ocr_tokens = _tokens(inspection.ocr_text)
        context = _normalize(
            "\n".join(
                f"{reference.nearby_text}\n{reference.description}"
                for reference in inspection.references
            )
        )
        for company in sorted(company_values):
            normalized_company = _normalize(company)
            distinctive = {
                token for token in _tokens(company)
                if len(token) >= 4 and token not in GENERIC_COMPANY_TOKENS
            }
            if normalized_company and normalized_company in context:
                inspection.reasons.add(f"party-context:{company}")
            elif distinctive & ocr_tokens:
                inspection.reasons.add(f"party-image:{company}")
        for value in sorted(other_values):
            normalized_value = _normalize(value)
            if normalized_value and normalized_value in ocr_normalized:
                inspection.reasons.add("detected-pii-in-image")
                break

    @staticmethod
    def _dhash(payload: bytes) -> int | None:
        try:
            with Image.open(io.BytesIO(payload)) as source:
                grayscale = source.convert("L").resize((9, 8))
                pixels = list(grayscale.getdata())
        except (UnidentifiedImageError, OSError):
            return None
        value = 0
        bit = 0
        for row_index in range(8):
            row = pixels[row_index * 9 : (row_index + 1) * 9]
            for left, right in zip(row, row[1:]):
                if left > right:
                    value |= 1 << bit
                bit += 1
        return value

    @staticmethod
    def _propagate_similar_party_logos(inspections: list[_Inspection]) -> None:
        party_hashes = [
            inspection.perceptual_hash
            for inspection in inspections
            if inspection.perceptual_hash is not None
            and any(reason.startswith("party-") for reason in inspection.reasons)
        ]
        for inspection in inspections:
            if inspection.reasons or inspection.perceptual_hash is None:
                continue
            if any((inspection.perceptual_hash ^ known).bit_count() <= 8 for known in party_hashes):
                inspection.reasons.add("similar-to-redacted-party-logo")

    @staticmethod
    def _placeholder(payload: bytes, suffix: str) -> bytes:
        try:
            with Image.open(io.BytesIO(payload)) as source:
                width, height = source.size
                source_format = source.format
        except (UnidentifiedImageError, OSError):
            width, height = 640, 240
            source_format = None
        width, height = max(width, 1), max(height, 1)
        image = Image.new("RGB", (width, height), (232, 232, 232))
        draw = ImageDraw.Draw(image)
        border = max(1, min(width, height) // 40)
        draw.rectangle(
            (border, border, width - border - 1, height - border - 1),
            outline=(80, 80, 80),
            width=border,
        )
        label = (
            "REDACTED"
            if width < 260 or height < 150
            else "REDACTED\nCONFIDENTIAL IMAGE"
        )
        font_size = max(10, min(56, min(width, height) // 7))
        while True:
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
            box = draw.multiline_textbbox((0, 0), label, font=font, align="center", spacing=4)
            if (
                (box[2] - box[0]) <= width * 0.88
                and (box[3] - box[1]) <= height * 0.80
            ) or font_size <= 8:
                break
            font_size -= 1
        text_width, text_height = box[2] - box[0], box[3] - box[1]
        draw.multiline_text(
            ((width - text_width) / 2, (height - text_height) / 2),
            label,
            fill=(55, 55, 55),
            font=font,
            align="center",
            spacing=4,
        )
        output = io.BytesIO()
        output_format = source_format or {
            ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF",
            ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF",
        }.get(suffix.casefold(), "PNG")
        if output_format.upper() in {"JPEG", "JPG"}:
            image.save(output, format="JPEG", quality=90)
        else:
            image.save(output, format=output_format)
        return output.getvalue()
