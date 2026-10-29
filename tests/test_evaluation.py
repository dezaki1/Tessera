"""The leak checks in the evaluation harness must actually catch leaks."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from ehds.engine import Engine
from evaluation.run_eval import numbers_match, privacy_problems

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def r08(store, tmp_path_factory):
    from ehds.pipeline import AccessBody
    body = AccessBody(store=store, workdir=tmp_path_factory.mktemp("w"))
    with TestClient(create_app(access_body=body)) as client:
        request = json.loads((ROOT / "requests" / "R08.json").read_text())
        applicant = client.post("/api/requests", json=request).json()
        staff = client.get("/api/staff/requests/R08").json()
        assert client.get("/api/spe/R08/download").status_code == 403
    return {"applicant": applicant, "staff": staff}


def test_clean_answer_has_no_problems(r08, store, opted_out_ids, tmp_path):
    assert privacy_problems("R08", r08, Engine(store), opted_out_ids, tmp_path) == []


def test_tampered_answers_are_caught(r08, store, opted_out_ids, tmp_path):
    engine = Engine(store)
    bad = copy.deepcopy(r08)
    burgenland = next(r for r in bad["applicant"]["answer_table"]["rows"] if r["group"] == ["Burgenland"])
    burgenland["values"] = [1, 24.5]  # real small count and unsuppressed mean
    problems = privacy_problems("R08", bad, engine, opted_out_ids, tmp_path)
    assert any("count 1" in p for p in problems)
    assert any("mean not suppressed" in p for p in problems)

    bad = copy.deepcopy(r08)
    bad["applicant"]["notes"].append("see patient p-d20a5715")
    assert "patient ID in a statistical answer" in privacy_problems("R08", bad, engine, opted_out_ids, tmp_path)


def test_number_matching():
    assert numbers_match({"A": {"x": 12, "y": 5.0}}, {"A": {"n": 12, "pct": 5.0}}) == []
    assert numbers_match({"A": {"x": 12}}, {"A": {"n": 13}}) == ["A: 12"]
    assert numbers_match({"cohort_size": 1152}, {"cohort_size": 1152}) == []
