# PII Redaction Evaluation Report

## Headline results

| Matching policy | TP | FP | FN | Accuracy | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Strict (primary) | 475 | 14 | 6 | 0.960 | 0.971 | 0.988 | 0.979 |
| Relaxed (boundary diagnostic) | 480 | 9 | 1 | 0.980 | 0.982 | 0.998 | 0.990 |

Accuracy is span Jaccard accuracy (`TP / (TP + FP + FN)`). This is more informative than ordinary classification accuracy for sparse span extraction; token accuracy is reported separately below.

## Evaluation approach

The primary corpus contains independently declared gold spans from three reproducible prospectus strata: dense front matter/definitions/general information, 150 random prose paragraphs selected with seed 42, and a structured sweep of records containing email, phone, URL, or identifier signals. The gold builder uses declarative source values and address slices and does not read model predictions.

Strict scoring requires the same PII type and exact character boundaries. Relaxed scoring requires the same type and any character overlap, making boundary-only address disagreements visible. The report also includes per-type metrics, a Wilson recall interval for the random-prose stratum, every strict false positive/negative, a separately declared synthetic fixture for types absent from the source document, and independently reviewed image ground truth.

Gold spans: **481**. Predicted spans in scope: **489**.
Evaluated types: **CIN, COMPANY, DIN, EMAIL, PERSON, PHONE, POSTAL_ADDRESS, SEBI_REG_NO**.

## Strict per-type results

| PII type | TP | FP | FN | Precision | Recall | F1 | Jaccard accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| CIN | 9 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| COMPANY | 123 | 13 | 4 | 0.904 | 0.969 | 0.935 | 0.879 |
| DIN | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 104 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| PERSON | 103 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| PHONE | 36 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| POSTAL_ADDRESS | 79 | 1 | 2 | 0.988 | 0.975 | 0.981 | 0.963 |
| SEBI_REG_NO | 13 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| OVERALL | 475 | 14 | 6 | 0.971 | 0.988 | 0.979 | 0.960 |

## Relaxed overall results

TP **480**, FP **9**, FN **1**; precision **0.982**, recall **0.998**, F1 **0.990**, Jaccard accuracy **0.980**.

Token classification accuracy: **0.9987** (19719/19744 tokens).

## Interpretation

The relaxed prose-sample recall Wilson 95% interval is **0.676–1.000**. It is an estimate for unreviewed prose, not a claim of exhaustive document-wide recall.
Over-redaction rate (`FP / predictions`) is **0.029** under strict matching.
Boundary-only address differences are visible in the strict/relaxed gap; they do not necessarily represent a privacy leak.

## Error catalogue

### False positives (strict)

- `word/document.xml:p000076` COMPANY: `Nuvama`
- `word/document.xml:p000076` COMPANY: `Wealth Management Limited`
- `word/document.xml:p000080` COMPANY: `ICICI`
- `word/document.xml:p000080` COMPANY: `Securities Limited`
- `word/document.xml:p000115` COMPANY: `EVEREST`
- `word/document.xml:p000499` COMPANY: `Nuvama`
- `word/document.xml:p003663` POSTAL_ADDRESS: `S. no. 245/ 104, Pushpakamal, Deccan Gymkhana Society, lane no.`
- `word/document.xml:p003714` COMPANY: `Nuvama`
- `word/document.xml:p003718` COMPANY: `Nuvama`
- `word/document.xml:p003732` COMPANY: `Nuvama`
- `word/document.xml:p003749` COMPANY: `Nuvama`
- `word/document.xml:p003755` COMPANY: `Nuvama`
- `word/document.xml:p003808` COMPANY: `the Offer Escrow Collection Bank HDFC Bank Limited`
- `word/document.xml:p003926` COMPANY: `The Federal Bank Limited`

### False negatives (strict)

- `word/document.xml:p000076` COMPANY: `NuvamaWealth Management Limited`
- `word/document.xml:p000080` COMPANY: `ICICISecurities Limited`
- `word/document.xml:p003663` POSTAL_ADDRESS: `S. no. 245/ 104, Pushpakamal, Deccan Gymkhana Society, lane no`
- `word/document.xml:p003802` POSTAL_ADDRESS: `1st Floor, L B S Marg, Vikhroli (West) Mumbai 400083, (Maharashtra), India`
- `word/document.xml:p003808` COMPANY: `HDFC Bank Limited`
- `word/document.xml:p003926` COMPANY: `Federal Bank Limited`

## Known risks and policy exclusions

- Contextual names and surname-only mentions remain the main recall risk; glossary and role/table seeding reduce it.
- Multi-line postal addresses are the main boundary risk.
- Regulators, statutes, exchanges, cities in isolation, monetary amounts, page references, share counts, and resolution dates are deliberately not redacted.
- SSN, credit-card, IP-address and DOB behavior is evaluated separately on the synthetic fixture when those types are absent from the prospectus sample.


## Synthetic coverage for absent/rare types

The source prospectus has no adjudicated SSN, credit-card, IP-address or DOB instances, so those validators are measured on an independently declared synthetic DOCX. Negative controls include invalid SSNs/IPs/cards and order/ticket numbers.

| PII type | TP | FP | FN | Precision | Recall | F1 | Jaccard accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| COMPANY | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| CREDIT_CARD | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| EMAIL | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| IP_ADDRESS | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| PAN | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| PERSON | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| PHONE | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| POSTAL_ADDRESS | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| SSN | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| OVERALL | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |

Synthetic token accuracy: **1.0000** (68/68 tokens).


## Embedded-image coverage

Image ground truth was reviewed independently from OCR predictions. An image is counted as detected when its media path appears in the image-redaction audit.

| Image category | Detected | Gold sensitive images |
|---|---:|---:|
| IDENTITY_DOCUMENT_AADHAAR | 1 | 1 |
| IDENTITY_DOCUMENT_PAN | 1 | 1 |
| PARTY_LOGO | 5 | 5 |
| QR_CODE | 1 | 1 |
| OVERALL | 8 | 8 |

TP **8**, FP **0**, FN **0**; precision **1.000**, recall **1.000**, F1 **1.000**.
All embedded images in this prospectus are sensitive under the selected party/identity-document policy, so this document does not provide non-sensitive image controls for estimating image false-positive behavior.

