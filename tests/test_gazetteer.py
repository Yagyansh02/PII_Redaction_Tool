from pathlib import Path

from pii_redactor.docx_io import TableData
from pii_redactor.detectors.gazetteer import EntityGazetteer, GazetteerBuilder, GazetteerDetector
from pii_redactor.detectors.glossary import Allowlist


def test_single_surname_does_not_redact_company_or_hospital() -> None:
    gazetteer = EntityGazetteer()
    gazetteer.add("Karunakar N. Bhandary", "PERSON", "test", 0.99)
    gazetteer.add("Rupal K. Sancheti", "PERSON", "test", 0.99)
    gazetteer.expand_variants()
    detector = GazetteerDetector(gazetteer, Allowlist())
    assert not detector.detect("Bhandary Metal Extrusion Private Limited")
    assert not detector.detect("Opposite Sancheti Hospital")
    assert detector.detect("allotted to Bhandary, 500 shares")


def test_international_legal_suffixes_and_ampersands_are_whole_companies() -> None:
    builder = GazetteerBuilder(
        Allowlist(),
        Path(__file__).resolve().parents[1] / "pii_redactor" / "resources" / "org_suffixes.txt",
        "all",
    )
    gazetteer = EntityGazetteer()
    text = (
        "Microsoft Corporation; Deloitte & Touche LLP; Wells Fargo & Company; "
        "Clayton Dubilier & Rice LLC; Citigroup Inc.; GSK plc; Engie S.A."
    )
    builder._seed_companies(gazetteer, text)
    companies = {entry.canonical for entry in gazetteer.by_type("COMPANY")}
    assert companies == {
        "Microsoft Corporation", "Deloitte & Touche LLP", "Wells Fargo & Company",
        "Clayton Dubilier & Rice LLC", "Citigroup Inc.", "GSK plc", "Engie S.A.",
    }
    builder._seed_companies(gazetteer, "Private Limited; India Limited; Alpha Inc. and Beta LLC")
    companies = {entry.canonical for entry in gazetteer.by_type("COMPANY")}
    assert "Private Limited" not in companies
    assert "India Limited" not in companies
    assert "Alpha Inc. and Beta LLC" not in companies
    assert {"Alpha Inc.", "Beta LLC"} <= companies


def test_definition_table_links_company_short_alias() -> None:
    gazetteer = EntityGazetteer()
    gazetteer.add("Nuvama Wealth Management Limited", "COMPANY", "suffix", 0.96)
    table = TableData(
        "word/document.xml",
        "word/document.xml:t0001",
        [["Term", "Description"], ["Nuvama", "Nuvama Wealth Management Limited"]],
    )
    GazetteerBuilder._seed_company_table_aliases(gazetteer, [table])
    entry = gazetteer.by_type("COMPANY")[0]
    assert "Nuvama" in entry.variants
