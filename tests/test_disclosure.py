from decimal import Decimal

import pytest

from ehds.disclosure import (
    LESS_THAN,
    SUPPRESSED,
    MetricDef,
    RawCell,
    RawMean,
    RawResult,
    SafeAnswer,
    disclose,
    find_violations,
    round_half_up,
    safe_count,
    safe_mean,
    safe_percentage,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("0.05", 0.1),
        ("0.15", 0.2),  # round(0.15, 1) gives 0.1: the float is 0.1499...
        ("0.25", 0.3),  # round(0.25, 1) gives 0.2: banker's rounding
        ("2.675", 2.7),
        ("37.05", 37.1),
        ("37.04999", 37.0),
        ("-0.05", -0.1),
        ("26.6", 26.6),
    ],
)
def test_round_half_up(value, expected):
    assert round_half_up(Decimal(value)) == expected


@pytest.mark.parametrize("n, expected", [(0, LESS_THAN), (1, LESS_THAN), (9, LESS_THAN), (10, 10), (532, 532)])
def test_counts(n, expected):
    assert safe_count(n) == expected


def test_percentage_rounds_from_exact_fraction():
    # 30 / 81 = 37.037..., 741 / 2000 = 37.05 exactly -> 37.1 (half up)
    assert safe_percentage(30, 81) == 37.0
    assert safe_percentage(741, 2000) == 37.1
    assert safe_percentage(33, 532) == 6.2


@pytest.mark.parametrize("num, den", [(9, 100), (0, 100), (10, 9), (0, 0), (5, 5)])
def test_percentage_suppressed_when_numerator_or_denominator_small(num, den):
    assert safe_percentage(num, den) == SUPPRESSED


def test_percentage_at_threshold_is_shown():
    assert safe_percentage(10, 10) == 100.0


def test_mean():
    assert safe_mean(RawMean(Decimal("408.1"), 15, 15)) == 27.2  # 27.2066...
    assert safe_mean(RawMean(Decimal("100.5"), 10, 10)) == 10.1  # 10.05 exactly -> half up
    assert safe_mean(RawMean(Decimal("98.1"), 9, 9)) == SUPPRESSED
    assert safe_mean(RawMean(Decimal(0), 0, 0)) == SUPPRESSED
    # 12 values, but from only 4 patients
    assert safe_mean(RawMean(Decimal("120"), 12, 4)) == SUPPRESSED


def _raw(cells, labels=(["A", "B", "C"],)):
    return RawResult(
        dimensions=["region"],
        group_labels=[list(x) for x in labels],
        metrics=[
            MetricDef("all", "all", "count"),
            MetricDef("cases", "cases", "count"),
            MetricDef("hidden", "hidden", "count", visible=False),
            MetricDef("share", "share", "percentage", numerator="cases", denominator="all"),
            MetricDef("hidden_pct", "hidden %", "percentage", numerator="hidden", denominator="all"),
            MetricDef("mean_x", "mean x", "mean"),
        ],
        cells=cells,
    )


def test_every_requested_group_is_listed_even_if_empty():
    answer = disclose(_raw({("A",): RawCell(counts={"all": 100, "cases": 20, "hidden": 50})}))
    nested = answer.to_nested()
    assert list(nested) == ["A", "B", "C"]
    assert nested["B"] == {
        "all": LESS_THAN,
        "cases": LESS_THAN,
        "share": SUPPRESSED,
        "hidden_pct": SUPPRESSED,
        "mean_x": SUPPRESSED,
    }
    assert nested["A"]["share"] == 20.0
    assert nested["A"]["hidden_pct"] == 50.0
    assert "hidden" not in nested["A"]


def test_hidden_numerator_below_threshold_suppresses_percentage():
    answer = disclose(_raw({("A",): RawCell(counts={"all": 100, "cases": 20, "hidden": 3})}))
    assert answer.to_nested()["A"]["hidden_pct"] == SUPPRESSED


def test_cross_product_of_dimensions():
    raw = _raw({}, labels=(["A", "B"], ["18-49", "50+"]))
    raw.dimensions = ["region", "age_band"]
    answer = disclose(raw)
    assert len(answer.rows) == 4
    assert set(answer.to_nested()["B"]) == {"18-49", "50+"}


def test_groups_outside_domain_are_rejected():
    with pytest.raises(ValueError):
        disclose(_raw({("Atlantis",): RawCell(counts={"all": 50})}))


def test_safe_answer_cannot_be_built_directly():
    with pytest.raises(TypeError):
        SafeAnswer(dimensions=(), columns=(), rows=())


def test_raw_result_repr_hides_values():
    raw = _raw({("A",): RawCell(counts={"all": 3})})
    assert "3" not in repr(raw).replace("cells=1", "")


def test_find_violations_flags_small_counts():
    answer = disclose(_raw({("A",): RawCell(counts={"all": 100, "cases": 20})}))
    assert find_violations(answer) == []
    tampered = SafeAnswer.__new__(SafeAnswer)
    object.__setattr__(tampered, "dimensions", answer.dimensions)
    object.__setattr__(tampered, "columns", answer.columns)
    object.__setattr__(tampered, "rows", ((("A",), (7, 20, 20.0, 50.0, SUPPRESSED)),))
    assert find_violations(tampered) == ["A.all: count 7 is below 10"]


def test_no_groups_gives_flat_answer():
    raw = RawResult(
        dimensions=[], group_labels=[], metrics=[MetricDef("cohort", "cohort", "count")],
        cells={(): RawCell(counts={"cohort": 1152})},
    )
    assert disclose(raw).to_nested() == {"cohort": 1152}
