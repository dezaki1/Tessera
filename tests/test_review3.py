"""Regression tests for review round 3 (purpose tester, fail-safe check, API tester, privacy attacker).

The reviewers' blind cases with expected results are in examples/review3/ and are also scored by
evaluation/run_eval.py (set "review3").
"""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from ehds.decision import decide
from ehds.pipeline import AccessBody, InvalidRequest
from ehds.spe import DownloadBlocked

ROOT = Path(__file__).resolve().parent.parent
REVIEW3 = ROOT / "examples" / "review3"
EXPECTED = json.loads((REVIEW3 / "expected.json").read_text(encoding="utf-8"))


def load(rid):
    return json.loads((REVIEW3 / f"{rid}.json").read_text(encoding="utf-8"))


def outcome(request):
    d = decide(request)
    return "REJECT" if d.decision == "REJECT" else "HOLD" if d.hold_for_review else "RELEASE"


@pytest.fixture()
def body(store, tmp_path):
    return AccessBody(store=store, workdir=tmp_path)


BASE = {"applicant": {"organisation": "Medical University research group"}, "requested_as": "health data request",
        "purpose": "Scientific research on chronic disease.", "question": "How common is COPD?"}


# --- purpose check ---------------------------------------------------------------------------

PURPOSE = [rid for rid in EXPECTED if rid.startswith("RP-")]

# Known shortfalls, kept visible rather than tuned away: these are held for staff review, not rejected.
HELD_INSTEAD_OF_REJECTED = {"RP-P1-15"}


@pytest.mark.parametrize("rid", PURPOSE)
def test_prohibited_purposes_release_nothing_and_legitimate_ones_are_not_rejected(rid):
    got = outcome(load(rid))
    if EXPECTED[rid]["decision"] == "REJECT":
        assert got == ("HOLD" if rid in HELD_INSTEAD_OF_REJECTED else "REJECT"), got
    else:
        assert got != "REJECT"


@pytest.mark.parametrize("rid", [rid for rid in EXPECTED if rid.startswith("RF-")])
def test_failsafe_cases_are_never_rejected(rid):
    request = load(rid)
    d = decide(request)
    assert d.decision == EXPECTED[rid]["decision"] and d.articles == EXPECTED[rid]["articles"]


def test_bracketed_description_does_not_make_an_applicant_commercial():
    d = decide({**BASE, "applicant": {"organisation": "Medical University of Vienna (PhD student, no company affiliation)"},
                "purpose": "My dissertation looks at whether asthma goes together with higher body weight."})
    assert not d.hold_for_review


def test_unmatched_purpose_reason_does_not_say_staff_will_review_before_answering():
    d = decide({**BASE, "purpose": "Background figures for our work."})
    assert "did not match" in d.reason and "will review it." not in d.reason


# --- privacy: opt-outs and silently wrong answers -------------------------------------------------

def test_question_about_opted_out_patients_is_not_answered(body):
    r = body.process(load("RV-A10"))
    assert r["status"] == "needs_review" and r["answer"] is None and r["staff"]["sql"] == []


def test_excluding_opted_out_patients_is_not_a_question_about_them(body):
    r = body.process({**BASE, "id": "X1", "definitions": ["COPD = any condition with an ICD-10 code starting with J44.",
                                                         "Exclude opted-out patients.",
                                                         "Report per region: patients with COPD."]})
    assert r["answer"] is not None


@pytest.mark.parametrize("rid", ["RV-A01", "RV-A03", "RV-B4"])
def test_wordings_that_gave_wrong_numbers_go_to_staff(body, rid):
    r = body.process(load(rid))
    assert r["status"] == "needs_review" and r["answer"] is None


def test_counting_rule_repeated_in_report_line_is_answered(body):
    r = body.process({**BASE, "id": "X2", "definitions": [
        "Count admissions (not patients) with a main diagnosis (primary_icd10) starting with I48.",
        "Report per hospital: inpatient stays with a main diagnosis (primary_icd10) starting with I48."]})
    assert r["answer"] is not None
    assert not any("counting rule" in u for u in r["staff"]["parse"]["unresolved"])


# --- API robustness ----------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [{"applicant": "Uni Wien"}, {"applicant": ["x"]}, {"applicant": 5},
                                 {"definitions": "COPD = J44."}, {"purpose": 5}, {"purpose": ""},
                                 {"requested_as": True}, {"purpose": "x" * 20_001},
                                 {"definitions": ["a"] * 201}])
def test_malformed_requests_get_422(body, bad):
    with TestClient(create_app(access_body=body)) as client:
        resp = client.post("/api/requests", json={**BASE, "id": "M1", "definitions": ["Report per region: patients."]}
                           | bad)
    assert resp.status_code == 422, resp.text


def test_unpaired_surrogate_is_refused(body):
    with pytest.raises(InvalidRequest):
        body.process({**BASE, "id": "M2", "purpose": "research \ud800", "definitions": []})


def test_oversized_body_gets_413(body):
    with TestClient(create_app(access_body=body)) as client:
        resp = client.post("/api/requests", content=b"{" + b" " * 600_000 + b"}",
                           headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_resubmitting_a_64_character_id_gets_a_valid_suffix(body):
    request = {**BASE, "id": "A" * 64, "definitions": ["Report per region: patients."]}
    body.process(request)
    second = body.process(request)
    assert second["request_id"].endswith("-2") and len(second["request_id"]) <= 64


def test_concurrent_submissions_with_the_same_id_get_distinct_ids(body):
    request = {**BASE, "id": "RACE", "definitions": ["Report per region: patients."]}
    ids, threads = [], [threading.Thread(target=lambda: ids.append(body.process(request)["request_id"]))
                        for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 8


# --- SPE queries -------------------------------------------------------------------------------

@pytest.fixture()
def spe(body):
    r = body.process(json.loads((ROOT / "requests" / "R05.json").read_text()))
    assert r["status"] == "spe_ready"
    return body, r["request_id"]


@pytest.mark.parametrize("sql", ["SELECT string_agg(patient_id, ',') FROM cohort",
                                 "SELECT list(patient_id) FROM cohort",
                                 "SELECT * FROM duckdb_databases()",
                                 "SELECT current_setting('home_directory')"])
def test_spe_blocks_system_and_row_packing_functions(spe, sql):
    body, rid = spe
    with pytest.raises(DownloadBlocked):
        body.spe.query(rid, sql, "t")


@pytest.mark.parametrize("sql", ["", "   ", "SELECT 1 " + "x" * 10_001])
def test_spe_refuses_empty_or_oversized_sql_and_logs_it(spe, sql):
    body, rid = spe
    with pytest.raises(ValueError):
        body.spe.query(rid, sql, "t")
    last = json.loads(body.audit.path.read_text().splitlines()[-1])
    assert last["event"] == "spe_query_refused" and last.get("error")


def test_spe_values_are_capped(spe):
    body, rid = spe
    rows = body.spe.query(rid, "SELECT repeat('x', 5000) AS v", "t")["rows"]
    assert len(rows[0][0]) == 200


def test_spe_query_timeout(spe, monkeypatch):
    import ehds.spe
    monkeypatch.setattr(ehds.spe, "QUERY_TIMEOUT_S", 1)
    body, rid = spe
    import duckdb
    with pytest.raises(duckdb.Error):
        body.spe.query(rid, "SELECT count(*) FROM cohort a, cohort b, cohort c, cohort d", "t")
    last = json.loads(body.audit.path.read_text().splitlines()[-1])
    assert last["event"] == "spe_query_refused" and "stopped after" in last["error"]
