import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from ehds.pipeline import AccessBody

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def client(store, tmp_path_factory):
    body = AccessBody(store=store, workdir=tmp_path_factory.mktemp("app"))
    with TestClient(create_app(access_body=body)) as c:
        yield c


def test_pages_render(client):
    assert "Submit a request" in client.get("/").text
    r = client.post("/staff/run-samples", follow_redirects=True)
    assert r.status_code == 200 and "R10" in r.text
    for rid in ("R01", "R02", "R05", "R06", "R08"):
        assert client.get(f"/requests/{rid}").status_code == 200
        assert client.get(f"/staff/requests/{rid}").status_code == 200
    assert "suppressed" in client.get("/requests/R08").text


def test_spe_page_blocks_download(client):
    client.post("/submit", data={"sample": "R05"})
    assert "Workspace for R05" in client.get("/spe/R05").text
    assert "p-" in client.get("/spe/R05?table=cohort").text  # visible inside the SPE only
    r = client.post("/spe/R05/download")
    assert r.status_code == 403 and "Download blocked" in r.text and "download_blocked" in r.text
    r = client.post("/spe/R05/query", data={"sql": "COPY cohort TO '/tmp/x.csv'"})
    assert "not available inside the SPE" in r.text


def test_applicant_view_hides_staff_detail(client):
    client.post("/submit", data={"sample": "R01"})
    data = client.get("/api/requests/R01").json()
    assert "staff" not in data and "Wien" in data["answer"]
    assert "SELECT" not in json.dumps(data)


def test_own_request_with_new_grouping(client):
    form = {"request_json": json.dumps({
        "id": "N01", "requested_as": "health data request", "purpose": "Research on hip fractures to plan rehabilitation.",
        "question": "How many inpatient stays with a femur fracture started in 2025, by hospital?",
        "definitions": ["Inpatient stay = encounter with type 'inpatient'.",
                        "Count stays (not patients) whose start_date is in 2025.",
                        "Report per hospital: inpatient stays with a main diagnosis (primary_icd10) starting with S72."]})}
    r = client.post("/submit", data=form, follow_redirects=True)
    assert r.status_code == 200 and "Klinikum Nord" in r.text
    assert client.post("/submit", data={"request_json": "not json"}).status_code == 200


def test_applicant_form_gives_the_same_result_as_the_json_request(tmp_path, store):
    """The form only builds the JSON request; decision and numbers must be identical."""
    import json as _json
    from pathlib import Path as _Path
    from ehds.pipeline import AccessBody
    from app.main import create_app
    from fastapi.testclient import TestClient as _Client
    r08 = _json.loads((_Path(__file__).resolve().parent.parent / "requests" / "R08.json").read_text())
    body = AccessBody(store=store, workdir=tmp_path)
    with _Client(create_app(access_body=body)) as client:
        resp = client.post("/apply", data={
            "name": r08["applicant"]["name"], "organisation": r08["applicant"]["organisation"], "kind": "statistics",
            "purpose": r08["purpose"], "question": r08["question"], "definitions": "\n".join(r08["definitions"])},
            follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/requests/A001"
        via_json = client.post("/api/requests", json=r08).json()
    via_form = body.results["A001"]
    assert via_form["decision"] == via_json["decision"] and via_form["articles"] == via_json["articles"]
    assert via_form["answer"] == via_json["answer"]


def test_applicant_form_records_choice_and_missing_fields(tmp_path, store):
    from ehds.pipeline import AccessBody
    from app.main import create_app
    from fastapi.testclient import TestClient as _Client
    body = AccessBody(store=store, workdir=tmp_path)
    with _Client(create_app(access_body=body)) as client:
        resp = client.post("/apply", data={"organisation": "Uni", "kind": "records", "purpose": "",
                                           "question": "q", "definitions": ""})
        assert resp.status_code == 200 and "Please fill in: purpose" in resp.text and body.results == {}
        resp = client.post("/apply", data={"organisation": "Medical University", "kind": "records",
                                           "purpose": "Research on COPD.", "question": "How many?",
                                           "definitions": "Report per region: patients."}, follow_redirects=False)
    assert body.requests["A001"]["requested_as"] == "health data access application"
