from dataclasses import dataclass

from pii_redactor.detectors.glossary import Allowlist
from pii_redactor.detectors.ner import SpacyNerDetector
from pii_redactor.detectors.base import DetectionContext


@dataclass
class FakeEntity:
    text: str
    label_: str
    start_char: int
    end_char: int


class FakeDoc:
    def __init__(self, entities: list[FakeEntity]) -> None:
        self.ents = entities


def detector_for(text: str, entity_text: str, label: str, lowercase: set[str]) -> SpacyNerDetector:
    detector = SpacyNerDetector(Allowlist(), lowercase_vocabulary=lowercase)
    start = text.index(entity_text)
    detector.nlp = lambda _text: FakeDoc([FakeEntity(entity_text, label, start, start + len(entity_text))])
    detector.model_name = "fake"
    return detector


def test_corpus_lowercase_vocabulary_rejects_customer_zero() -> None:
    text = "Customer Zero improves our zero waste program"
    detector = detector_for(text, "Customer Zero", "PERSON", {"customer", "zero", "waste"})
    assert detector.detect(text) == []


def test_box_and_statute_person_false_positives_are_rejected() -> None:
    box = "P.O. Box 505000"
    assert detector_for(box, "P.O. Box", "PERSON", set()).detect(box) == []
    statute = "One Big Beautiful Bill Act"
    detector = detector_for(statute, "Bill Act", "PERSON", {"bill", "act"})
    assert detector.detect(statute) == []


def test_person_entity_expands_to_full_surname() -> None:
    text = "Teri L. List 1,3"
    detector = detector_for(text, "Teri L.", "PERSON", {"list"})
    spans = detector.detect(text)
    assert len(spans) == 1
    assert spans[0].text == "Teri L. List"


def test_table_person_footnote_is_trimmed_without_rejecting_name() -> None:
    text = "Mark A. L. Mason 3"
    detector = detector_for(text, text, "PERSON", set())
    context = DetectionContext(metadata={"cell_key": "word/document.xml:t1:r2:c2"})
    spans = detector.detect(text, context)
    assert [span.text for span in spans] == ["Mark A. L. Mason"]
    assert any(
        trace["rule"] == "trailing_table_footnote_trimmed"
        for trace in detector.last_trace
    )


def test_bare_name_tld_is_company_candidate_not_url() -> None:
    for value in ("Outlook.com", "Code.org", "StopNCII.org"):
        detector = detector_for(value, value, "ORG", set())
        spans = detector.detect(value)
        assert any(span.pii_type == "COMPANY" and span.text == value for span in spans)
