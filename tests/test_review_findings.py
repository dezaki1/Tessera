"""Regression tests for the problems confirmed by the four independent reviews
(privacy attacker, jury simulator, legal reviewer, demo critic)."""

import json
from pathlib import Path

import pytest

from ehds.decision import decide
from ehds.parser import ParsedRequest
from ehds.pipeline import AccessBody
from ehds.spec import AgeCriterion, CohortSpec, Metric, QuerySpec, RecordSelection
from evaluation.run_eval import numbers_match

from . import oracle

ROOT = Path(__file__).resolve().parent.parent
JURY = ROOT / "examples" / "jury"
BASE = {"requested_as": "health data request", "requested_format": "anonymised statistics",
        "purpose": "University research on chronic disease to improve care.", "question": "How many?"}


@pytest.fixture(scope="module")
def body(store, tmp_path_factory):
    return AccessBody(store=store, workdir=tmp_path_factory.mktemp("review"))


# --- privacy -----------------------------------------------------------------------------

def test_overlapping_age_bands_are_refused(body):
    """18-91 and 18-92 differ by exactly the hidden count of 92-year-olds."""
    r = body.process({**BASE, "id": "PV1", "definitions": [
        "Adult = aged 18 or older on 31 December 2025.",
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Age bands on 31 December 2025: 18-91, 18-92, 92-92.",
        "Report per band: adults with type 2 diabetes."]})
    assert r["status"] == "needs_review" and r["answer"] is None
    assert any("overlap" in f for f in r["staff"]["flags"])


def test_stays_of_fewer_than_10_patients_are_hidden(body):
    """11 breast-cancer stays in Wien belong to 5 patients."""
    r = body.process({**BASE, "id": "PV2", "definitions": [
        "Breast cancer = any condition with an ICD-10 code starting with C50.",
        "Inpatient stay = encounter with type 'inpatient'.", "Count stays (not patients).",
        "Report per region: inpatient stays, inpatient stays of patients with breast cancer and that share in %."]})
    p, c, _, _, e = oracle.frames()
    stays = e[(e.type == "inpatient") & e.patient_id.isin(oracle.with_condition("C50"))]
    wien = stays.merge(p[["patient_id", "region"]], on="patient_id").query("region == 'Wien'")
    assert len(wien) >= 10 and wien.patient_id.nunique() < 10  # the case the rule must catch
    total, bc, share = r["answer"]["Wien"].values()
    assert bc == "<10" and share == "suppressed" and isinstance(total, int)


def test_spe_cohort_keeps_the_stay_condition(body):
    """The cohort 'patients 65+ with a hip fracture stay' once lost the stay and held 3574 patients."""
    r = body.process(json.loads((JURY / "J07.json").read_text()))
    p, _, _, _, e = oracle.frames()
    hip = set(e[(e.type == "inpatient") & e.primary_icd10.str.startswith("S72")].patient_id)
    expected = int(((p.age >= 65) & p.patient_id.isin(hip)).sum())
    assert r["status"] == "spe_ready" and r["answer"] == {"cohort_size": expected} == {"cohort_size": 10}
    assert r["spe"]["tables"]["cohort"] == expected


def test_spe_is_not_built_when_cohort_and_requested_count_disagree(body, monkeypatch):
    """Safety net: a wrong cohort must never be copied silently."""
    wrong = ParsedRequest(
        statistics=QuerySpec(metrics=[Metric(name="n", label="cohort", kind="count",
                                             criteria=[AgeCriterion(min=65), AgeCriterion(max=70)])]),
        cohort=CohortSpec(criteria=[AgeCriterion(min=65)], records=[RecordSelection(table="conditions")]))
    monkeypatch.setattr(body.parser, "parse", lambda request: wrong)
    r = body.process(json.loads((ROOT / "requests" / "R05.json").read_text()) | {"id": "PV4"})
    assert r["status"] == "needs_review" and r["spe"] is None
    assert any("cohort check failed" in f for f in r["staff"]["flags"])
    assert body.spe.manifest("PV4") is None


