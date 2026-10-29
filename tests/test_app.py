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
