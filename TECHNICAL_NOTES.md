# Technical Notes: PII Redaction Tool for Word Documents

For a complete end-to-end, file-by-file, and line-level explanation with interview questions, see [PROJECT_WALKTHROUGH.md](PROJECT_WALKTHROUGH.md).

This application reads a `.docx`, detects personally identifiable information (PII), and replaces it with deterministic, realistic fake alternatives while preserving the original Word package, styles, tables, drawing positions, headers, footers, hyperlinks, and field codes. Sensitive raster images are replaced in place with same-sized neutral placeholders. It was implemented for the KSH International Red Herring Prospectus assignment, but the detector API and CLI are reusable for other DOCX files.

## Quick start

Python 3.12 is recommended. The default sensitive-image policy also requires the local `tesseract` and `zbarimg` commands (Ubuntu/Debian packages `tesseract-ocr` and `zbar-tools`). No document content is sent to an external service.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m pii_redactor \
  --input "../Red Herring Prospectus.docx" \
  --output "output/Red Herring Prospectus - REDACTED.docx" \
  --map output/redaction_map.json \
  --detections output/detections.jsonl \
  --image-audit output/image_redactions.json \
  --types all --company-scope parties --image-policy sensitive --seed 42
```

The repository also contains a minimal same-origin web application. Start it
after installing the requirements, then open `http://localhost:10000`:

```bash
.venv/bin/python -m web
```

`web/app.py` exposes `POST /api/redact`, while `web/static/` contains the plain
HTML/CSS/JavaScript frontend. The handler accepts one DOCX, rejects oversized,
corrupt, encrypted, duplicate-entry, and suspiciously expanded ZIP packages,
serializes redaction jobs to bound memory, and deletes its temporary directory
after the response. It deliberately returns no redaction map or occurrence
audit. The frontend defaults to `image-policy=all`, the safest behavior when
users do not need non-confidential illustrations preserved.

`Dockerfile` installs the native Tesseract and zbar commands and launches one
Uvicorn worker, exposing a `/health` probe. The cloud image installs
`requirements-cloud.txt` with `en_core_web_lg`, matching the reference
CLI/evaluation environment (`requirements.txt`), so the published metrics
describe the same model that actually serves requests in production; peak
memory still depends on the uploaded document and must be monitored. The host
must be sized for `en_core_web_lg` (~1.5-2 GB RAM at model-load time, ~560 MB
on disk for the model artifacts) — a free/demo-tier instance capped near
512 MB will OOM. `SpacyNerDetector` caches the loaded model at process scope
(`pii_redactor/detectors/ner.py`) so it is loaded once per worker process, not
once per request. Public demo hosting is not appropriate for real confidential
documents without authentication, access controls, provider/DPA review,
retention/log review, abuse controls, and a fresh security assessment.

Use `--dry-run` for counts without writing a DOCX, `--no-ner` to run without the spaCy NER model, and `--types EMAIL,PHONE,...` to restrict the text policy. `--debug-block p012809` (or a text substring) prints every spaCy/pipeline candidate for matching records, including its producer, accept/reject status, and exact rule. `--image-policy all` redacts every raster without OCR classification; `--image-policy none` explicitly leaves images unchanged. A completed run adds the custom Word property `PiiRedactorVersion`; rerunning it is a safe no-op unless `--force` is given.

The default policy uses two local trained components: spaCy for supplemental entity proposals and Tesseract for image classification. Neither generates replacements or makes network calls. For a run without learned detection models, use `--no-ner --image-policy all`; rule-based text detection remains active and every image is removed conservatively.

## Approach

The system combines five layers:

1. Validated regex detects email, guarded Indian and North American phone numbers, SSN, credit card, IPv4/IPv6, URL, and Indian identifiers. Credit cards require a known issuer and valid Luhn checksum; Aadhaar requires Verhoeff; DIN and bank accounts require labels/table context. A bare 10-digit number is not treated as a phone without phone context. Phone surrogate keys contain digits only, so `(800) 285-7772` and `800-285-7772` share one fake number while retaining their respective masks.
2. Word structure seeds people, companies, DINs, and Indian/US addresses from director/contact tables and labels. ZIP-anchored US street and P.O.-box addresses can be projected across adjacent paragraphs. Slash-separated contact lists, Word-joined names such as `SunilNagayya Shetty`, and names separated by Word layout controls are handled explicitly.
3. A document gazetteer propagates exact, case, shortened, and safe surname variants. International legal suffixes and ampersands are recognized without joining neighboring entities; definition-table aliases are linked back to their full company. `en_core_web_lg` supplies lower-confidence PERSON/ORG candidates and suffixless brand proposals; high-confidence structured spans win overlaps.
4. A mined definitions glossary, the document's lower-case vocabulary, public-body/statute rules, and a curated allowlist veto financial terms, regulators, exchanges, statutes, headings, cities, `P.O. Box`, and ordinary capitalized phrases in isolation.
5. Local Tesseract OCR, QR decoding, nearby Word text, and perceptual hashes identify identity-document scans, QR payloads, party logos, and visually repeated logo variants. Flagged media are replaced with `REDACTED` placeholders without changing their Word drawing extents.

The DOCX layer edits the original ZIP/XML package instead of rebuilding it. It walks `word/document.xml`, all headers/footers, comments/footnotes/endnotes when present, text boxes, nested tables, visible `w:t` nodes, and hidden `w:instrText` field codes. Replacements spanning Word runs inherit the first run’s formatting. Cross-paragraph address blocks are projected back into their owning paragraphs. Word tabs and line breaks are represented as hard virtual boundaries: an entity can be projected into safe text-node segments, while any other replacement crossing one is refused and counted.

Surrogates are seeded and deterministic. Casing and identifier/phone grouping are preserved; company legal suffixes remain; US P.O. boxes stay P.O. boxes and street addresses stay streets; generated cards pass Luhn; generated Aadhaar values pass Verhoeff; CINs retain their statutory shape. Known `first.last@…` addresses reuse the same fake name generated for that person. URL keys use the normalized registrable domain, preserving paths while mapping every source domain to one plausible fake domain; a short numeric disambiguator is added only if two generated domain stems collide. The same original therefore receives the same replacement throughout a run. A global post-run invariant rejects any surrogate shared by two distinct canonical entities, across every label. A final privacy invariant also fails the run if any surname from a detected multi-token person remains in the edited document.

A bare capitalized `Name.tld` token with no scheme, no `www.`, and no path is treated as an organization/product candidate rather than a URL—for example, `Outlook.com`, `Code.org`, and `StopNCII.org`. URL classification requires `http://`/`https://`, a leading `www.`, or a path such as `example.com/investor`. Public regulators and public bodies remain allowlisted, including SEC, PCAOB, and the European Union.

## Redaction policy and tradeoffs

The reusable CLI defaults to `--company-scope all`, which combines legal-suffix rules with filtered NER brand candidates. `--company-scope parties` is the narrower prospectus policy: it redacts the issuer, promoters/group entities/trusts, BRLMs, registrar, banks, auditors, and legal counsel. Party mode deliberately leaves SEBI, RBI, RoC, BSE, NSE, CDSL/NSDL, statutes, public regulator/exchange URLs, cities in isolation, prices, share counts, page/section numbers, resolution dates, and audit firm registration/peer-review numbers unchanged. CIN, DIN, SEBI registration numbers, PAN, Aadhaar, IFSC, GSTIN, passport and context-labelled bank accounts are bonus sensitive types beyond the assignment minimum.

The principal recall risk is a new name/address form that is absent from the document’s structured party lists. The principal precision risk is NER confusing capitalized financial language with an entity; the glossary and suffix/party-scope gates mitigate this. Address boundaries are inherently less crisp than email/phone boundaries, so evaluation reports both exact and overlap-tolerant scoring. The unlabelled-address fallback is intentionally limited to short records beginning with an address unit such as `Flat`, `Plot`, `House`, or `Unit`; a facility mention such as “machinery at Unit 2 in Chakan” inside business prose is not an address.

