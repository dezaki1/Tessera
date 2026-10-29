"""Independent pandas re-implementation of the counting rules, used only by tests.

It shares no code with the app: it reads the CSVs itself, so an error in the
store or engine shows up as a mismatch.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
REF = "2025-12-31"


@lru_cache(maxsize=None)
def frames():
    read = lambda name: pd.read_csv(DATA / f"{name}.csv", dtype=str, keep_default_na=False)
    p = read("patients")
    p = p[p.opted_out != "true"].copy()
    p["age"] = 2025 - p.birth_date.str[:4].astype(int)
    keep = set(p.patient_id)
    rest = {n: read(n) for n in ("conditions", "medications", "observations", "encounters")}
    rest = {n: df[df.patient_id.isin(keep)].copy() for n, df in rest.items()}
    return p, rest["conditions"], rest["medications"], rest["observations"], rest["encounters"]


def with_condition(*prefixes: str) -> set[str]:
    _, c, _, _, _ = frames()
    return set(c[c.icd10.str.startswith(prefixes)].patient_id)


def with_active_medication(*prefixes: str) -> set[str]:
    _, _, m, _, _ = frames()
    active = m[(m.start_date <= REF) & ((m.end_date == "") | (m.end_date >= REF))]
    return set(active[active.atc.str.startswith(prefixes)].patient_id)


def latest_value(loinc: str) -> pd.Series:
    _, _, _, o, _ = frames()
    rows = o[o.loinc == loinc].sort_values(["patient_id", "date"]).groupby("patient_id").tail(1)
    return rows.set_index("patient_id").value.astype(float)


def band(age: int, bands: list[tuple[int, int | None]]) -> int | None:
    for i, (lo, hi) in enumerate(bands):
        if age >= lo and (hi is None or age <= hi):
            return i
    return None
