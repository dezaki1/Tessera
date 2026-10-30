"""Compute expected answers for the holdout3 requests with plain pandas.

Written blind: uses only the CSV files in data/ and the rules in the official
task README and data/README.md. Does not import any app code.

Run:  .venv/bin/python examples/holdout3/compute_expected.py
Writes examples/holdout3/expected.json
"""
import json
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent.parent / "data"
REF = "2025-12-31"


def read(name):
    return pd.read_csv(DATA / name, dtype=str, keep_default_na=False)


patients = read("patients.csv")
patients = patients[patients.opted_out != "true"].copy()
patients["age"] = 2025 - patients.birth_date.str[:4].astype(int)
keep = set(patients.patient_id)
conditions = read("conditions.csv")
conditions = conditions[conditions.patient_id.isin(keep)]
medications = read("medications.csv")
medications = medications[medications.patient_id.isin(keep)]
observations = read("observations.csv")
observations = observations[observations.patient_id.isin(keep)]
encounters = read("encounters.csv")
encounters = encounters[encounters.patient_id.isin(keep)]

active = medications[(medications.start_date <= REF) &
                     ((medications.end_date == "") | (medications.end_date >= REF))]


def dx(prefix):
    return set(conditions[conditions.icd10.str.startswith(prefix)].patient_id)


def on(prefix):
    return set(active[active.atc.str.startswith(prefix)].patient_id)


def latest(loinc):
    obs = observations[observations.loinc == loinc]
    obs = obs.sort_values(["patient_id", "date"]).groupby("patient_id").tail(1)
    # exact decimal values, never floats
    return {pid: Fraction(Decimal(v)) for pid, v in zip(obs.patient_id, obs.value)}


def _q(x):
    # exact rational -> one decimal, halves up (Decimal ROUND_HALF_UP)
    d = Decimal(x.numerator) / Decimal(x.denominator)
    q = d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    # verify exactly with fractions that q is the half-up rounding of x
    assert Fraction(q) - Fraction(1, 20) <= x < Fraction(q) + Fraction(1, 20), (x, q)
    return q


def cnt(n):
    return "<10" if n < 10 else n


def pct(num, den):
    if num < 10 or den < 10:
        return "suppressed"
    return float(_q(Fraction(num * 100, den)))


def mean(values):
    if len(values) < 10:
        return "suppressed"
    return float(_q(sum(values, Fraction(0)) / len(values)))


def band(age, bands):
    for label, lo, hi in bands:
        if lo <= age <= hi:
            return label
    return None


def ages(ids):
    s = patients[patients.patient_id.isin(ids)]
    return dict(zip(s.patient_id, s.age))


PAT = patients.set_index("patient_id")
REGIONS = sorted(patients.region.unique())  # region names as in the data
HOSPITALS = ["Klinikum Nord", "Klinikum Süd", "Klinikum West"]

out = {}

# ---------------- REJECTS ----------------
for rid in ["H3-01", "H3-02", "H3-03"]:
    out[rid] = {"decision": "REJECT", "articles": ["54"], "answer": None}

# ---------------- H3-04 pharma drug safety (SGLT2, CKD, eGFR) ----------------
B4 = [("18-49", 18, 49), ("50-64", 50, 64), ("65-74", 65, 74), ("75+", 75, 999)]
sglt2 = {pid for pid in on("A10BK") if PAT.at[pid, "age"] >= 18}
ckd = dx("N18")
egfr = latest("62238-1")
ans = {}
for label, lo, hi in B4:
    grp = {pid for pid in sglt2 if lo <= PAT.at[pid, "age"] <= hi}
    with_ckd = grp & ckd
    vals = [egfr[pid] for pid in grp if pid in egfr]
    ans[label] = {
        "patients_on_sglt2": cnt(len(grp)),
        "with_ckd": cnt(len(with_ckd)),
        "ckd_pct": pct(len(with_ckd), len(grp)),
        "patients_with_egfr": cnt(len(vals)),
        "mean_egfr": mean(vals),
    }
