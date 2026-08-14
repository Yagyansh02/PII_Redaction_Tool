#!/usr/bin/env python3
"""Materialize the hand-adjudicated RHP gold spans from declarative anchors.

This file intentionally contains reviewed source values and record slices. It
does not read detector output, which prevents circular evaluation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PEOPLE = (
    "Kushal Subbayya Hegde", "Kushal Hegde", "Rajesh Kushal Hegde", "Rajesh Hegde",
    "Rohit Kushal Hegde", "Rohit Hegde", "Rakhi Girija Shetty", "Pushpa Kushal Hegde",
    "Dinesh Hirachand Munot", "Ajay Shriram Patil", "Ram Kumar Tiwari", "Indu Jacob",
    "Sarthak Malvadkar", "Kishan Rastogi", "Abhijit Diwan", "Lokesh Shah", "Soumavo Sarkar",
    "Shanti Gopalkrishnan", "Eric Bacha", "Sachin Gawade", "Pravin Teli", "Siddharth Jadhav",
    "Tushar Gavankar", "Varun Badai", "Prakash Boricha", "Hitesh Ramani", "Chitra Raste",
    "Sharmila Joshi", "Cherag Gyara", "Manisha Shukla", "Tushar Wakhele",
    "Ashish Mathew Pulloor", "Anand Soni", "Sandesh Bhagwat", "Amod Joshi",
    "Katyayani Balasubramanian", "Karunakar Hegde", "Karunakar N. Bhandary",
    "Karunakar Bhandary", "Sangeeta Ramprasad Rai", "Lalit Muljibhai Sarvaiya",
    "Ganesh Prasad", "Vijay Hegde", "Pushpa Hegde", "Maithili Rajesh Hegde",
    "Rupal K. Sancheti", "Salil Ajay Bhargava", "Jabeen Ajay Menon", "Ajay Menon",
    "Sunil Nagayya Shetty", "Narayna B. Shetty", "Narayana B. Shetty", "Jayaram N. Shetty",
    "Gopal BO", "DM Shetty", "Vincent Malhotra", "Hardik Parikh",
)

COMPANIES = (
    "KSH International Limited", "KSH International Private Limited", "Bhandary Metal Extrusion Private Limited",
    "Dhaulagiri Family Trust", "Everest Family Trust", "Makalu Family Trust",
    "Broad Family Trust", "Annapurna Family Trust", "Kanchenjunga Family Trust",
    "Waterloo Industrial Park VI Private Limited", "Waterloo Motors Private Limited",
    "KSH Project Management Services Private Limited", "KSH Infra Park VI Private Limited",
    "KSH Distriparks Private Limited", "KSH Integrated Logistics Private Limited",
    "Kushal Motors and Electricals Private Limited", "Waterloo Industrial Park I Private Limited",
    "Waterloo Industrial Park II Private Limited", "Waterloo Industrial Park III Private Limited",
    "Waterloo Industrial Park IV Private Limited", "Waterloo Industrial Park V Private Limited",
    "Waterloo Industrial Park VIII Private Limited", "Waterloo Industrial Park IX Private Limited",
    "Waterloo Industrial Park IX A Private Limited", "Waterloo Industrial Park IX B Private Limited",
    "KSH Infra Park IV Private Limited", "Nuvama Wealth Management Limited",
    "ICICI Securities Limited", "MUFG Intime India Private Limited",
    "Link Intime India Private Limited", "HDFC Bank Limited", "ICICI Bank Limited",
    "CARE Analytics and Advisory Private Limited", "CARE Ratings Limited",
    "Kirtane & Pandit LLP", "Kirtane & Pandit", "Kanj & Co. LLP", "Kanj and Co LLP",
    "Trilegal", "Hingne Tare & Associates", "Citibank N.A.", "Export-Import Bank of India",
    "IndusInd Bank Limited", "State Bank of India", "Federal Bank Limited", "Bajaj Finance Limited",
    "Waterloo Industrial Park IX", "ICICI Securities", "ICICI Bank",
)

EMAILS = (
    "ksh.ipo@nuvama.com", "ksh@icicisecurities.com", "kshinternational.ipo@in.mpms.mufg.com",
    "customercare@icicisecurities.com", "siddharth.jadhav@hdfcbank.com",
    "sachin.gawade@hdfcbank.com", "eric.bacha@hdfcbank.com", "tushar.gavankar@hdfcbank.com",
    "pravin.teli2@hdfcbank.com", "cs.connect@kshinternational.com", "customerservice.mb@nuvama.com",
    "Ipocmg@icicibank.com", "parag.pansare@kirtanepandit.com", "ksh@in.mpms.mufg.com",
    "Sarthak.malvadkar@kshinterantional.com", "prakash.boricha@nuvama.com",
    "sheetal.parab@nuvama.com", "ipo@trilegal.com", "hingnetare@gmail.com", "hitesh.ramani@citi.com",
    "pro@eximbankindia.in", "sharmila.joshi@indusind.com", "cherag.gyara@icicibank.com",
    "manisha.shukla@hdfcbank.com", "rm6.ifbpune@sbi.co.in", "ashishmp@federalbank.co.in",
    "anand.soni@bajajfinserv.in",
)

PHONES = (
    "+ 91 20 45053237", "+91 22 4009 4400", "+91 22 6807 7100", "+91 81081 14949",
    "+ 91 20 4505 3237", "+91 22 40094400", "+ 91 22 4009 4400", "+91 22 4079 1000",
    "+91 22 30752929", "+91 22 30752928", "+91 22 30752914", "022-68052182",
    "+ 91 (20) 6729 5100", "+ 91 20 6729 5100", "+91 20 6606 4494", "+91 20 2640 3100",
    "+91-20-26234000", "+ 91 8879770456", "+91 20 6769 4648", "+91 20 2561 8211",
    "+ 91 91586 40360", "+91 20 7157 6403",
)

IDENTIFIERS = {
    "CIN": ("U28129PN1979PLC141032", "U67190MH1999PTC118368", "L65920MH1994PLC080618", "L65190GJ1994PLC021012"),
    "DIN": ("00135070", "00114193", "00134926", "03124510", "00049801", "01217000", "10938958", "05293084"),
    "SEBI_REG_NO": ("INM000013004", "INM000011179", "INR000004058", "INZ000166136", "INBI00000063", "INBI00000004"),
}

# Each value is a source substring. Empty string means the entire paragraph is
# address data. Multi-paragraph addresses are annotated one record at a time.
ADDRESS_SLICES: dict[str, tuple[str, ...]] = {
    "word/document.xml:p000011": ("",), "word/document.xml:p000012": ("",),
    "word/document.xml:p000013": ("",), "word/document.xml:p000014": ("",),
    "word/document.xml:p000111": ("11/3,",), "word/document.xml:p000112": ("201,",),
    "word/document.xml:p000138": ("",), "word/document.xml:p000146": ("ICICI Venture House",),
    "word/document.xml:p000155": ("C-101,",),
    "word/document.xml:p000186": ("11/3, 11/4 and 11/5,", "201, Tower 2,"),
    "word/document.xml:p000193": ("11/3, 11/4 and 11/5,",),
    "word/document.xml:p000224": ("201, Tower 2,",), "word/document.xml:p000285": ("11/3, 11/4 and 11/5,",),
    "word/document.xml:p000311": ("Plot No. J-25,",), "word/document.xml:p000313": ("11/3, 11/4 and 11/5,",),
    "word/document.xml:p000315": ("Plot No. 5,",),
    "word/document.xml:p003628": ("11/3,",), "word/document.xml:p003629": ("",), "word/document.xml:p003630": ("",),
    "word/document.xml:p003633": ("201,",), "word/document.xml:p003634": ("",),
    "word/document.xml:p003651": ("",), "word/document.xml:p003655": ("",),
    "word/document.xml:p003659": ("",), "word/document.xml:p003663": ("",), "word/document.xml:p003664": ("",),
    "word/document.xml:p003668": ("",), "word/document.xml:p003672": ("",),
    "word/document.xml:p003676": ("",), "word/document.xml:p003680": ("",),
    "word/document.xml:p003683": ("Gat No.",), "word/document.xml:p003684": ("",), "word/document.xml:p003685": ("",),
    "word/document.xml:p003694": ("801-804,",), "word/document.xml:p003695": ("",),
    "word/document.xml:p003700": ("ICICI Venture House",),
    "word/document.xml:p003780": ("801-804,",), "word/document.xml:p003781": ("",), "word/document.xml:p003782": ("",),
    "word/document.xml:p003788": ("ICICI Venture House",),
    "word/document.xml:p003794": ("",), "word/document.xml:p003795": ("",),
    "word/document.xml:p003796": ("",), "word/document.xml:p003797": ("",),
    "word/document.xml:p003801": ("C-101,",), "word/document.xml:p003802": ("1st Floor,",),
    "word/document.xml:p003809": ("FIG-OPS Department",), "word/document.xml:p003810": ("",),
    "word/document.xml:p003819": ("163,",), "word/document.xml:p003823": ("FIG-OPS Department",),
    "word/document.xml:p003824": ("",), "word/document.xml:p003833": ("163,",),
    "word/document.xml:p003837": ("FIG-OPS Department",), "word/document.xml:p003838": ("",),
    "word/document.xml:p003864": ("",), "word/document.xml:p003865": ("",),
    "word/document.xml:p003866": ("",), "word/document.xml:p003867": ("",),
    "word/document.xml:p003878": ("",), "word/document.xml:p003879": ("",), "word/document.xml:p003880": ("",),
    "word/document.xml:p003887": ("Flat No.",),
    "word/document.xml:p003895": ("",), "word/document.xml:p003896": ("",),
    "word/document.xml:p003902": ("",), "word/document.xml:p003903": ("",), "word/document.xml:p003904": ("",),
    "word/document.xml:p003906": ("2401",), "word/document.xml:p003907": ("",), "word/document.xml:p003908": ("",),
    "word/document.xml:p003911": ("CBG,",), "word/document.xml:p003912": ("",),
    "word/document.xml:p003915": ("",), "word/document.xml:p003916": ("",), "word/document.xml:p003917": ("",),
    "word/document.xml:p003921": ("",), "word/document.xml:p003922": ("",), "word/document.xml:p003923": ("",),
    "word/document.xml:p003926": ("Ground Floor,",), "word/document.xml:p003933": ("Unit no.",),
}
ADDRESS_END_MARKERS = {
    "word/document.xml:p003802": " Telephone:",
    "word/document.xml:p003819": " Telephone:",
    "word/document.xml:p003833": " Telephone:",
}


def _patterns(values: tuple[str, ...]) -> list[re.Pattern[str]]:
    patterns = []
    for value in sorted(values, key=len, reverse=True):
        tokens = value.split()
        expression = r"\s*".join(re.escape(token) for token in tokens)
        patterns.append(re.compile(rf"(?<![\w@.]){expression}(?![\w])", re.I))
    return patterns


def _add_matches(rows: list[dict[str, object]], record: dict[str, object], pii_type: str, patterns: list[re.Pattern[str]]) -> None:
    text = str(record["text"])
    occupied: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in occupied):
                continue
            occupied.append((match.start(), match.end()))
            rows.append({
                "record_id": record["record_id"], "part": record.get("part", "word/document.xml"),
                "start": match.start(), "end": match.end(), "type": pii_type,
                "text": match.group(0), "stratum": record.get("stratum", ""),
            })


def curate(sample_path: Path, output_path: Path) -> None:
    sample = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows: list[dict[str, object]] = []
    patterns_by_type = {
        "PERSON": _patterns(PEOPLE), "COMPANY": _patterns(COMPANIES),
        "EMAIL": _patterns(EMAILS), "PHONE": _patterns(PHONES),
        **{pii_type: _patterns(values) for pii_type, values in IDENTIFIERS.items()},
    }
    for record in sample:
        for pii_type, patterns in patterns_by_type.items():
            _add_matches(rows, record, pii_type, patterns)
        record_id = str(record["record_id"])
        text = str(record["text"])
        for marker in ADDRESS_SLICES.get(record_id, ()):
            start = text.index(marker) if marker else 0
            # For two inline addresses, stop before the next address label.
            if record_id == "word/document.xml:p000186" and marker.startswith("11/3"):
                end = text.index(" and its Corporate Office", start)
            elif record_id in ADDRESS_END_MARKERS:
                end = text.index(ADDRESS_END_MARKERS[record_id], start)
            else:
                end = len(text)
            while end > start and text[end - 1] in " .;\t\r\n":
                end -= 1
            rows.append({
                "record_id": record_id, "part": record.get("part", "word/document.xml"),
                "start": start, "end": end, "type": "POSTAL_ADDRESS", "text": text[start:end],
                "stratum": record.get("stratum", ""),
            })
    rows.sort(key=lambda item: (str(item["record_id"]), int(item["start"]), -int(item["end"]), str(item["type"])))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} independently anchored gold spans to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=Path, default=Path("evaluation/gold/rhp_sample.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/gold/rhp_gold.jsonl"))
    args = parser.parse_args()
    curate(args.sample, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
