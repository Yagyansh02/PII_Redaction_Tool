from pii_redactor.spans import Span, resolve_spans


def make(start: int, end: int, pii_type: str, priority: int, confidence: float = 0.9) -> Span:
    return Span(start, end, pii_type, "x" * (end - start), "test", confidence, priority)


def test_higher_priority_wins_overlap() -> None:
    person = make(0, 12, "PERSON", 80)
    email = make(5, 20, "EMAIL", 100)
    assert resolve_spans([person, email]) == [email]


def test_longer_span_wins_equal_rank() -> None:
    short = make(2, 5, "PERSON", 80)
    long = make(2, 9, "PERSON", 80)
    assert resolve_spans([short, long]) == [long]


def test_non_overlapping_spans_are_ordered() -> None:
    right = make(10, 12, "PHONE", 98)
    left = make(0, 4, "PERSON", 80)
    assert resolve_spans([right, left]) == [left, right]
