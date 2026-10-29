import json
from pathlib import Path

import duckdb
import pytest

from ehds.pipeline import AccessBody
from ehds.spe import DownloadBlocked

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def body(store, tmp_path_factory):
    b = AccessBody(store=store, workdir=tmp_path_factory.mktemp("spe"))
    b.process(json.loads((ROOT / "requests" / "R05.json").read_text()))
    return b


def test_spe_contains_only_cohort_and_named_records(body, opted_out_ids):
    result = body.results["R05"]
    assert result["status"] == "spe_ready"
    assert set(result["spe"]["tables"]) == {"cohort", "conditions", "medications", "observations"}
    db = body.spe.root / "R05" / "workspace.duckdb"
    with duckdb.connect(db.as_posix(), read_only=True) as con:
        cohort = {r[0] for r in con.execute("SELECT patient_id FROM cohort").fetchall()}
        assert len(cohort) == result["answer"]["cohort_size"]
        assert not cohort & opted_out_ids
        for table in ("conditions", "medications", "observations"):
            ids = {r[0] for r in con.execute(f"SELECT DISTINCT patient_id FROM {table}").fetchall()}
            assert ids <= cohort and not ids & opted_out_ids
        loincs = {r[0] for r in con.execute("SELECT DISTINCT loinc FROM observations").fetchall()}
        assert loincs == {"4548-4"}


def test_download_is_blocked_and_logged(body):
    with pytest.raises(DownloadBlocked):
        body.spe.download("R05", actor="dr.hadzic")
    entries = body.audit.entries("R05", "download_blocked")
    assert entries and entries[-1]["actor"] == "dr.hadzic"


def test_queries_inside_spe_cannot_export(body):
    assert body.spe.query("R05", "SELECT count(*) FROM cohort", "x")["rows"] == [["1152"]]
    for sql in ("COPY cohort TO 'x.csv'", "SELECT * FROM read_csv('data/patients.csv')",
                "SELECT 1; DROP TABLE cohort"):
        with pytest.raises((DownloadBlocked, ValueError)):
            body.spe.query("R05", sql, "x")


def test_rejected_requests_query_nothing(body):
    r = body.process(json.loads((ROOT / "requests" / "R06.json").read_text()))
    assert r["answer"] is None and r["staff"]["sql"] == [] and r["spe"] is None