out["H3-04"] = {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": ans}

# ---------------- H3-05 startup AF model: prevalence by sex x age band ----------------
B5 = [("18-49", 18, 49), ("50-64", 50, 64), ("65-79", 65, 79), ("80+", 80, 999)]
af = dx("I48")
adults = patients[patients.age >= 18]
ans = {}
for sex in ["female", "male"]:
    ans[sex] = {}
    for label, lo, hi in B5:
        grp = set(adults[(adults.sex == sex) & adults.age.between(lo, hi)].patient_id)
        n_af = len(grp & af)
        ans[sex][label] = {"adults": cnt(len(grp)), "with_af": cnt(n_af),
                           "prevalence_pct": pct(n_af, len(grp))}
out["H3-05"] = {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": ans}

# ---------------- H3-06 SPE: heart failure readmission cohort ----------------
hf = dx("I50")
ip_2425 = set(encounters[(encounters.type == "inpatient") &
                         (encounters.start_date >= "2024-01-01") &
                         (encounters.start_date <= "2025-12-31")].patient_id)
cohort = {pid for pid in hf & ip_2425 if PAT.at[pid, "age"] >= 18}
out["H3-06"] = {"decision": "APPROVE_SPE", "articles": ["53", "66", "68", "73"],
                "answer": {"cohort_size": len(cohort)}}

# ---------------- H3-07 access application w/o justification: asthma inhalers ----------------
B7 = [("0-17", 0, 17), ("18-64", 18, 64), ("65+", 65, 999)]
asthma = dx("J45")
ics_laba = on("R03AK")
lama = on("R03BB")
ans = {}
for label, lo, hi in B7:
    grp = {pid for pid in asthma if lo <= PAT.at[pid, "age"] <= hi}
    a = len(grp & ics_laba)
    b = len(grp & lama)
    ans[label] = {"patients_with_asthma": cnt(len(grp)),
                  "ics_laba_pct": pct(a, len(grp)),
                  "lama_pct": pct(b, len(grp))}
out["H3-07"] = {"decision": "APPROVE_STATISTICAL", "articles": ["53", "66", "69"], "answer": ans}

# ---------------- H3-08 plain English: depression & SSRI by region ----------------
dep = {pid for pid in dx("F32") if PAT.at[pid, "age"] >= 18}
ssri = on("N06AB")
ans = {}
for reg in REGIONS:
    grp = {pid for pid in dep if PAT.at[pid, "region"] == reg}
    n = len(grp & ssri)
    ans[reg] = {"adults_with_depression": cnt(len(grp)), "on_ssri": cnt(n),
                "on_ssri_pct": pct(n, len(grp))}
out["H3-08"] = {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": ans}

# ---------------- H3-09 by hospital: CKD, eGFR, SGLT2 ----------------
sglt2_all = on("A10BK")
ans = {}
for h in HOSPITALS:
    grp = {pid for pid in ckd if PAT.at[pid, "data_holder"] == h}
    vals = [egfr[pid] for pid in grp if pid in egfr]
    below45 = sum(1 for v in vals if v < 45)
    n_sg = len(grp & sglt2_all)
    ans[h] = {"patients_with_ckd": cnt(len(grp)),
              "patients_with_egfr": cnt(len(vals)),
              "mean_egfr": mean(vals),
              "egfr_below_45_pct": pct(below45, len(vals)),
              "sglt2_pct": pct(n_sg, len(grp))}
out["H3-09"] = {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": ans}

# ---------------- H3-10 inpatient stays with main diagnosis pneumonia (J18) ----------------
B10 = [("0-17", 0, 17), ("18-64", 18, 64), ("65+", 65, 999)]
st = encounters[(encounters.type == "inpatient") &
                encounters.primary_icd10.str.startswith("J18") &
                (encounters.start_date >= "2023-01-01") & (encounters.start_date <= "2025-12-31")]
st = st.merge(patients[["patient_id", "age"]], on="patient_id")
ans = {}
for label, lo, hi in B10:
    g = st[st.age.between(lo, hi)]
    ans[label] = {"stays": cnt(len(g)), "patients": cnt(g.patient_id.nunique())}
out["H3-10"] = {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": ans}

REASONS = {
    "H3-01": "Tuning gambling mechanics to keep vulnerable people playing longer develops an addictive product and is detrimental to people (Art. 54).",
    "H3-02": "Using health data to decline or reprice loans is prohibited (Art. 54).",
    "H3-03": "Using health data to screen job candidates is prohibited (Art. 54).",
    "H3-04": "Drug safety (pharmacovigilance) is an allowed purpose (Art. 53). Statistics answer the question (Art. 69). Small cells are suppressed.",
    "H3-05": "Training and testing a diagnostic algorithm is an allowed purpose (Art. 53). Statistics are requested and answer the question (Art. 69).",
    "H3-06": "Research and algorithm development are allowed (Art. 53). Per-patient timelines are needed, so pseudonymised data is justified (Art. 66). Access only under a data permit in a secure processing environment (Art. 68, 73).",
    "H3-07": "Education is an allowed purpose (Art. 53). No reason is given why statistics are not enough, so records are not justified (Art. 66). Approved as statistics (Art. 69).",
    "H3-08": "Policy-making and research are allowed purposes (Art. 53). Statistics answer the question (Art. 69).",
    "H3-09": "Quality of care is an allowed purpose (Art. 53). Statistics answer the question (Art. 69).",
    "H3-10": "Health system planning is an allowed purpose (Art. 53). Statistics answer the question (Art. 69).",
}
out = {k: {"decision": v["decision"], "articles": v["articles"], "reason": REASONS[k], "answer": v["answer"]}
       for k, v in sorted(out.items())}
if __name__ == "__main__":
    (HERE / "expected.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))
