from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from evaluation.build_fixture import build_fixture
from pii_redactor.docx_io import DocxPackage
from web.app import app


def test_home_and_health_have_security_headers() -> None:
    with TestClient(app) as client:
        home = client.get("/")
        health = client.get("/health")

    assert home.status_code == 200
    assert "DOCX PII Redactor" in home.text
    assert home.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
    assert health.json()["status"] == "ok"


def test_redaction_upload_returns_a_valid_docx(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    build_fixture(source, tmp_path / "gold.jsonl", tmp_path / "corpus.jsonl")

    with TestClient(app) as client, source.open("rb") as handle:
        response = client.post(
            "/api/redact",
            files={"document": ("Customer File.docx", handle, "application/octet-stream")},
            data={
                "company_scope": "all",
                "image_policy": "none",
                "use_ner": "false",
                "seed": "42",
            },
        )

    assert response.status_code == 200, response.text
    assert int(response.headers["x-redaction-count"]) > 0
    assert response.headers["x-ner-model"] == "disabled"
    disposition = response.headers["content-disposition"]
    assert "Customer File - REDACTED.docx" in disposition or (
        "Customer%20File%20-%20REDACTED.docx" in disposition
    )
    output = tmp_path / "download.docx"
    output.write_bytes(response.content)
    package = DocxPackage(output)
    assert package.is_redacted()
    assert "Priya Sharma" not in "\n".join(record.text for record in package.records)


def test_invalid_and_non_docx_uploads_are_rejected() -> None:
    with TestClient(app) as client:
        wrong_extension = client.post(
            "/api/redact", files={"document": ("notes.txt", b"hello", "text/plain")}
        )
        invalid_zip = client.post(
            "/api/redact",
            files={"document": ("broken.docx", b"not a zip", "application/octet-stream")},
        )

    assert wrong_extension.status_code == 400
    assert invalid_zip.status_code == 400


def test_docx_zip_bomb_guard_rejects_large_expansion(
    monkeypatch, tmp_path: Path
) -> None:
    from web import app as web_app_module

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * 1024)
    monkeypatch.setattr(web_app_module, "MAX_UNCOMPRESSED_BYTES", 100)

    with TestClient(app) as client:
        response = client.post(
            "/api/redact",
            files={"document": ("large.docx", payload.getvalue(), DOCX_MEDIA_TYPE)},
        )

    assert response.status_code == 413


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
