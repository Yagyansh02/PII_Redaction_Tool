from pii_redactor.detectors.addresses import AddressDetector
from pii_redactor.detectors.dates import DateOfBirthDetector
from pii_redactor.detectors.gazetteer import GazetteerBuilder
from pii_redactor.detectors.glossary import Allowlist
from pathlib import Path


def test_date_requires_birth_context() -> None:
    detector = DateOfBirthDetector()
    assert detector.detect("Date of birth: 14/09/1987")
    assert not detector.detect("Board resolution dated 14/09/2024")


def test_indian_address_with_label() -> None:
    text = "Registered Office: Flat 8, Lotus Heights, Baner Road, Pune – 411 045, Maharashtra, India\nTelephone: +91 20 12345678"
    spans = AddressDetector().detect(text)
    assert spans
    assert spans[0].text.endswith("India")
    assert "Telephone" not in spans[0].text


def test_company_prefix_is_not_absorbed_into_address() -> None:
    text = "Hingne Tare & Associates Flat No. 102, Sai Complex, Pune – 411 030, Maharashtra, India"
    spans = AddressDetector().detect(text)
    assert spans
    assert spans[0].text.startswith("Flat No. 102")


def test_facility_unit_in_business_prose_is_not_an_address() -> None:
    text = (
        "We intend to utilize portions of the Net Proceeds for funding capital "
        "expenditure requirements of our Company towards purchasing and setting "
        "up new machinery at Unit 2 in Chakan, Pune in Maharashtra. While we "
        "have procured quotations from various vendors, no firm orders have "
        "been placed and the actual requirements may differ from our estimates."
    )
    assert not AddressDetector().detect(text)


def test_short_unlabelled_address_still_matches() -> None:
    text = "Unit no. 1601, B-wing BKC, Mumbai Maharashtra India"
    spans = AddressDetector().detect(text)
    assert len(spans) == 1
    assert spans[0].text == text


def test_us_zip_and_cross_line_addresses() -> None:
    detector = AddressDetector()
    first = "One Microsoft Way\nRedmond, Washington 98052-6399"
    second = "Computershare\nP.O. Box 505000\nLouisville, KY 40233-5000"
    assert any(span.text == first for span in detector.detect(first))
    assert any(span.text == "P.O. Box 505000\nLouisville, KY 40233-5000" for span in detector.detect(second))
    assert any(span.text == "Redmond, Washington 98052-6399" for span in detector.detect(first.splitlines()[1]))


def test_slash_separated_contact_people_are_all_seeded() -> None:
    builder = GazetteerBuilder(
        Allowlist(),
        Path(__file__).resolve().parents[1] / "pii_redactor" / "resources" / "org_suffixes.txt",
    )
    from pii_redactor.detectors.gazetteer import EntityGazetteer

    gazetteer = EntityGazetteer()
    builder._seed_labeled_people(
        gazetteer,
        "Contact Person: Lokesh Shah/ Soumavo Sarkar Website: www.example.com",
    )
    assert {entry.canonical for entry in gazetteer.by_type("PERSON")} == {"Lokesh Shah", "Soumavo Sarkar"}


def test_party_banks_and_single_word_legal_counsel_are_seeded() -> None:
    builder = GazetteerBuilder(
        Allowlist(),
        Path(__file__).resolve().parents[1] / "pii_redactor" / "resources" / "org_suffixes.txt",
    )
    from pii_redactor.detectors.gazetteer import EntityGazetteer

    gazetteer = EntityGazetteer()
    for text in (
        "Citibank N.A.",
        "Export-Import Bank of India",
        "State Bank of India, Industrial Finance Branch",
        "Legal Counsel to our Company as to Indian Law Trilegal",
    ):
        builder._seed_role_organizations(gazetteer, text)
        builder._seed_companies(gazetteer, text)
    assert {entry.canonical for entry in gazetteer.by_type("COMPANY")} == {
        "Citibank N.A.", "Export-Import Bank of India", "State Bank of India", "Trilegal"
    }
