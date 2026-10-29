import csv
from pathlib import Path

import pytest

from ehds.store import load_store

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def store():
    return load_store(ROOT / "data")


@pytest.fixture(scope="session")
def opted_out_ids():
    with open(ROOT / "data" / "patients.csv", newline="", encoding="utf-8") as f:
        return {row["patient_id"] for row in csv.DictReader(f) if row["opted_out"] == "true"}
