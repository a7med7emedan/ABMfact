from __future__ import annotations

import pytest

from abmfact.claims import ClaimError, Metric, parse_claim, parse_claims

OUT = {"a": 2.0, "s": [1.0, 3.0, 2.0]}


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("a", 2.0),
        ("s[0]", 1.0),
        ("s[-1]", 2.0),
        ("mean(s)", 2.0),
        ("sum(s)", 6.0),
        ("min(s)", 1.0),
        ("max(s)", 3.0),
        ("argmax(s)", 1.0),
        ("argmin(s)", 0.0),
        ("last(s)", 2.0),
        ("first(s)", 1.0),
        ("std(s)", 1.0),
        ("max(a)", 2.0),
        ("std(a)", 0.0),
    ],
)
def test_metric_grammar(expr, value):
    assert Metric.parse(expr).evaluate(OUT) == pytest.approx(value)


@pytest.mark.parametrize("expr", ["a + 1", "__import__('os')", "foo(s)", "s[1:2]", "", "1a"])
def test_metric_rejects_anything_else(expr):
    with pytest.raises(ClaimError):
        Metric.parse(expr)


def test_metric_rejects_non_string():
    with pytest.raises(ClaimError):
        Metric.parse(3)  # type: ignore[arg-type]


def test_metric_evaluation_errors():
    with pytest.raises(ClaimError, match="needs output"):
        Metric.parse("b").evaluate(OUT)
    with pytest.raises(ClaimError, match="series"):
        Metric.parse("s").evaluate(OUT)
    with pytest.raises(ClaimError, match="out of range"):
        Metric.parse("s[7]").evaluate(OUT)
    with pytest.raises(ClaimError, match="empty"):
        Metric.parse("mean(e)").evaluate({"e": []})


def test_parse_threshold_and_points():
    c = parse_claim({"id": "C1", "metric": "a", "test": "greater_than", "value": 1, "where": {"b": [1, 2], "c": 3}})
    assert c.value == 1.0
    pts = c.points({"b": 0, "z": 9})
    assert pts == [{"b": 1, "c": 3, "z": 9}, {"b": 2, "c": 3, "z": 9}]
    assert c.to_dict()["where"] == {"b": [1, 2], "c": [3]}
    assert parse_claim({"id": "C", "metric": "a", "test": "less_than", "value": 1}).points({"q": 1}) == [{"q": 1}]


def test_parse_between_and_monotone():
    b = parse_claim({"id": "B", "metric": "a", "test": "between", "value": [0, 1], "tolerance": 0.1})
    assert b.value == (0.0, 1.0)
    assert b.to_dict()["value"] == [0.0, 1.0]
    m = parse_claim({"id": "M", "metric": "a", "test": "monotone_decreasing", "over": "k", "where": {"k": [1, 2]}})
    assert m.is_monotone and m.to_dict()["over"] == "k"


@pytest.mark.parametrize(
    "raw",
    [
        {"metric": "a", "test": "greater_than", "value": 1},
        {"id": "X", "metric": "a", "test": "bigger", "value": 1},
        {"id": "X", "metric": "a", "test": "greater_than"},
        {"id": "X", "metric": "a", "test": "between", "value": [2, 1]},
        {"id": "X", "metric": "a", "test": "between", "value": 3},
        {"id": "X", "metric": "a", "test": "monotone_increasing"},
        {"id": "X", "metric": "a", "test": "monotone_increasing", "over": "k", "where": {"k": [1]}},
        {"id": "X", "metric": "a", "test": "monotone_increasing", "over": "k", "where": {"k": ["a", "b"]}},
        {"id": "X", "metric": "a", "test": "greater_than", "value": 1, "tolerance": 0},
        {"id": "X", "metric": "a", "test": "greater_than", "value": 1, "skip_checks": {"nope": "x"}},
    ],
)
def test_parse_claim_errors(raw):
    with pytest.raises(ClaimError):
        parse_claim(raw, known_checks=["grid"])


def test_parse_claims_errors():
    with pytest.raises(ClaimError):
        parse_claims([])
    with pytest.raises(ClaimError, match="duplicate"):
        parse_claims([{"id": "A", "metric": "a", "test": "less_than", "value": 1}] * 2)
    with pytest.raises(ClaimError):
        parse_claim("nope")  # type: ignore[arg-type]
