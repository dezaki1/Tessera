import pytest

from ehds.disclosure import disclose
from ehds.engine import Engine, SpecError
from ehds.spec import (
    AgeBand,
    AgeCriterion,
    ConditionCriterion,
    DataHolderCriterion,
    Dimension,
    EncounterCriterion,
    EncounterFilter,
    MedicationCriterion,
    Metric,
    ObservationCriterion,
    QuerySpec,
    RegionCriterion,
    SexCriterion,
    ValueSource,
)

from . import oracle


@pytest.fixture(scope="module")
def engine(store):
    return Engine(store)


def counts(raw, metric):
    return {g: c.counts.get(metric, 0) for g, c in raw.cells.items()}


def test_prevalence_by_region(engine):
    adult = AgeCriterion(min=18)
    spec = QuerySpec(
        dimensions=[Dimension(kind="region")],
        metrics=[
            Metric(name="adults", label="adults", kind="count", criteria=[adult]),
            Metric(name="t2d", label="t2d", kind="count",
                   criteria=[adult, ConditionCriterion(icd10_prefixes=["E11"])]),
            Metric(name="prev", label="prev", kind="percentage", numerator="t2d", denominator="adults"),
        ],
    )
    raw, sqls = engine.run(spec)
    p, *_ = oracle.frames()
    adults = p[p.age >= 18]
    t2d = adults[adults.patient_id.isin(oracle.with_condition("E11"))]
    assert counts(raw, "adults") == {(r,): n for r, n in adults.region.value_counts().items()}
    assert counts(raw, "t2d") == {(r,): n for r, n in t2d.region.value_counts().items()}
    assert len(sqls) == 1 and "E11" in sqls[0]


def test_negated_active_medication_by_age_band(engine):
    bands = [(65, 74), (75, 84), (85, None)]
    af = [ConditionCriterion(icd10_prefixes=["I48"])]
    spec = QuerySpec(
        dimensions=[Dimension(kind="age_band", bands=[
            AgeBand(label=f"{lo}-{hi}" if hi else f"{lo}+", min=lo, max=hi) for lo, hi in bands])],
        metrics=[
            Metric(name="af", label="af", kind="count", criteria=af),
            Metric(name="no_oac", label="no oac", kind="count", criteria=af + [
                MedicationCriterion(atc_prefixes=["B01AA", "B01AF"], active=True, negate=True)]),
        ],
    )
    raw, _ = engine.run(spec)
    p, *_ = oracle.frames()
    pts = p[p.patient_id.isin(oracle.with_condition("I48"))].copy()
    pts["band"] = pts.age.map(lambda a: oracle.band(a, bands))
    oac = oracle.with_active_medication("B01AA", "B01AF")
    for i, label in enumerate(["65-74", "75-84", "85+"]):
        group = pts[pts.band == i]
        assert raw.cells[(label,)].counts["af"] == len(group)
        assert raw.cells[(label,)].counts["no_oac"] == (~group.patient_id.isin(oac)).sum()


def test_mean_of_latest_measurement(engine):
    crit = [SexCriterion(values=["female"]), ConditionCriterion(icd10_prefixes=["C50"]),
            ObservationCriterion(loinc="39156-5")]
    spec = QuerySpec(
        dimensions=[Dimension(kind="region")],
        metrics=[
            Metric(name="n", label="n", kind="count", criteria=crit),
            Metric(name="bmi", label="bmi", kind="mean", criteria=crit,
                   value=ValueSource(source="observation", loinc="39156-5")),
        ],
    )
    raw, _ = engine.run(spec)
    p, *_ = oracle.frames()
    women = p[(p.sex == "female") & p.patient_id.isin(oracle.with_condition("C50"))]
    bmi = oracle.latest_value("39156-5")
    women = women[women.patient_id.isin(bmi.index)]
    for region, group in women.groupby("region"):
        mean = raw.cells[(region,)].means["bmi"]
        assert mean.n == len(group) == raw.cells[(region,)].counts["n"]
        assert float(mean.total) == pytest.approx(bmi[group.patient_id].sum())


