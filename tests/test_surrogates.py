import re

from pii_redactor.detectors.structured import luhn_valid, verhoeff_valid
from pii_redactor.spans import Span
from pii_redactor.surrogates import SurrogateStore


def span(value: str, pii_type: str, **metadata: object) -> Span:
    return Span(0, len(value), pii_type, value, "test", 0.99, metadata=metadata)


def test_person_variants_share_a_canonical_fake() -> None:
    store = SurrogateStore(42)
    full = store.replacement_for(span("Kushal Subbayya Hegde", "PERSON", canonical="Kushal Subbayya Hegde"))
    upper = store.replacement_for(span("KUSHAL SUBBAYYA HEGDE", "PERSON", canonical="Kushal Subbayya Hegde"))
    surname = store.replacement_for(span("Hegde", "PERSON", canonical="Kushal Subbayya Hegde"))
    assert upper == full.upper()
    assert surname.casefold() == full.split()[-1].casefold()


def test_credit_card_surrogate_is_luhn_valid_and_formatted() -> None:
    fake = SurrogateStore(42).replacement_for(span("4111 1111 1111 1111", "CREDIT_CARD"))
    assert fake.count(" ") == 3
    assert luhn_valid(fake)
    assert fake != "4111 1111 1111 1111"


def test_aadhaar_surrogate_is_verhoeff_valid() -> None:
    fake = SurrogateStore(42).replacement_for(span("2363 6804 3103", "AADHAAR"))
    assert verhoeff_valid(fake)


def test_deterministic_across_stores() -> None:
    value = span("person@example.com", "EMAIL")
    assert SurrogateStore(7).replacement_for(value) == SurrogateStore(7).replacement_for(value)
    assert SurrogateStore(7).replacement_for(value) != SurrogateStore(8).replacement_for(value)


def test_phone_grouping_variants_share_digits_and_keep_area_code() -> None:
    store = SurrogateStore(42)
    compact = store.replacement_for(span("+91 20 45053237", "PHONE"))
    spaced = store.replacement_for(span("+ 91 20 4505 3237", "PHONE"))
    assert compact.startswith("+91 20 ")
    assert re.sub(r"\D", "", compact) == re.sub(r"\D", "", spaced)


def test_north_american_phone_masks_share_one_fake_number() -> None:
    store = SurrogateStore(42)
    parenthesized = store.replacement_for(span("(800) 285-7772", "PHONE"))
    hyphenated = store.replacement_for(span("800-285-7772", "PHONE"))
    assert re.sub(r"\D", "", parenthesized) == re.sub(r"\D", "", hyphenated)
    assert re.fullmatch(r"\(\d{3}\) \d{3}-\d{4}", parenthesized)
    assert re.fullmatch(r"\d{3}-\d{3}-\d{4}", hyphenated)


def test_urls_share_domains_by_registrable_host_and_are_injective() -> None:
    store = SurrogateStore(42)
    root = store.replacement_for(span("www.microsoft.com", "URL"))
    path = store.replacement_for(span("https://www.microsoft.com/investor", "URL"))
    code = store.replacement_for(span("Code.org", "URL"))
    outlook = store.replacement_for(span("Outlook.com", "URL"))
    assert path == root + "/investor"
    assert code != outlook
    store.assert_url_injective()


def test_url_hosts_are_plausible_and_only_numbered_on_collision() -> None:
    store = SurrogateStore(42)
    first = store.replacement_for(span("www.microsoft.com", "URL"))
    second = store.replacement_for(span("www.example.org", "URL"))
    assert not re.search(r"-[0-9a-f]{12}\.example\.com", first)
    assert first != second


def test_url_injectivity_assertion_fails_loudly() -> None:
    store = SurrogateStore(42)
    store.base_values[("URL", "code.org")] = "https://same.example.com"
    store.base_values[("URL", "outlook.com")] = "https://same.example.com"
    import pytest
    with pytest.raises(AssertionError, match="URL surrogate collision"):
        store.assert_url_injective()


def test_addresses_are_injective_and_po_box_shape_is_preserved() -> None:
    store = SurrogateStore(42)
    first = store.replacement_for(
        span("One Microsoft Way\nRedmond, Washington 98052-6399", "POSTAL_ADDRESS")
    )
    second = store.replacement_for(
        span("P.O. Box 505000\nLouisville, KY 40233-5000", "POSTAL_ADDRESS")
    )
    assert first != second
    assert second.startswith("P.O. Box ")
    assert re.search(r"\d{5}-\d{4}$", first)
    assert re.search(r"\d{5}-\d{4}$", second)
    store.assert_globally_injective()


def test_global_injectivity_assertion_covers_all_labels() -> None:
    import pytest

    store = SurrogateStore(42)
    store.replacement_for(span("Alice Stone", "PERSON"))
    store.replacement_for(span("Acme Corporation", "COMPANY"))
    entries = list(store.entries.values())
    entries[1].replacement = entries[0].replacement
    with pytest.raises(AssertionError, match="global surrogate collision"):
        store.assert_globally_injective()


def test_cin_surrogate_retains_valid_shape() -> None:
    fake = SurrogateStore(42).replacement_for(span("U28129PN1979PLC141032", "CIN"))
    assert re.fullmatch(r"U\d{5}[A-Z]{2}\d{4}PLC\d{6}", fake)


def test_email_reuses_known_person_surrogate_tokens() -> None:
    store = SurrogateStore(42)
    fake_person = store.replacement_for(span("Sarthak Malvadkar", "PERSON"))
    fake_email = store.replacement_for(span("sarthak.malvadkar@example.org", "EMAIL"))
    tokens = fake_person.split()
    first, last = tokens[0].casefold(), tokens[-1].casefold()
    assert fake_email.startswith(f"{first}.{last}@")


def test_person_and_email_generation_avoid_source_surnames() -> None:
    protected = {"Shah", "Joshi", "List"}
    store = SurrogateStore(42, protected)
    person = store.replacement_for(span("Teri L. List", "PERSON"))
    email = store.replacement_for(span("contact@example.org", "EMAIL"))
    assert person.split()[-1].casefold() not in {value.casefold() for value in protected}
    assert email.split("@", 1)[0].split(".")[-1].casefold() not in {
        value.casefold() for value in protected
    }
