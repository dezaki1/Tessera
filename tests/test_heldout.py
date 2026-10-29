"""Held-out requests (examples/heldout) checked end to end against the pandas oracle.

They use things none of the 10 sample requests do: grouping by hospital and
sex, filtering stays by primary_icd10, names without codes, thresholds,
region filters from the question, a different SPE cohort, and a new
prohibited purpose. Expected values are computed independently here.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import pytest

from ehds.pipeline import AccessBody

from . import oracle

HELDOUT = Path(__file__).resolve().parent.parent / "examples" / "heldout"


def small(n):
    return "<10" if n < 10 else int(n)


def pct(num, den):
    if num < 10 or den < 10:
        return "suppressed"
    return float((Decimal(int(num)) * 100 / Decimal(int(den))).quantize(Decimal("0.1"), ROUND_HALF_UP))


def mean(values):
    if len(values) < 10:
        return "suppressed"
    total = sum(Decimal(str(v)) for v in values)
    return float((total / len(values)).quantize(Decimal("0.1"), ROUND_HALF_UP))


@pytest.fixture(scope="module")
def body(store, tmp_path_factory):
    return AccessBody(store=store, workdir=tmp_path_factory.mktemp("heldout"))


def run(body, rid):
    result = body.process(json.loads((HELDOUT / f"{rid}.json").read_text()))
    assert not result["staff"]["parse"]["unresolved"], result["staff"]["parse"]["unresolved"]
    return result


def values(answer_cell):
    return list(answer_cell.values())


def test_h01_stays_by_hospital_with_primary_diagnosis(body):
    r = run(body, "H01")
    p, _, _, _, e = oracle.frames()
    e = e[(e.type == "inpatient") & e.primary_icd10.str.startswith("S72") & e.start_date.str.startswith("2025")]
    for holder in ("Klinikum Nord", "Klinikum Süd", "Klinikum West"):
        assert values(r["answer"][holder]) == [small((e.data_holder == holder).sum())]


def test_h02_mean_egfr_by_hospital_and_sex(body):
    r = run(body, "H02")
    p, *_ = oracle.frames()
    egfr = oracle.latest_value("62238-1")
    pts = p[(p.age >= 18) & p.patient_id.isin(oracle.with_condition("I50")) & p.patient_id.isin(egfr.index)]
    for holder in ("Klinikum Nord", "Klinikum Süd", "Klinikum West"):
        for sex in ("female", "male"):
            g = pts[(pts.data_holder == holder) & (pts.sex == sex)]
            assert values(r["answer"][holder][sex]) == [small(len(g)), mean(list(egfr[g.patient_id]))]


def test_h03_share_without_codes(body):
    r = run(body, "H03")
    p, *_ = oracle.frames()
    t2d = p[(p.age >= 18) & p.patient_id.isin(oracle.with_condition("E11"))]
    sglt2 = oracle.with_active_medication("A10BK")
    for holder, g in t2d.groupby("data_holder"):
        n, k = len(g), g.patient_id.isin(sglt2).sum()
        assert values(r["answer"][holder]) == [small(n), small(k), pct(k, n)]


def test_h04_main_diagnosis_by_name(body):
    r = run(body, "H04")
    p, _, _, _, e = oracle.frames()
    e = e[(e.type == "inpatient") & e.primary_icd10.str.startswith("J18") & e.start_date.str.startswith("2024")]
    e = e.merge(p[["patient_id", "region"]], on="patient_id")
    for region in sorted(p.region.unique()):
        assert values(r["answer"][region]) == [small((e.region == region).sum())]


def test_h05_region_filter_from_question(body):
    r = run(body, "H05")
    p, *_ = oracle.frames()
    g = p[(p.region == "Tirol") & p.patient_id.isin(oracle.with_condition("J45"))]
    bands = {"0-17": (0, 17), "18-64": (18, 64), "65+": (65, 200)}
    for label, (lo, hi) in bands.items():
        assert values(r["answer"][label]) == [small(((g.age >= lo) & (g.age <= hi)).sum())]


def test_h06_spe_cohort_and_tables(body):
    r = run(body, "H06")
    assert r["decision"] == "APPROVE_SPE"
    p, *_ = oracle.frames()
    expected = ((p.age >= 18) & p.patient_id.isin(oracle.with_condition("I48"))).sum()
    assert r["answer"] == {"cohort_size": int(expected)}
    assert set(r["spe"]["tables"]) == {"cohort", "conditions", "medications", "encounters"}


def test_h07_alcohol_marketing_is_rejected(body):
    r = body.process(json.loads((HELDOUT / "H07.json").read_text()))
    assert r["decision"] == "REJECT" and r["articles"] == ["54"] and r["answer"] is None


def test_h08_threshold_by_hospital_and_sex(body):
    r = run(body, "H08")
    p, *_ = oracle.frames()
    hba1c = oracle.latest_value("4548-4")
    base = p[(p.age >= 18) & p.patient_id.isin(oracle.with_condition("E11")) & p.patient_id.isin(hba1c.index)]
    for (holder, sex), g in base.groupby(["data_holder", "sex"]):
        n = len(g)
        k = (hba1c[g.patient_id] > 8).sum()
        assert values(r["answer"][holder][sex]) == [small(n), small(k), pct(k, n)]
