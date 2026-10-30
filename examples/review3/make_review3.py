"""Builds examples/review3/ from the round-3 reviewers' blind cases.

Expected decisions are the reviewers' own labels, written before they read the app.
Expected numbers are computed here with plain pandas, without any app code:
opted-out patients removed, age = 2025 - birth year, a diagnosis = a row in conditions.csv.

    python examples/review3/make_review3.py <review3 folder with purpose/, failsafe/, privacy/>
"""

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent.parent / "data"
SRC = Path(sys.argv[1])
STAT, SPE, REJ = (["APPROVE_STATISTICAL", ["53", "69"]], ["APPROVE_SPE", ["53", "66", "68", "73"]],
                  ["REJECT", ["54"]])

patients = pd.read_csv(DATA / "patients.csv")
patients = patients[~patients["opted_out"].astype(str).str.lower().isin(["true", "1", "yes"])]
patients["age"] = 2025 - pd.to_datetime(patients["birth_date"]).dt.year
conditions = pd.read_csv(DATA / "conditions.csv")
REGIONS = sorted(patients["region"].unique())


def with_code(prefix: str) -> set:
    return set(conditions.loc[conditions["icd10"].str.startswith(prefix), "patient_id"])


def shown(n: int):
    return n if n >= 10 else "<10"


requests, expected = [], {}

# 1. Purpose tester: 60 purposes in plain language, same statistical question for all.
for case in json.loads((SRC / "purpose" / "cases.json").read_text()):
    requests.append({
        "id": f"RP-{case['id']}", "applicant": {"organisation": case["organisation"]},
        "requested_as": "health data request", "requested_format": "anonymised statistics",
        "purpose": case["purpose"], "question": "How many adults have type 2 diabetes, per region?",
        "definitions": ["Adult = aged 18 or older on 31 December 2025.",
                        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
                        "Report per region: adults with type 2 diabetes."]})
    d, a = REJ if case["expected"] == "REJECT" else STAT
    expected[f"RP-{case['id']}"] = {"decision": d, "articles": a}

# 2. Fail-safe check: 25 legitimate requests; the two access applications justify pseudonymised data.
for case in json.loads((SRC / "failsafe" / "cases.json").read_text()):
    requests.append(case | {"id": f"RF-{case['id']}"})
    d, a = SPE if "application" in case["requested_as"] else STAT
    expected[f"RF-{case['id']}"] = {"decision": d, "articles": a}

# 3. Privacy attacker: legitimate statistics requests that probe the small-cell rule and opt-outs.
privacy = {}
for name in ("attacks.json", "followups.json"):
    data = json.loads((SRC / "privacy" / name).read_text())
    for item in data if isinstance(data, list) else data.get("attacks", list(data.values())):
        r = item.get("request", item)
        privacy[r["id"]] = r
for pid in ("A01", "A03", "A10", "A11", "B1", "B3a", "B3b", "B4", "B5"):
    requests.append(privacy[pid] | {"id": f"RV-{pid}"})
    expected[f"RV-{pid}"] = {"decision": STAT[0], "articles": STAT[1]}

copd = patients[patients["patient_id"].isin(with_code("J44"))]
expected["RV-B1"]["answer"] = {
    reg: {"patients_with_copd": shown(int((copd["region"] == reg).sum())),
          "women_with_copd": shown(int(((copd["region"] == reg) & (copd["sex"] == "female")).sum())),
          "men_with_copd": shown(int(((copd["region"] == reg) & (copd["sex"] == "male")).sum()))}
    for reg in REGIONS}
breast = patients[patients["patient_id"].isin(with_code("C50"))]
expected["RV-B3a"]["answer"] = {"patients_with_breast_cancer":
                                shown(int(breast["region"].isin(["Burgenland", "Salzburg", "Vorarlberg"]).sum()))}
expected["RV-B3b"]["answer"] = {"patients_with_breast_cancer":
                                shown(int(breast["region"].isin(["Salzburg", "Vorarlberg"]).sum()))}
dep = patients[patients["patient_id"].isin(with_code("F32")) & (patients["age"] >= 18)]
expected["RV-B5"]["answer"] = {reg: {"adults_with_depression": shown(int((dep["region"] == reg).sum()))}
                               for reg in REGIONS}

for old in HERE.glob("R[PFV]-*.json"):
    old.unlink()
for r in requests:
    (HERE / f"{r['id']}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
(HERE / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False), encoding="utf-8")
print(len(requests), "requests written")
