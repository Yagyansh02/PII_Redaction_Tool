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

The included `Dockerfile` deploys both UI and API as a Docker web service. `requirements-cloud.txt` uses `en_core_web_sm` to fit a free/trial-tier instance (~512 MB RAM); the reference `requirements.txt` environment used for the reported metrics uses `en_core_web_lg`. That gap is deliberately narrowed with domain-specific precision/recall guards added and verified against `en_core_web_sm`'s actual failure modes (see `PERSON_VETO_TOKENS` in `pii_redactor/detectors/ner.py` and `_seed_transaction_people` in `pii_redactor/detectors/gazetteer.py`), not just assumed to be fine. If the deployment is ever moved to a plan with more RAM, switch `requirements-cloud.txt` to `en_core_web_lg` to match the reference environment exactly. Actual peak memory still depends on document size and must be monitored. Use an access-controlled paid deployment after completing a privacy/security review; the service is not intended to hold confidential production data on a free/shared tier.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Render Blueprint flow, local Docker smoke test, and production security checklist.
