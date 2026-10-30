"""Stress test: jury-style requests in wording the parser was not built on.

Each case is a request plus an independent pandas calculation of the expected
answer (via tests/oracle.py and the small-cell rule re-implemented here).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from ehds.pipeline import AccessBody

from . import oracle

REGIONS = ["Burgenland", "Kärnten", "Niederösterreich", "Oberösterreich", "Salzburg", "Steiermark",
           "Tirol", "Vorarlberg", "Wien"]
HOSPITALS = ["Klinikum Nord", "Klinikum Süd", "Klinikum West"]
SEXES = ["female", "male"]


# --- independent small-cell rule -----------------------------------------------------

def c(n):
    n = int(n)
    return "<10" if n < 10 else n


def p(num, den):
    num, den = int(num), int(den)
    if num < 10 or den < 10:
        return "suppressed"
    return float((Decimal(num) * 100 / Decimal(den)).quantize(Decimal("0.1"), ROUND_HALF_UP))


def m(values):
    values = [Decimal(str(v)) for v in values]
    if len(values) < 10:
        return "suppressed"
    return float((sum(values) / len(values)).quantize(Decimal("0.1"), ROUND_HALF_UP))


# --- oracle helpers -------------------------------------------------------------------

def pts():
    return oracle.frames()[0]


def stays():
    p_, _, _, _, e = oracle.frames()
    return e.merge(p_[["patient_id", "region", "sex", "age"]], on="patient_id")


def has(*prefixes):
    return pts().patient_id.isin(oracle.with_condition(*prefixes))


def on(*prefixes):
    return pts().patient_id.isin(oracle.with_active_medication(*prefixes))


def bands(df, spec):
    out = {}
    for label, lo, hi in spec:
        out[label] = df[(df.age >= lo) & (df.age <= hi)]
    return out


def by(df, col, domain):
    return {g: df[df[col] == g] for g in domain}


def req(question, definitions, purpose="University research to improve patient care."):
    return {"requested_as": "health data request", "requested_format": "anonymised statistics",
            "purpose": purpose, "question": question, "definitions": definitions,
            "applicant": {"name": "Juror", "organisation": "Jury", "country": "AT"}}


# --- cases: (id, request, function returning {group path: [values]}) -------------------

def s01():
    g = pts()[has("J45")]
    return {(s,): [c(len(x))] for s, x in by(g, "sex", SEXES).items()}


def s02():
    g = pts()[has("I50")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s03():
    e = stays()
    e = e[e.type == "inpatient"].copy()
    e["year"] = e.start_date.str[:4]
    return {(h, y): [c(((e.data_holder == h) & (e.year == y)).sum())]
            for h in HOSPITALS for y in ["2023", "2024", "2025"]}


def s04():
    e = stays()
    e = e[(e.type == "emergency") & e.start_date.str.startswith("2025")]
    return {(h,): [c((e.data_holder == h).sum())] for h in HOSPITALS}


def s05():
    q = pts()
    g = q[(q.sex == "female") & (q.age >= 50) & (q.age <= 69) & has("C50")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s06():
    q = pts()
    g = q[(q.sex == "male") & (q.age >= 65) & has("J44")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s07():
    q = pts()
    base = q[(q.age >= 18) & has("I10")]
    statin = oracle.with_active_medication("C10AA")
    spec = [("18-39", 18, 39), ("40-59", 40, 59), ("60-79", 60, 79), ("80 and over", 80, 200)]
    return {(b,): [c(len(x)), p(x.patient_id.isin(statin).sum(), len(x))] for b, x in bands(base, spec).items()}


def s08():
    q = pts()
    bmi = oracle.latest_value("39156-5")
    g = q[(q.age >= 18) & has("E66") & q.patient_id.isin(bmi.index)]
    return {(s,): [c(len(x)), m(bmi[x.patient_id])] for s, x in by(g, "sex", SEXES).items()}


def s09():
    q = pts()
    sbp = oracle.latest_value("8480-6")
    g = q[(q.age >= 18) & has("I10") & q.patient_id.isin(sbp.index)]
    return {(h,): [m(sbp[x.patient_id])] for h, x in by(g, "data_holder", HOSPITALS).items()}


def s10():
    q = pts()
    egfr = oracle.latest_value("62238-1")
    low = set(egfr[egfr < 45].index)
    g = q[has("N18") & q.patient_id.isin(low)]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s11():
    q = pts()
    base = q[has("F32")]
    ssri = oracle.with_active_medication("N06AB")
    return {(s,): [c(len(x)), p(x.patient_id.isin(ssri).sum(), len(x))] for s, x in by(base, "sex", SEXES).items()}


def s12():
    g = pts()[has("E11") & has("I10")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s13():
    g = pts()[has("I50", "I48")]
    return {(h,): [c(len(x))] for h, x in by(g, "data_holder", HOSPITALS).items()}


def s14():
    e = stays()
    e = e[(e.type == "inpatient") & e.primary_icd10.str.startswith("I50")]
    return {(s,): [c((e.sex == s).sum())] for s in SEXES}


def s15():
    e = stays()
    ids = set(e[(e.type == "inpatient") & e.start_date.str.startswith("2025")].patient_id)
    g = pts()[pts().patient_id.isin(ids)]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s16():
    e = stays()
    e = e[(e.type == "inpatient") & e.start_date.str.startswith("2025")].copy()
    e["los"] = (pd.to_datetime(e.end_date) - pd.to_datetime(e.start_date)).dt.days
    return {(h,): [c(len(x)), m(list(x.los))] for h, x in by(e, "data_holder", HOSPITALS).items()}


def s17():
    g = pts()[has("I63")]
    return {(s,): [c(len(x)), m(list(x.age))] for s, x in by(g, "sex", SEXES).items()}


def s18():
    g = pts()[has("J44")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s19():
    g = pts()[has("I50")]
    return {(h,): [c(len(x))] for h, x in by(g, "data_holder", HOSPITALS).items()}


def s20():
    q = pts()
    g = q[(q.age < 18) & has("J45")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def s21():
    q = pts()
    g = q[(q.region == "Wien") & has("E10", "E11", "E12", "E13", "E14")]
    return {(s,): [c(len(x))] for s, x in by(g, "sex", SEXES).items()}


def s22():
    q = pts()
    g = q[(q.data_holder == "Klinikum West") & has("I48")]
    spec = [("0-64", 0, 64), ("65+", 65, 200)]
    return {(b,): [c(len(x))] for b, x in bands(g, spec).items()}


def s24():
    q = pts()
    base = q[has("I48")]
    doac = oracle.with_active_medication("B01AE", "B01AF")
    return {(r,): [c(len(x)), c(x.patient_id.isin(doac).sum()), p(x.patient_id.isin(doac).sum(), len(x))]
            for r, x in by(base, "region", REGIONS).items()}


def s25():
    g = pts()[on("A10BA02")]
    return {(h,): [c(len(x))] for h, x in by(g, "data_holder", HOSPITALS).items()}


def s26():
    q = pts()
    adults = q[q.age >= 18]
    ob = adults.patient_id.isin(oracle.with_condition("E66"))
    return {(r,): [c(len(x)), c(ob[x.index].sum()), p(ob[x.index].sum(), len(x))]
            for r, x in by(adults, "region", REGIONS).items()}


def s27():
    e = stays()
    ids = set(e[(e.type == "emergency") & e.start_date.str.startswith("2024")].patient_id)
    g = pts()[pts().patient_id.isin(ids)]
    spec = [("0-17", 0, 17), ("18-64", 18, 64), ("65+", 65, 200)]
    return {(b,): [c(len(x))] for b, x in bands(g, spec).items()}


def s28():
    e = stays()
    e = e[(e.type == "inpatient") & e.primary_icd10.str.startswith("K35")]
    return {(r,): [c((e.region == r).sum())] for r in REGIONS}


def s29():
    g = pts()[has("N18")]
    return {(r, s): [c(((g.region == r) & (g.sex == s)).sum())] for r in REGIONS for s in SEXES}


def s30():
    q = pts()
    hb = oracle.latest_value("4548-4")
    g = q[(q.age >= 18) & has("E11") & q.patient_id.isin(hb.index)]
    return {(h,): [c(len(x)), m(hb[x.patient_id])] for h, x in by(g, "data_holder", HOSPITALS).items()}


CASES = [
    ("S01", req("Give the number of patients with asthma, split by gender.",
                ["Asthma = ICD-10 J45.", "Report by gender: number of patients with asthma."]), s01),
    ("S02", req("How many people have heart failure per Bundesland?",
                ["Report per Bundesland: patients with heart failure."]), s02),
    ("S03", req("How many hospital admissions were there per hospital and year?",
                ["Hospital admission = encounter of type 'inpatient'.", "Count admissions, not patients.",
                 "Report per hospital and year: hospital admissions."]), s03),
    ("S04", req("How many emergency visits took place in 2025 at each hospital?",
                ["Count visits (not patients) whose start_date is in 2025.",
                 "Report per hospital: emergency visits."]), s04),
    ("S05", req("How many women aged 50-69 have breast cancer, by region?",
                ["Breast cancer = ICD-10 code starting with C50.",
                 "Report per region: women aged 50-69 with breast cancer."]), s05),
    ("S06", req("How many men aged 65 or older have COPD in each region?",
                ["Report per region: men aged 65 or older with COPD."]), s06),
    ("S07", req("What proportion of adults with hypertension are on a statin, by age group?",
                ["Age groups on 31 December 2025: 18-39, 40-59, 60-79, 80 and over.",
                 "Statin = ATC code starting with C10AA.",
                 "Report per age group: adults with hypertension and the share of them with an active statin."]), s07),
    ("S08", req("What is the average BMI of obese adults, by sex?",
                ["Obesity = any condition with an ICD-10 code starting with E66.",
                 "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out.",
                 "Report per sex: adults with obesity who have a BMI and their mean BMI."]), s08),
    ("S09", req("What is the mean systolic blood pressure of adults with hypertension per hospital?",
                ["Use the most recent systolic blood pressure per patient.",
                 "Report per hospital: mean systolic blood pressure of adults with hypertension."]), s09),
    ("S10", req("How many patients with chronic kidney disease have an eGFR below 45, by region?",
                ["Use the latest eGFR per patient.",
                 "Report per region: patients with chronic kidney disease whose latest eGFR is below 45."]), s10),
    ("S11", req("What share of patients with depression currently take an SSRI, by sex?",
                ["Report per sex: patients with depression and the percentage with an active SSRI."]), s11),
    ("S12", req("How many patients have both type 2 diabetes and hypertension, per region?",
                ["Report per region: patients with type 2 diabetes and hypertension."]), s12),
    ("S13", req("How many patients have heart failure or atrial fibrillation, per hospital?",
                ["Report per hospital: patients with heart failure or atrial fibrillation."]), s13),
    ("S14", req("How many inpatient stays had heart failure as main diagnosis, by sex?",
                ["Inpatient stay = encounter with type 'inpatient'.", "Count stays, not patients.",
                 "Report per sex: inpatient stays with primary_icd10 starting with I50."]), s14),
    ("S15", req("How many patients had at least one inpatient stay in 2025, per region?",
                ["Report per region: patients with at least one inpatient stay in 2025."]), s15),
    ("S16", req("What was the average length of stay of inpatient stays in 2025, per hospital?",
                ["Count stays (not patients) whose start_date is in 2025.",
                 "Length of stay = end_date minus start_date in days.",
                 "Report per hospital: inpatient stays and the mean length of stay."]), s16),
    ("S17", req("What is the mean age of patients with a stroke, by sex?",
                ["Stroke = ICD-10 I63.", "Report per sex: patients with stroke and their mean age."]), s17),
    ("S18", req("How many patients have COPD in each region?",
                ["COPD = ICD-10 code starting with J44.",
                 "Show per region the number of patients with COPD."]), s18),
    ("S19", req("How many patients with heart failure does each hospital treat?",
                ["Heart failure: ICD-10 I50.", "Report per data holder: patients with heart failure."]), s19),
    ("S20", req("How many children have asthma, by region?",
                ["Children = aged under 18 on 31 December 2025.",
                 "Report per region: children with asthma."]), s20),
    ("S21", req("How many patients living in Vienna have diabetes, by sex?",
                ["Diabetes = any condition with an ICD-10 code starting with E10, E11, E12, E13 or E14.",
                 "Report per sex: patients in Vienna with diabetes."]), s21),
    ("S22", req("How many patients with atrial fibrillation are treated at Klinikum West, by age band?",
                ["Age bands: 0-64, 65+.", "Report per age band: patients with atrial fibrillation at Klinikum West."]), s22),
    ("S24", req("What share of patients with atrial fibrillation receive a DOAC, per region?",
                ["DOAC = ATC code starting with B01AE or B01AF.",
                 "Report per region: patients with atrial fibrillation, how many of them have an active DOAC and that share in %."]), s24),
    ("S25", req("How many patients are currently on metformin, per hospital?",
                ["Report per hospital: patients currently taking metformin."]), s25),
    ("S26", req("What is the prevalence of obesity among adults in each region?",
                ["Adult = aged 18 or older on 31 December 2025.",
                 "Obesity = ICD-10 code starting with E66.",
                 "Prevalence = adults with obesity / adults × 100.",
                 "Report per region: adults, adults with obesity and the prevalence."]), s26),
    ("S27", req("How many patients had at least one emergency visit in 2024, by age group?",
                ["Age groups: 0-17, 18-64, 65+.",
                 "Report per age group: patients with an emergency visit in 2024."]), s27),
    ("S28", req("How many hospital admissions for appendicitis were there, by region?",
                ["Count admissions (not patients) with a main diagnosis of appendicitis.",
                 "Report per region: inpatient stays with a main diagnosis of appendicitis."]), s28),
    ("S29", req("How many patients have chronic kidney disease, by region and sex?",
                ["CKD = ICD-10 code starting with N18.", "Report per region and sex: patients with CKD."]), s29),
    ("S30", req("What is the average HbA1c of adults with type 2 diabetes in each hospital?",
                ["Use the latest HbA1c per patient.",
                 "Report per hospital: adults with type 2 diabetes who have an HbA1c and their average HbA1c."]), s30),
]


@pytest.fixture(scope="module")
def body(store, tmp_path_factory):
    return AccessBody(store=store, workdir=tmp_path_factory.mktemp("stress"))


def lookup(answer, path):
    node = answer
    for key in path:
        node = node[key]
    return list(node.values())


@pytest.mark.parametrize("cid, request_, expected", CASES, ids=[x[0] for x in CASES])
def test_stress(body, cid, request_, expected):
    result = body.process({"id": cid, **request_})
    assert result["decision"] == "APPROVE_STATISTICAL", result["reason"]
    assert result["status"] == "answered", (result["status"], result["staff"]["parse"]["unresolved"])
    for path, values in expected().items():
        assert lookup(result["answer"], path) == values, path


# --- round 2: question-only requests and other phrasings --------------------------------

def r01():
    g = pts()[has("J45")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def r02():
    q = pts()
    hb = oracle.latest_value("4548-4")
    g = q[has("E11") & q.patient_id.isin(hb.index)]
    return {(h,): [c(len(x)), m(hb[x.patient_id])] for h, x in by(g, "data_holder", HOSPITALS).items()}


def r03():
    q = pts()
    base = q[has("I10")]
    ace = oracle.with_active_medication("C09AA")
    return {(s,): [c(len(x)), c(x.patient_id.isin(ace).sum()), p(x.patient_id.isin(ace).sum(), len(x))]
            for s, x in by(base, "sex", SEXES).items()}


def r04():
    q = pts()
    g = q[(q.age >= 66) & has("I50")]
    return {(h,): [c(len(x))] for h, x in by(g, "data_holder", HOSPITALS).items()}


def r05():
    e = stays()
    e = e[(e.type == "inpatient") & (e.data_holder == "Klinikum Süd") & e.start_date.str.startswith("2024")]
    return {(s,): [c((e.sex == s).sum())] for s in SEXES}


def r06():
    g = pts()[has("E66")]
    return {(r, h, s): [c(((g.region == r) & (g.data_holder == h) & (g.sex == s)).sum())]
            for r in REGIONS for h in HOSPITALS for s in SEXES}


def r07():
    q = pts()
    bmi = oracle.latest_value("39156-5")
    g = q[(q.age >= 18) & q.patient_id.isin(set(bmi[bmi >= 30].index))]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


def r08():
    e = stays()
    e = e[e.type == "outpatient"].copy()
    e["year"] = e.start_date.str[:4]
    return {(y,): [c((e.year == y).sum())] for y in ["2023", "2024", "2025"]}


def r09():
    g = pts()[has("J44") & ~on("R03")]
    return {(r,): [c(len(x))] for r, x in by(g, "region", REGIONS).items()}


ROUND2 = [
    ("R2-01", req("How many patients have asthma, per region?", []), r01),
    ("R2-02", req("What is the average HbA1c of patients with type 2 diabetes, by hospital?",
                  ["Type 2 diabetes means any diagnosis with ICD-10 code E11.",
                   "HbA1c is defined as LOINC 4548-4, latest value per patient."]), r02),
    ("R2-03", req("What share of patients with hypertension take an ACE inhibitor, by sex?", []), r03),
    ("R2-04", req("How many patients older than 65 with heart failure are there per hospital?",
                  ["Report per hospital: patients older than 65 with heart failure."]), r04),
    ("R2-05", req("How many inpatient stays at Klinikum Süd started in 2024, by sex?",
                  ["Count stays (not patients) whose start_date is in 2024.",
                   "Report per sex: inpatient stays at Klinikum Süd."]), r05),
    ("R2-06", req("How many patients have obesity, by region, hospital and sex?",
                  ["Report per region, hospital and sex: patients with obesity."]), r06),
    ("R2-07", req("How many adults have a BMI of 30 or more, per region?",
                  ["Report per region: adults whose latest BMI is at least 30."]), r07),
    ("R2-08", req("How many outpatient visits were there per year?",
                  ["Count visits, not patients.", "Report per year: outpatient visits."]), r08),
    ("R2-09", req("How many patients with COPD have no active inhaler prescription, by region?",
                  ["Inhaler = ATC code starting with R03.",
                   "Report per region: patients with COPD without an active inhaler."]), r09),
    ("R2-10", req("How many patients have asthma, per region?", [],
                  purpose="A pharmaceutical company plans a clinical trial of a new asthma drug and needs to "
                          "estimate how many eligible patients each region has."), r01),
]


@pytest.mark.parametrize("cid, request_, expected", ROUND2, ids=[x[0] for x in ROUND2])
def test_stress_round2(body, cid, request_, expected):
    test_stress(body, cid, request_, expected)
