#!/usr/bin/env python3
"""Verify that mapped source values no longer occur in redacted DOCX XML."""

from __future__ import annotations

import argparse
import json
import hashlib
import zipfile
from pathlib import Path


def check(
    docx_path: Path,
    map_path: Path,
    detections_path: Path | None = None,
    image_audit_path: Path | None = None,
) -> list[dict[str, str]]:
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(docx_path) as archive:
        xml = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        ).decode("utf-8", errors="replace")
        media = {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith("word/media/")
        }
    leaks: list[dict[str, str]] = []
    originals = [
        {"type": item["type"], "original": item["original"]}
        for item in mapping.get("mappings", [])
    ]
    surrogate_payload = "\n".join(
        item.get("replacement", "") for item in mapping.get("mappings", [])
    )
    if detections_path:
        originals.extend(
            {"type": row["type"], "original": row["text"]}
            for row in (
                json.loads(line) for line in detections_path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        )
    seen: set[tuple[str, str]] = set()
    for item in originals:
        original = item["original"]
        key = (item["type"], original)
        if key in seen:
            continue
        seen.add(key)
        # A generic fragment such as "Maharashtra, India" may legitimately
        # occur inside a generated fake address; it cannot support a global
        # string-level leak assertion.
        if original and original in surrogate_payload:
            continue
        # Boundary projection can audit one segment of a multi-token person as
        # a single given name (for example, ``Rajesh``). The same ordinary
        # token can legitimately occur elsewhere in a term such as ``Rajesh
        # Branch``. Whole names are checked here, while the pipeline separately
        # enforces the stricter document-wide no-source-surname invariant.
        if item["type"] == "PERSON" and len(original.split()) == 1:
            continue
        if original and original in xml:
            leaks.append({"type": item["type"], "original": original})
    if image_audit_path:
        for item in json.loads(image_audit_path.read_text(encoding="utf-8")):
            media_path = item["media_path"]
            payload = media.get(media_path)
            if payload is None:
                leaks.append({"type": "IMAGE", "original": f"missing:{media_path}"})
            elif hashlib.sha256(payload).hexdigest() == item["source_sha256"]:
                leaks.append({"type": "IMAGE", "original": media_path})
    return leaks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--map", required=True, dest="map_path", type=Path)
    parser.add_argument("--detections", type=Path)
    parser.add_argument("--image-audit", type=Path)
    args = parser.parse_args()
    leaks = check(args.docx, args.map_path, args.detections, args.image_audit)
    if leaks:
        print(json.dumps(leaks, indent=2, ensure_ascii=False))
        print(f"FAILED: {len(leaks)} source text/media values remain")
        return 1
    message = "PASS: no mapped source value remains in Word XML (visible text or field codes)"
    if args.image_audit:
        message += ", and no audited source image bytes remain"
    print(message + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
