from pii_redactor.detectors.base import DetectionContext
from pii_redactor.detectors.structured import (
    StructuredDetector,
    known_card_issuer,
    luhn_valid,
    verhoeff_valid,
)


def types(text: str, context: DetectionContext | None = None) -> set[str]:
    return {span.pii_type for span in StructuredDetector().detect(text, context)}


def test_email_and_explicit_indian_phones() -> None:
    text = "E-mail: person@example.com; Tel: + 91 20 45053237; Mobile: 9876543210"
    spans = StructuredDetector().detect(text)
    assert [span.pii_type for span in spans].count("EMAIL") == 1
    assert [span.pii_type for span in spans].count("PHONE") == 2


def test_url_split_by_a_word_run_space_is_one_span() -> None:
    spans = [span for span in StructuredDetector().detect("www.kshinternational. com") if span.pii_type == "URL"]
    assert len(spans) == 1
    assert spans[0].text == "www.kshinternational. com"


def test_regulator_and_exchange_urls_are_policy_exclusions() -> None:
    text = "https://www.sebi.gov.in, www.sec.gov and www.bseindia.com but www.issuer.example.com"
    urls = [span.text for span in StructuredDetector().detect(text) if span.pii_type == "URL"]
    assert urls == ["www.issuer.example.com"]


def test_url_does_not_cross_tab_or_consume_sentence_punctuation() -> None:
    text = "2.\tCompensation Committee and www.microsoft.com. At our office"
    urls = [span.text for span in StructuredDetector().detect(text) if span.pii_type == "URL"]
    assert urls == ["www.microsoft.com"]


def test_bare_name_tld_is_not_a_url_without_path() -> None:
    text = "Outlook.com Code.org StopNCII.org microsoft.com/investor"
    urls = [span.text for span in StructuredDetector().detect(text) if span.pii_type == "URL"]
    assert urls == ["microsoft.com/investor"]


def test_unlabelled_ten_digit_artifact_is_not_phone() -> None:
    assert "PHONE" not in types("Order 6350960932 and share count 661973547370")


def test_guarded_north_american_phones() -> None:
    text = "Call (800) 285-7772, fax (425) 706-4400, or use 800-285-7772. Order 8002857772."
    phones = [span.text for span in StructuredDetector().detect(text) if span.pii_type == "PHONE"]
    assert phones == ["(800) 285-7772", "(425) 706-4400", "800-285-7772"]


def test_luhn_and_issuer_validation() -> None:
    assert luhn_valid("4111 1111 1111 1111")
    assert known_card_issuer("4111 1111 1111 1111")
    assert "CREDIT_CARD" in types("Card 4111 1111 1111 1111")
    assert "CREDIT_CARD" not in types("Ticket 4111 1111 1111 1112")
    assert "CREDIT_CARD" not in types("Layout 1094535838831")


def test_ssn_invalid_ranges() -> None:
    assert "SSN" in types("SSN 123-45-6789")
    assert "SSN" not in types("SSN 000-45-6789")
    assert "SSN" not in types("SSN 666-45-6789")
    assert "SSN" not in types("SSN 901-45-6789")


def test_ip_validation() -> None:
    detected = types("IPs 192.168.10.24 and 2001:db8::8a2e:370:7334")
    assert "IP_ADDRESS" in detected
    assert len([span for span in StructuredDetector().detect("192.168.10.24 2001:db8::1") if span.pii_type == "IP_ADDRESS"]) == 2
    assert "IP_ADDRESS" not in types("Version 999.10.10.10")


def test_din_requires_context() -> None:
    detector = StructuredDetector()
    assert any(span.pii_type == "DIN" for span in detector.detect("DIN: 00135070"))
    assert not any(span.pii_type == "DIN" for span in detector.detect("Page 00135070"))
    context = DetectionContext(metadata={"table_headers": ["Name", "DIN"]})
    assert any(span.pii_type == "DIN" for span in detector.detect("00135070", context))


def test_sebi_registration_is_not_misclassified_as_bank_account() -> None:
    spans = StructuredDetector().detect("Contact Person: Asha Rao SEBI Registration No.: INR000004058")
    assert any(span.pii_type == "SEBI_REG_NO" for span in spans)
    assert not any(span.pii_type == "BANK_ACCOUNT" for span in spans)


def test_verhoeff_known_valid_and_invalid() -> None:
    # UIDAI documentation commonly uses this Verhoeff-valid demonstration value.
    assert verhoeff_valid("2363 6804 3103")
    assert not verhoeff_valid("2363 6804 3104")
