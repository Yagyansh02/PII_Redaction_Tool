#!/usr/bin/env python3
"""Score strict and overlap-tolerant span extraction and write Markdown."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Item:
    record_id: str
    start: int
    end: int
    pii_type: str
    text: str
    stratum: str = ""


def read_items(path: Path) -> list[Item]:
    items: list[Item] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        items.append(Item(row["record_id"], int(row["start"]), int(row["end"]), row["type"], row.get("text", ""), row.get("stratum", "")))
    return items


def match_items(gold: list[Item], predictions: list[Item], relaxed: bool) -> tuple[list[tuple[Item, Item]], list[Item], list[Item]]:
    unmatched_predictions = set(range(len(predictions)))
    matches: list[tuple[Item, Item]] = []
    false_negatives: list[Item] = []
    for truth in gold:
        candidates: list[tuple[int, int]] = []
        for index in unmatched_predictions:
            prediction = predictions[index]
            if prediction.record_id != truth.record_id or prediction.pii_type != truth.pii_type:
                continue
            exact = prediction.start == truth.start and prediction.end == truth.end
            overlap = prediction.start < truth.end and truth.start < prediction.end
            if exact or (relaxed and overlap):
                intersection = min(prediction.end, truth.end) - max(prediction.start, truth.start)
                candidates.append((0 if exact else -intersection, index))
        if not candidates:
            false_negatives.append(truth)
            continue
        _, selected = min(candidates)
        unmatched_predictions.remove(selected)
        matches.append((truth, predictions[selected]))
    false_positives = [predictions[index] for index in sorted(unmatched_predictions)]
    return matches, false_positives, false_negatives


def metric_row(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    jaccard = tp / (tp + fp + fn) if tp + fp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "jaccard": jaccard}


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (1.0, 1.0)
    estimate = successes / total
    denominator = 1 + z * z / total
    centre = estimate + z * z / (2 * total)
    spread = z * math.sqrt((estimate * (1 - estimate) + z * z / (4 * total)) / total)
    return ((centre - spread) / denominator, (centre + spread) / denominator)


def token_accuracy(corpus_path: Path, gold: list[Item], predictions: list[Item]) -> tuple[int, int, float]:
    texts: dict[str, str] = {}
    for line in corpus_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            texts[row["record_id"]] = row["text"]
    by_gold: dict[str, list[Item]] = defaultdict(list)
    by_prediction: dict[str, list[Item]] = defaultdict(list)
    for item in gold:
        by_gold[item.record_id].append(item)
    for item in predictions:
        by_prediction[item.record_id].append(item)
    correct = total = 0
    for record_id, text in texts.items():
        for token in re.finditer(r"\S+", text):
            truth_positive = any(item.start < token.end() and token.start() < item.end for item in by_gold[record_id])
            predicted_positive = any(item.start < token.end() and token.start() < item.end for item in by_prediction[record_id])
            correct += truth_positive == predicted_positive
            total += 1
    return correct, total, correct / total if total else 1.0


def filter_to_corpus(items: Iterable[Item], corpus_path: Path | None) -> list[Item]:
    if corpus_path is None:
        return list(items)
    ids = {
        json.loads(line)["record_id"]
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    return [item for item in items if item.record_id in ids]


def render_report(
    gold_path: Path,
    prediction_path: Path,
    corpus_path: Path | None,
    evaluated_types: set[str] | None = None,
) -> str:
    gold = read_items(gold_path)
    predictions = filter_to_corpus(read_items(prediction_path), corpus_path)
    if evaluated_types is not None:
        gold = [item for item in gold if item.pii_type in evaluated_types]
        predictions = [item for item in predictions if item.pii_type in evaluated_types]
    strict = match_items(gold, predictions, False)
    relaxed = match_items(gold, predictions, True)
    strict_overall = metric_row(len(strict[0]), len(strict[1]), len(strict[2]))
    relaxed_overall = metric_row(len(relaxed[0]), len(relaxed[1]), len(relaxed[2]))
    types = sorted({item.pii_type for item in gold + predictions})
    lines = [
        "# PII Redaction Evaluation Report",
        "",
        "## Headline results",
        "",
        "| Matching policy | TP | FP | FN | Accuracy | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Strict (primary) | {strict_overall['tp']} | {strict_overall['fp']} | {strict_overall['fn']} | {strict_overall['jaccard']:.3f} | {strict_overall['precision']:.3f} | {strict_overall['recall']:.3f} | {strict_overall['f1']:.3f} |",
        f"| Relaxed (boundary diagnostic) | {relaxed_overall['tp']} | {relaxed_overall['fp']} | {relaxed_overall['fn']} | {relaxed_overall['jaccard']:.3f} | {relaxed_overall['precision']:.3f} | {relaxed_overall['recall']:.3f} | {relaxed_overall['f1']:.3f} |",
        "",
        "Accuracy is span Jaccard accuracy (`TP / (TP + FP + FN)`). This is more informative than ordinary classification accuracy for sparse span extraction; token accuracy is reported separately below.",
        "",
        "## Evaluation approach",
        "",
        "The primary corpus contains independently declared gold spans from three reproducible prospectus strata: dense front matter/definitions/general information, 150 random prose paragraphs selected with seed 42, and a structured sweep of records containing email, phone, URL, or identifier signals. The gold builder uses declarative source values and address slices and does not read model predictions.",
        "",
        "Strict scoring requires the same PII type and exact character boundaries. Relaxed scoring requires the same type and any character overlap, making boundary-only address disagreements visible. The report also includes per-type metrics, a Wilson recall interval for the random-prose stratum, every strict false positive/negative, a separately declared synthetic fixture for types absent from the source document, and independently reviewed image ground truth.",
        "",
        f"Gold spans: **{len(gold)}**. Predicted spans in scope: **{len(predictions)}**.",
        f"Evaluated types: **{', '.join(types)}**.",
        "",
        "## Strict per-type results",
        "",
        "| PII type | TP | FP | FN | Precision | Recall | F1 | Jaccard accuracy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pii_type in types + ["OVERALL"]:
        selected_gold = gold if pii_type == "OVERALL" else [item for item in gold if item.pii_type == pii_type]
        selected_predictions = predictions if pii_type == "OVERALL" else [item for item in predictions if item.pii_type == pii_type]
        matches, fps, fns = match_items(selected_gold, selected_predictions, False)
        row = metric_row(len(matches), len(fps), len(fns))
        lines.append(
            f"| {pii_type} | {row['tp']} | {row['fp']} | {row['fn']} | {row['precision']:.3f} | {row['recall']:.3f} | {row['f1']:.3f} | {row['jaccard']:.3f} |"
        )
    lines.extend([
        "",
        "## Relaxed overall results",
        "",
        f"TP **{relaxed_overall['tp']}**, FP **{relaxed_overall['fp']}**, FN **{relaxed_overall['fn']}**; precision **{relaxed_overall['precision']:.3f}**, recall **{relaxed_overall['recall']:.3f}**, F1 **{relaxed_overall['f1']:.3f}**, Jaccard accuracy **{relaxed_overall['jaccard']:.3f}**.",
    ])
    if corpus_path is not None:
        correct, total, accuracy = token_accuracy(corpus_path, gold, predictions)
        lines.extend(["", f"Token classification accuracy: **{accuracy:.4f}** ({correct}/{total} tokens)."])
    prose_gold = [item for item in gold if "B_prose" in item.stratum]
    prose_predictions = [item for item in predictions if any(item.record_id == truth.record_id for truth in prose_gold)]
    prose_matches = match_items(prose_gold, prose_predictions, True)[0]
    low, high = wilson(len(prose_matches), len(prose_gold))
    lines.extend([
        "",
        "## Interpretation",
        "",
        f"The relaxed prose-sample recall Wilson 95% interval is **{low:.3f}–{high:.3f}**. It is an estimate for unreviewed prose, not a claim of exhaustive document-wide recall.",
        f"Over-redaction rate (`FP / predictions`) is **{len(strict[1]) / len(predictions) if predictions else 0.0:.3f}** under strict matching.",
        "Boundary-only address differences are visible in the strict/relaxed gap; they do not necessarily represent a privacy leak.",
        "",
        "## Error catalogue",
        "",
        "### False positives (strict)",
        "",
    ])
    if strict[1]:
        lines.extend(f"- `{item.record_id}` {item.pii_type}: `{item.text}`" for item in strict[1])
    else:
        lines.append("- None.")
    lines.extend(["", "### False negatives (strict)", ""])
    if strict[2]:
        lines.extend(f"- `{item.record_id}` {item.pii_type}: `{item.text}`" for item in strict[2])
    else:
        lines.append("- None.")
    lines.extend([
        "",
        "## Known risks and policy exclusions",
        "",
        "- Contextual names and surname-only mentions remain the main recall risk; glossary and role/table seeding reduce it.",
        "- Multi-line postal addresses are the main boundary risk.",
        "- Regulators, statutes, exchanges, cities in isolation, monetary amounts, page references, share counts, and resolution dates are deliberately not redacted.",
        "- SSN, credit-card, IP-address and DOB behavior is evaluated separately on the synthetic fixture when those types are absent from the prospectus sample.",
        "",
    ])
    return "\n".join(lines)


def render_synthetic_section(gold_path: Path, prediction_path: Path, corpus_path: Path) -> str:
    gold = read_items(gold_path)
    predictions = filter_to_corpus(read_items(prediction_path), corpus_path)
    types = sorted({item.pii_type for item in gold + predictions})
    lines = [
        "## Synthetic coverage for absent/rare types",
        "",
        "The source prospectus has no adjudicated SSN, credit-card, IP-address or DOB instances, so those validators are measured on an independently declared synthetic DOCX. Negative controls include invalid SSNs/IPs/cards and order/ticket numbers.",
        "",
        "| PII type | TP | FP | FN | Precision | Recall | F1 | Jaccard accuracy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pii_type in types + ["OVERALL"]:
        selected_gold = gold if pii_type == "OVERALL" else [item for item in gold if item.pii_type == pii_type]
        selected_predictions = predictions if pii_type == "OVERALL" else [item for item in predictions if item.pii_type == pii_type]
        matches, fps, fns = match_items(selected_gold, selected_predictions, False)
        row = metric_row(len(matches), len(fps), len(fns))
        lines.append(
            f"| {pii_type} | {row['tp']} | {row['fp']} | {row['fn']} | {row['precision']:.3f} | {row['recall']:.3f} | {row['f1']:.3f} | {row['jaccard']:.3f} |"
        )
    correct, total, accuracy = token_accuracy(corpus_path, gold, predictions)
    lines.extend(["", f"Synthetic token accuracy: **{accuracy:.4f}** ({correct}/{total} tokens).", ""])
    return "\n".join(lines)


def render_image_section(gold_path: Path, prediction_path: Path) -> str:
    gold_rows = json.loads(gold_path.read_text(encoding="utf-8"))
    prediction_rows = json.loads(prediction_path.read_text(encoding="utf-8"))
    gold_sensitive = {row["media_path"] for row in gold_rows if row.get("sensitive")}
    predicted = {row["media_path"] for row in prediction_rows}
    tp = len(gold_sensitive & predicted)
    fp = len(predicted - gold_sensitive)
    fn = len(gold_sensitive - predicted)
    row = metric_row(tp, fp, fn)
    category_totals: dict[str, int] = defaultdict(int)
    category_hits: dict[str, int] = defaultdict(int)
    for item in gold_rows:
        if not item.get("sensitive"):
            continue
        category = item.get("category", "OTHER")
        category_totals[category] += 1
        if item["media_path"] in predicted:
            category_hits[category] += 1
    lines = [
        "## Embedded-image coverage",
        "",
        "Image ground truth was reviewed independently from OCR predictions. An image is counted as detected when its media path appears in the image-redaction audit.",
        "",
        "| Image category | Detected | Gold sensitive images |",
        "|---|---:|---:|",
    ]
    for category in sorted(category_totals):
        lines.append(f"| {category} | {category_hits[category]} | {category_totals[category]} |")
    lines.extend([
        f"| OVERALL | {tp} | {len(gold_sensitive)} |",
        "",
        f"TP **{tp}**, FP **{fp}**, FN **{fn}**; precision **{row['precision']:.3f}**, recall **{row['recall']:.3f}**, F1 **{row['f1']:.3f}**.",
        "All embedded images in this prospectus are sensitive under the selected party/identity-document policy, so this document does not provide non-sensitive image controls for estimating image false-positive behavior.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--types", help="comma-separated evaluated types; default: all present")
    parser.add_argument("--synthetic-gold", type=Path)
    parser.add_argument("--synthetic-predictions", type=Path)
    parser.add_argument("--synthetic-corpus", type=Path)
    parser.add_argument("--image-gold", type=Path)
    parser.add_argument("--image-predictions", type=Path)
    args = parser.parse_args()
    evaluated_types = {item.strip().upper() for item in args.types.split(",")} if args.types else None
    report = render_report(args.gold, args.predictions, args.corpus, evaluated_types)
    synthetic_args = (args.synthetic_gold, args.synthetic_predictions, args.synthetic_corpus)
    if any(synthetic_args) and not all(synthetic_args):
        parser.error("all three --synthetic-* arguments are required together")
    if all(synthetic_args):
        report += "\n\n" + render_synthetic_section(*synthetic_args)
    image_args = (args.image_gold, args.image_predictions)
    if any(image_args) and not all(image_args):
        parser.error("both --image-gold and --image-predictions are required together")
    if all(image_args):
        report += "\n\n" + render_image_section(*image_args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
