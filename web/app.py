"""Minimal, privacy-conscious upload/download API for the DOCX redactor."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from pii_redactor import RedactionPipeline, __version__
from pii_redactor.config import RedactionConfig


LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
UPLOAD_CHUNK_BYTES = 1024 * 1024
PROCESSING_LOCK = threading.Lock()


def _positive_env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


MAX_UPLOAD_BYTES = _positive_env_int("MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
MAX_UNCOMPRESSED_BYTES = _positive_env_int(
    "MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024
)
MAX_ZIP_MEMBERS = _positive_env_int("MAX_ZIP_MEMBERS", 10_000)

app = FastAPI(
    title="DOCX PII Redactor",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/assets", StaticFiles(directory=STATIC_ROOT), name="assets")


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    """Reject clearly oversized requests early and add browser hardening headers."""

    response: Response | None = None
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            # Multipart framing needs a small allowance beyond the DOCX itself.
            if int(content_length) > MAX_UPLOAD_BYTES + 1024 * 1024:
                response = JSONResponse(
                    {"detail": f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."},
                    status_code=413,
                )
        except ValueError:
            response = JSONResponse(
                {"detail": "Invalid Content-Length header."}, status_code=400
            )

    if response is None:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "form-action 'self'; base-uri 'none'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html", media_type="text/html")


@app.get("/health", include_in_schema=False)
@app.get("/api/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def _safe_download_name(filename: str | None) -> str:
    source_name = Path(filename or "document.docx").name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(source_name).stem).strip(" ._")
    return f"{stem or 'document'} - REDACTED.docx"


def _validate_docx_archive(path: Path) -> None:
    """Validate the ZIP envelope before the XML pipeline opens it."""

    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise HTTPException(413, "The DOCX contains too many package entries.")
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise HTTPException(400, "The DOCX contains duplicate package entries.")
            if "word/document.xml" not in names:
                raise HTTPException(400, "The upload is not a valid DOCX document.")
            if any(member.flag_bits & 0x1 for member in members):
                raise HTTPException(400, "Encrypted DOCX packages are not supported.")
            total_size = sum(member.file_size for member in members)
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise HTTPException(413, "The expanded DOCX is too large to process safely.")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise HTTPException(400, "The DOCX package failed its integrity check.")
    except zipfile.BadZipFile as error:
        raise HTTPException(400, "The upload is not a valid DOCX document.") from error


def _save_upload(upload: UploadFile, destination: Path) -> int:
    total = 0
    try:
        with destination.open("wb") as target:
            while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                    )
                target.write(chunk)
    finally:
        upload.file.close()
    if total == 0:
        raise HTTPException(400, "The uploaded file is empty.")
    return total


def _remove_workspace(workspace: Path) -> None:
    shutil.rmtree(workspace, ignore_errors=True)


@app.post("/api/redact", include_in_schema=False)
async def redact_document(request: Request) -> FileResponse:
    """Redact one DOCX and stream it back without retaining source or audit data.

    Accepts multipart/form-data with the DOCX under the field name ``document``
    *or* ``file`` (the latter is the conventional name used by many HTTP clients
    and automated evaluators).  All other form fields are optional.
    """

    # Parse the multipart body manually so we accept both "document" and "file"
    # as the upload field name without hard-coding a single FastAPI File() param.
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(400, "Could not parse the multipart form body.") from exc

    # Resolve the uploaded file from whichever field name the client used.
    # request.form() returns starlette.datastructures.UploadFile instances, so
    # we check against that base class (not fastapi.UploadFile which is a subclass).
    upload: UploadFile | None = None
    for field_name in ("document", "file"):
        candidate = form.get(field_name)
        if isinstance(candidate, UploadFile) or hasattr(candidate, "read"):
            upload = candidate
            break

    if upload is None:
        raise HTTPException(
            422,
            "No DOCX file found in the request. "
            "Send the file in a multipart/form-data field named 'document' or 'file'.",
        )

    # --- optional form fields with safe defaults ---
    def _form_str(key: str, default: str) -> str:
        value = form.get(key)
        return str(value).strip() if value is not None else default

    def _form_bool(key: str, default: bool) -> bool:
        raw = form.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() not in {"false", "0", "no", "off"}

    def _form_int(key: str, default: int) -> int:
        raw = form.get(key)
        if raw is None:
            return default
        try:
            return int(str(raw).strip())
        except ValueError:
            return default

    company_scope = _form_str("company_scope", "all")
    image_policy = _form_str("image_policy", "all")
    use_ner = _form_bool("use_ner", True)
    seed = _form_int("seed", 42)

    # Validate enumerated fields.
    if company_scope not in {"parties", "all", "none"}:
        raise HTTPException(422, "company_scope must be one of: parties, all, none.")
    if image_policy not in {"sensitive", "all", "none"}:
        raise HTTPException(422, "image_policy must be one of: sensitive, all, none.")

    original_name = upload.filename or ""
    if Path(original_name).suffix.casefold() != ".docx":
        upload.file.close()
        raise HTTPException(400, "Please upload a .docx file.")
    if not 0 <= seed <= 2_147_483_647:
        upload.file.close()
        raise HTTPException(422, "Seed must be between 0 and 2147483647.")

    workspace = Path(tempfile.mkdtemp(prefix="pii-redactor-web-"))
    source = workspace / "source.docx"
    output = workspace / "redacted.docx"
    try:
        _save_upload(upload, source)
        _validate_docx_archive(source)

        if not PROCESSING_LOCK.acquire(blocking=False):
            raise HTTPException(
                503,
                "The redactor is processing another document. Please retry shortly.",
                headers={"Retry-After": "10"},
            )
        try:
            config = RedactionConfig(
                company_scope=company_scope,
                image_policy=image_policy,
                use_ner=use_ner,
                seed=seed,
            )
            result = RedactionPipeline(config).run(source, output)
        finally:
            PROCESSING_LOCK.release()

        response_path = source if result.already_redacted else output
        headers = {
            "X-Redaction-Count": str(result.total),
            "X-Image-Redaction-Count": str(len(result.image_redactions)),
            "X-NER-Model": result.ner_model,
            "X-Already-Redacted": str(result.already_redacted).lower(),
            "X-Redaction-Counts": json.dumps(result.counts, separators=(",", ":")),
            "Cache-Control": "no-store",
        }
        return FileResponse(
            response_path,
            media_type=DOCX_MEDIA_TYPE,
            filename=_safe_download_name(original_name),
            headers=headers,
            background=BackgroundTask(_remove_workspace, workspace),
        )
    except HTTPException:
        _remove_workspace(workspace)
        raise
    except (FileNotFoundError, ValueError) as error:
        _remove_workspace(workspace)
        LOGGER.info("A DOCX redaction request was rejected: %s", type(error).__name__)
        raise HTTPException(
            422,
            "The document could not be processed with the selected redaction policy.",
        ) from error
    except Exception as error:
        _remove_workspace(workspace)
        LOGGER.exception("Unexpected DOCX redaction failure")
        raise HTTPException(
            500, "The document could not be processed due to an internal error."
        ) from error
