import json
from pathlib import Path

import pytest

from ehds.decision import decide

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = json.loads((ROOT / "requests" / "expected.json").read_text())


def r(purpose, requested_as="health data request", question="How many?"):
    return {"purpose": purpose, "question": question, "requested_as": requested_as}


@pytest.mark.parametrize("rid", sorted(EXPECTED))
def test_sample_decisions(rid):
    d = decide(json.loads((ROOT / "requests" / f"{rid}.json").read_text()))
    assert d.decision == EXPECTED[rid]["decision"]
    assert d.articles == EXPECTED[rid]["articles"]


@pytest.mark.parametrize("purpose", [
    "We study how nicotine replacement therapy supports smoking cessation in COPD patients.",
    "Research on the harms of alcohol use for liver disease, to inform prevention.",
    "We need real-world evidence for the marketing authorisation of a new medicine.",
    "Occupational health study of shift workers to improve workplace safety.",
    "Health promotion programme for regions with high diabetes prevalence.",
    "The national social insurance wants to estimate the yearly cost of diabetes care for planning.",
])
def test_allowed_purposes_are_not_rejected(purpose):
    assert decide(r(purpose)).decision == "APPROVE_STATISTICAL"


@pytest.mark.parametrize("purpose", [
    "We want to set life insurance premiums based on diagnoses.",
    "Our bank wants to use health data for credit scoring of loan applicants.",
    "We plan an e-cigarette flavour line for young adults and want to know where smokers live.",
    "Launch campaign: we want to target physicians who prescribe our competitor's products.",
    "Our brewery is developing a new alcohol-free beer brand for consumers with diabetes.",
    "We will check our job applicants for depression before hiring decisions.",
])
def test_prohibited_purposes_are_rejected(purpose):
    d = decide(r(purpose))
    assert d.decision == "REJECT" and d.articles == ["54"]


def test_spe_needs_an_explanation():
    base = "We train a prediction model for heart failure readmission."
    assert decide(r(base, "health data access application")).decision == "APPROVE_STATISTICAL"
    assert decide(r(base + " Summary statistics are not sufficient because the model needs patient-level data.",
                    "health data access application")).decision == "APPROVE_SPE"


def test_trace_explains_each_step():
    d = decide(json.loads((ROOT / "requests" / "R09.json").read_text()))
    assert [f.outcome for f in d.trace] == ["no match", "no match", "no match", "matched"]