def test_count_stays_by_region_and_hospital(engine):
    stays = [EncounterCriterion(filter=EncounterFilter(
        types=["inpatient"], date_from="2025-01-01", date_to="2025-12-31"))]
    spec = QuerySpec(
        dimensions=[Dimension(kind="region"), Dimension(kind="data_holder")],
        metrics=[Metric(name="stays", label="stays", kind="count", unit="encounter", criteria=stays)],
    )
    raw, _ = engine.run(spec)
    p, _, _, _, e = oracle.frames()
    e = e[(e.type == "inpatient") & e.start_date.str.startswith("2025")]
    e = e.merge(p[["patient_id", "region"]], on="patient_id")
    expected = e.groupby(["region", "data_holder"]).size().to_dict()
    assert len(raw.cells) == 9 * 3
    for group, cell in raw.cells.items():
        assert cell.counts["stays"] == expected.get(group, 0)


def test_primary_diagnosis_filter_and_length_of_stay(engine):
    crit = [EncounterCriterion(filter=EncounterFilter(types=["inpatient"], primary_icd10_prefixes=["S72"]))]
    spec = QuerySpec(
        dimensions=[Dimension(kind="data_holder")],
        metrics=[
            Metric(name="stays", label="stays", kind="count", unit="encounter", criteria=crit),
            Metric(name="los", label="los", kind="mean", unit="encounter", criteria=crit,
                   value=ValueSource(source="length_of_stay")),
        ],
    )
    raw, _ = engine.run(spec)
    _, _, _, _, e = oracle.frames()
    e = e[(e.type == "inpatient") & e.primary_icd10.str.startswith("S72")].copy()
    e["los"] = (pd_dates(e.end_date) - pd_dates(e.start_date)).dt.days
    for holder, group in e.groupby("data_holder"):
        cell = raw.cells[(holder,)]
        assert cell.counts["stays"] == len(group)
        assert int(cell.means["los"].total) == group.los.sum()
        assert cell.means["los"].n_patients == group.patient_id.nunique()


def pd_dates(s):
    import pandas as pd
    return pd.to_datetime(s)


def test_empty_groups_are_present(engine):
    spec = QuerySpec(
        dimensions=[Dimension(kind="age_band", bands=[AgeBand(label="0-9", min=0, max=9),
                                                       AgeBand(label="200+", min=150)])],
        metrics=[Metric(name="copd", label="copd", kind="count",
                        criteria=[ConditionCriterion(icd10_prefixes=["J44"])])],
    )
    raw, _ = engine.run(spec)
    assert set(raw.cells) == {("0-9",), ("200+",)}
    answer = disclose(raw).to_nested()
    assert answer == {"0-9": {"copd": "<10"}, "200+": {"copd": "<10"}}


def test_filters_on_region_and_hospital(engine):
    spec = QuerySpec(metrics=[Metric(name="n", label="n", kind="count", criteria=[
        RegionCriterion(values=["wien"]), DataHolderCriterion(values=["Klinikum Nord"])])])
    raw, _ = engine.run(spec)
    p, *_ = oracle.frames()
    assert raw.cells[()].counts["n"] == ((p.region == "Wien") & (p.data_holder == "Klinikum Nord")).sum()


def test_unknown_values_are_rejected(engine):
    spec = QuerySpec(metrics=[Metric(name="n", label="n", kind="count",
                                     criteria=[RegionCriterion(values=["Atlantis'; DROP TABLE patients; --"])])])
    with pytest.raises(SpecError):
        engine.run(spec)


def test_visit_grouping_needs_visit_metric(engine):
    spec = QuerySpec(dimensions=[Dimension(kind="encounter_year")],
                     metrics=[Metric(name="n", label="n", kind="count")])
    with pytest.raises(SpecError):
        engine.run(spec)
