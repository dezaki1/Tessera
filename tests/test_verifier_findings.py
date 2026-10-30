"""Regression tests for the second independent verifier's findings (examples/verifier/)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from ehds.decision import decide
from ehds.pipeline import AccessBody, InvalidRequestId

ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "examples" / "verifier"


def load(path):
    return json.loads(Path(path).read_text())


@pytest.fixture()
def body(store, tmp_path):
    return AccessBody(store=store, workdir=tmp_path)


# 1. prohibited purposes in new wording release no numbers
@pytest.mark.parametrize("rid, point", [("N05", "54(c)"), ("N06", "54(b)")])
def test_new_prohibited_wordings_release_nothing(body, rid, point):
    r = body.process(load(VERIFIER / f"{rid}.json"))
    assert r["decision"] == "REJECT" and f"Art. {point}" in r["reason"]
    assert r["answer"] is None and r["staff"]["sql"] == []


def test_commercial_applicant_without_purpose_is_held(body):
    request = {"id": "H1", "requested_as": "health data request", "purpose": "We would like to understand the market.",
               "question": "How many adults have obesity per region?",
               "applicant": {"organisation": "Fitnessworld Holding GmbH"},
               "definitions": ["Report per region: adults with obesity."]}
    r = body.process(request)
    assert r["status"] == "needs_review" and r["answer"] is None and r["staff"]["sql"] == []
    assert "nothing has been released yet" in r["reason"]
    assert any(f.startswith("Held for review") for f in r["staff"]["flags"])


def test_public_body_or_research_is_not_held():
    for org, purpose in [("Federal Ministry of Health", "Planning of hospital capacity."),
                         ("Example GmbH", "We are commissioned by the health ministry to plan hospital capacity.")]:
        d = decide({"purpose": purpose, "question": "How many?", "requested_as": "health data request",
                    "applicant": {"organisation": org}})
        assert not d.hold_for_review, (org, d.staff_flags)


@pytest.mark.parametrize("text", ["to improve the quality of care", "treatment optimisation for COPD",
                                  "guideline adherence in heart failure"])
def test_art_53_1_f_wording(text):
    d = decide({"purpose": f"We work on {text}.", "question": "How many?", "requested_as": "health data request"})
    assert "improving care" in d.purposes and "(point f)" in d.reason


# 2. ids: a new submission never replaces an earlier result or SPE
def test_duplicate_id_gets_a_new_id_and_keeps_the_original(body):
    first = body.process(load(ROOT / "requests" / "R05.json"))
    assert first["status"] == "spe_ready"
    other = load(ROOT / "requests" / "R02.json") | {"id": "R05"}
    second = body.process(other)
    assert second["request_id"] == "R05-2" and second["decision"] == "REJECT"
    assert body.results["R05"]["status"] == "spe_ready" and body.spe.manifest("R05") is not None
    assert any("already exists" in n for n in second["notes"])


def test_duplicate_id_over_http(body):
    with TestClient(create_app(access_body=body)) as client:
        assert client.post("/api/requests", json=load(ROOT / "requests" / "R05.json")).json()["request_id"] == "R05"
        again = client.post("/api/requests", json=load(ROOT / "requests" / "R05.json"))
        assert again.status_code == 200 and again.json()["request_id"] == "R05-2"
        assert client.get("/spe/R05").status_code == 200


# 3. invalid ids give a clear 4xx, never HTTP 500
@pytest.mark.parametrize("bad", ["../x y", "a" * 65, "R05;drop", ""])
def test_invalid_id_is_a_clear_client_error(body, bad):
    with TestClient(create_app(access_body=body), raise_server_exceptions=False) as client:
        resp = client.post("/api/requests", json=load(ROOT / "requests" / "R05.json") | {"id": bad})
        assert resp.status_code == 422 and "id" in resp.text
        page = client.post("/submit", data={"request_json": json.dumps(load(ROOT / "requests" / "R05.json") | {"id": bad})})
        assert page.status_code == 200 and ("not valid" in page.text or "id" in page.text)
    with pytest.raises(InvalidRequestId):
        body.process(load(ROOT / "requests" / "R05.json") | {"id": bad})


# 4. every refused SPE query is audited, including errors raised by DuckDB
def test_every_refused_spe_query_is_audited(body):
    body.process(load(ROOT / "requests" / "R05.json"))
    attempts = ["SELECT * FROM missing_table", "SELECT * FROM 'data/patients.csv'", "DELETE FROM cohort",
                "COPY cohort TO 'x.csv'"]
    for sql in attempts:
        with pytest.raises(Exception):
            body.spe.query("R05", sql, "tester")
    logged = [e["sql"] for e in body.audit.entries("R05", "spe_query_refused")]
    assert logged == attempts


# 5. statistics applicants never see internal SPE-cohort notes; the reason matches the status
def test_statistics_applicant_sees_no_spe_cohort_notes(body):
    r = body.process(load(VERIFIER / "N04.json"))
    assert r["decision"] == "APPROVE_STATISTICAL" and r["status"] == "answered"
    shown = json.dumps({k: r[k] for k in ("notes", "reason", "how_we_counted")})
    assert "Cohort" not in shown and "secure processing environment" not in shown
    assert not any("SPE" in f for f in r["staff"]["flags"])


def test_reason_does_not_claim_an_answer_that_is_held(body):
    r = body.process({"id": "H2", "requested_as": "health data request", "purpose": "Research on asthma.",
                      "question": "How many?", "definitions": ["Report per postcode: patients with asthma."]})
    assert r["status"] == "needs_review" and r["answer"] is None
    assert "The question is answered" not in r["reason"] and "nothing has been released yet" in r["reason"]
