# DOCX PII Redactor

This submission replaces PII in `.docx` files with deterministic, realistic surrogates while preserving Word formatting and package structure. Detection combines validated regular expressions, table/label context, a document gazetteer, and local spaCy NER; local Tesseract and QR inspection identify sensitive embedded images for in-place replacement. The main recall risk is an unseen name or unusual multi-line address, while the main precision risk is capitalized financial prose being mistaken for a company; allowlists, context gates, checksums, and negative controls reduce those errors. No document content is sent to an external service.

## Deliverables

- [Source code](pii_redactor/)
- [Redacted DOCX](output/Red%20Herring%20Prospectus%20-%20REDACTED.docx)
- [Evaluation report](EVALUATION_REPORT.md), including accuracy **0.960**, precision **0.971**, and recall **0.988** under strict scoring
- [Detailed walkthrough](PROJECT_WALKTHROUGH.md) and [technical notes](TECHNICAL_NOTES.md)

## Run

Python 3.12 is recommended. The sensitive-image policy also needs local `tesseract` and `zbarimg` commands.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pii_redactor \
  --input "../Red Herring Prospectus.docx" \
  --output "output/Red Herring Prospectus - REDACTED.docx" \
  --types all --company-scope parties --image-policy sensitive --seed 42
```

Optional `--map`, `--detections`, and `--image-audit` paths produce sensitive review artifacts and are documented in the technical notes. The evaluation uses independently declared stratified gold spans, synthetic negative controls, and reviewed image ground truth; see the report for precision, recall, F1, token accuracy, per-type results, and limitations.

## Web app and deployment

The minimal frontend and FastAPI backend run as one same-origin service. Uploaded DOCX files are size/ZIP-validated, processed in an isolated temporary directory, returned directly, and deleted after the response; the web API never creates a reversible redaction map. Run it locally with `python -m web` and open `http://localhost:10000`.

The included `Dockerfile` and `render.yaml` deploy both UI and API to a Render Docker web service. Render uses `requirements-cloud.txt` with `en_core_web_sm` to target its 512 MB demo instance; actual peak memory depends on document size and must be monitored. The reported assignment metrics were produced by the reference `requirements.txt` environment with `en_core_web_lg` and must not be attributed to the smaller deployment model. The free service is suitable for demonstration, not confidential production data: use an access-controlled paid deployment after completing a privacy/security review.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Render Blueprint flow, local Docker smoke test, and production security checklist.