Under `--image-policy sensitive`, OCR is used as a classifier only. PAN/Aadhaar-style identity documents, QR codes, OCR-visible party names, logos next to detected party text, and near-duplicate variants of a detected party logo are redacted. OCR text and QR payloads are not persisted in the audit. If the required local inspection commands are unavailable, the process fails closed and asks the operator to install them or select the explicit `all`/`none` policy.

`output/redaction_map.json` is intentionally reversible audit data and must be stored as sensitive material or deleted after review. `output/detections.jsonl` records every text span, source detector, confidence, offsets, and replacement. `output/image_redactions.json` records media paths, non-content classification reasons, source hashes, and usage counts without storing OCR text.

## Evaluation

The RHP corpus uses three reproducible strata:

- Dense: cover/front matter, definitions, and General Information.
- Prose: 150 random paragraphs using seed 42.
- Structured sweep: every paragraph containing an email/phone/URL/identifier signal.

`evaluation/curate_rhp_gold.py` materializes reviewed ground truth from declarative source values and address slices; it never reads predictions. `evaluation/score.py` reports TP/FP/FN, precision, recall, F1, span Jaccard accuracy, token accuracy, strict boundaries, relaxed overlap, Wilson intervals, and every disagreement. The generated [evaluation report](EVALUATION_REPORT.md) contains the current numbers. The RHP validation strata were used during iterative hardening, so the report does not claim they are an untouched external test set.

`evaluation/gold/rhp_image_gold.json` independently classifies every source media asset. The document contains five party-logo assets, one QR code, one PAN-card scan, and one Aadhaar-card scan; all eight are detected and removed. Because this document contains no non-sensitive images, it cannot estimate image-classifier false positives on clean media.

The prospectus has no adjudicated SSN, credit-card, IP-address, or date-of-birth instances. `evaluation/fixtures/synthetic_pii.docx` independently tests those classes, including invalid-number and order/ticket negative controls.

```bash
.venv/bin/python evaluation/build_fixture.py
.venv/bin/python evaluation/build_sample.py \
  --input "../Red Herring Prospectus.docx" \
  --output evaluation/gold/rhp_sample.jsonl
.venv/bin/python evaluation/curate_rhp_gold.py
.venv/bin/pytest tests -q
.venv/bin/python evaluation/leak_check.py \
  --docx "output/Red Herring Prospectus - REDACTED.docx" \
  --map output/redaction_map.json \
  --detections output/detections.jsonl \
  --image-audit output/image_redactions.json
```

## Adding a PII type

The single extension point is `Detector.detect(text, context) -> list[Span]`. For example:

```python
class VehicleRegistrationDetector(Detector):
    pii_type = "VEHICLE_REGISTRATION"
    priority = 95

    def detect(self, text, context=None):
        return [
            Span(m.start(), m.end(), self.pii_type, m.group(),
                 "vehicle_regex", 0.97, self.priority)
            for m in VEHICLE_RE.finditer(text)
        ]
```

Register it in `pipeline.py`, add the type in `config.ALL_TYPES`, add a format-aware branch in `SurrogateStore`, and add positive/negative tests. DOCX traversal, overlap resolution, audit output, and CLI behavior need no changes.

## Verification performed

- All retained automated tests pass, covering table-footnote name normalization, global surrogate injectivity, P.O.-box shape preservation, bare-domain organization classification, North American phone normalization, US ZIP/P.O.-box addresses, URL boundary rules, Word tab/line-break safety, lower-case vocabulary/statute false-positive controls, whole-person redaction, validators, image-package preservation, field-code redaction, deterministic formats, name/email linkage, and idempotency.
- The final DOCX passes ZIP integrity and opens/renders through LibreOffice 24.2 as a 125-page A4 PDF. The formerly damaged long prose renders in full.
- The leak checker finds no detected source value in any Word XML, including `mailto:` field codes, and no audited source image bytes remain.
- All eight source media assets are replaced: five party-logo assets, one QR code, one PAN-card scan, and one Aadhaar-card scan.
- All 27 unique original emails have zero intersection with the redacted email set.
- The Microsoft FY2025 source and its generated regression artifacts are intentionally excluded from the cleaned submission. General tests for name-footnote handling, companies, URLs, addresses, and surrogate consistency remain.