def test_reidentification_gets_no_spe(body):
    r = body.process({"id": "PV5", "requested_as": "health data access application",
                      "requested_format": "pseudonymised records",
                      "purpose": "We train a risk model; aggregates are not enough because it needs patient-level "
                                 "data. We will re-identify patients and link them to our customer database.",
                      "question": "Provide conditions of adults with diabetes.",
                      "definitions": ["Report the size of the cohort."]})
    assert r["status"] == "needs_review" and r["spe"] is None
    assert any("re-identify" in n for n in r["notes"])


# --- silent wrong answers (jury simulator) -------------------------------------------------

@pytest.mark.parametrize("rid", [f"J0{i}" for i in range(1, 9)])
def test_jury_requests(body, rid):
    expected = json.loads((JURY / "expected.json").read_text())[rid]
    r = body.process(json.loads((JURY / f"{rid}.json").read_text()))
    assert r["decision"] == expected["decision"] and r["articles"] == expected["articles"]
    assert numbers_match(expected["answer"], r["answer"]) == []


def test_plural_persons_matches_defined_person_concept(body):
    r = body.process(json.loads((JURY / "J06.json").read_text()) | {"id": "PV6"})
    assert r["answer"]["male"]["persons_aged_65_or_over"] == 1752 or 1752 in r["answer"]["male"].values()


# --- legal ---------------------------------------------------------------------------------

def test_unrecognised_purpose_is_not_called_allowed():
    d = decide({"purpose": "We are curious about our competitors.", "question": "How many?",
                "requested_as": "health data request"})
    assert "is an allowed purpose" not in d.reason and "could not match" in d.reason


@pytest.mark.parametrize("rid, point", [("R02", "54(b)"), ("R04", "54(c)"), ("R06", "54(a) and (b)"), ("R10", "54(d)")])
def test_rejections_cite_the_exact_point_and_quote_the_applicant(rid, point):
    request = json.loads((ROOT / "requests" / f"{rid}.json").read_text())
    d = decide(request)
    assert f"Art. {point}" in d.reason and "Your application says:" in d.reason


def test_public_only_purposes_are_flagged_for_private_applicants():
    d = decide({"purpose": "Public health surveillance to plan our new pharmacy chain.", "question": "How many?",
                "requested_as": "health data request",
                "applicant": {"organisation": "MediMarkt GmbH"}})
    assert any("Art. 53(2)" in f for f in d.staff_flags)
    assert "point a" not in d.reason
    public = decide({"purpose": "Public health surveillance of influenza.", "question": "How many?",
                     "requested_as": "health data request",
                     "applicant": {"organisation": "Federal Ministry of Health"}})
    assert "(point a)" in public.reason and not any("53(2)" in f for f in public.staff_flags)


def test_opt_out_note_only_on_approvals(body):
    rejected = body.process(json.loads((ROOT / "requests" / "R02.json").read_text()))
    approved = body.process(json.loads((ROOT / "requests" / "R01.json").read_text()))
    assert not any("opted out" in n for n in rejected["notes"])
    assert any("Art. 71" in n for n in approved["notes"])


@pytest.mark.parametrize("rid", ["J01", "J02", "J03", "J04", "J05", "J06", "J08"])
def test_jury_requests_need_no_staff_review(body, rid):
    """Ordinary sentences ('Count each person once', 'The year of a stay is ...', 'Aged 65 or over ...',
    'Only admissions with a start_date in 2025') must not raise false alarms."""
    r = body.process(json.loads((JURY / f"{rid}.json").read_text()) | {"id": f"NW{rid}"})
    assert r["status"] == "answered", r["staff"]["parse"]["unresolved"]


def test_distinct_patients_is_a_separate_number(body):
    r = body.process(json.loads((JURY / "J08.json").read_text()) | {"id": "PV8"})
    assert [c["kind"] for c in r["answer_table"]["columns"]] == ["count", "count"]
    units = [m["unit"] for m in r["staff"]["parse"]["statistics"]["metrics"]]
    assert units == ["encounter", "patient"]


def test_malformed_age_band_does_not_crash(body):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(access_body=body)) as client:
        resp = client.post("/api/requests", json={**BASE, "id": "PV9", "definitions": [
            "Age bands on 31 December 2025: under 0, 0-150.", "Report per band: patients."]})
    assert resp.status_code == 200 and resp.json()["status"] == "needs_review"
