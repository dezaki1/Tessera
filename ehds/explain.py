"""Plain-English descriptions of a QuerySpec, shown to applicants and staff."""

from __future__ import annotations

from .spec import CohortSpec, Criterion, Dimension, EncounterFilter, Metric, QuerySpec

_UNITS = {"%": "%"}


def _codes(prefixes: list[str]) -> str:
    return ", ".join(f"{p}*" for p in prefixes)


def _visit(f: EncounterFilter) -> str:
    parts = [("/".join(f.types) + " visit") if f.types else "visit"]
    if f.primary_icd10_prefixes:
        parts.append(f"with main diagnosis (primary_icd10) {_codes(f.primary_icd10_prefixes)}")
    if f.date_from or f.date_to:
        parts.append(f"starting {f.date_from or '…'} to {f.date_to or '…'}")
    if f.data_holders:
        parts.append(f"at {', '.join(f.data_holders)}")
    return " ".join(parts)


def criterion(c: Criterion) -> str:
    if c.kind == "age":
        if c.min is not None and c.max is not None:
            text = f"aged {c.min}–{c.max}"
        elif c.min is not None:
            text = f"aged {c.min} or older"
        else:
            text = f"aged {c.max} or younger"
        text += " on 31 Dec 2025"
    elif c.kind == "sex":
        text = f"sex {'/'.join(c.values)}"
    elif c.kind == "region":
        text = f"living in {', '.join(c.values)}"
    elif c.kind == "data_holder":
        text = f"record held by {', '.join(c.values)}"
    elif c.kind == "condition":
        text = f"a diagnosis in conditions.csv coded {_codes(c.icd10_prefixes)}"
    elif c.kind == "medication":
        text = f"{'an active' if c.active else 'any'} prescription coded ATC {_codes(c.atc_prefixes)}"
    elif c.kind == "observation":
        which = "latest" if c.which == "latest" else "any"
        text = f"a measurement LOINC {c.loinc}"
        if c.comparator:
            text = f"{which} value of LOINC {c.loinc} {c.comparator} {c.threshold}"
    elif c.kind == "has_encounter":
        text = f"at least one {_visit(c.filter)}"
    elif c.kind == "encounter":
        text = _visit(c.filter)
    else:
        text = c.kind
    if c.negate:
        return f"NOT {text}" if c.kind in ("age", "sex", "region", "data_holder", "encounter") else f"no {text.removeprefix('a ').removeprefix('an ').removeprefix('any ')}"
    return text


def criteria(cs: list[Criterion]) -> str:
    return " AND ".join(criterion(c) for c in cs) or "everyone"


def metric(m: Metric, by_name: dict[str, Metric]) -> str:
    what = "stays" if m.unit == "encounter" else "patients"
    if m.kind == "count":
        return f"Number of {what}: {criteria(m.criteria)}."
    if m.kind == "percentage":
        return (f"Share in %: ({by_name[m.numerator].label}) ÷ ({by_name[m.denominator].label}) × 100, "
                "one decimal.")
    v = m.value
    value = {"age": "age on 31 Dec 2025", "length_of_stay": "length of stay in days"}.get(
        v.source, f"latest value of LOINC {v.loinc} per patient")
    return f"Mean of the {value}, over {what}: {criteria(m.criteria)}; one decimal."


def dimension(d: Dimension) -> str:
    if d.kind == "age_band":
        return "age band on 31 Dec 2025 (" + ", ".join(b.label for b in d.bands) + ")"
    return {"region": "region where the patient lives", "data_holder": "hospital (data holder)",
            "sex": "sex", "encounter_type": "visit type", "encounter_year": "year the visit started"}.get(d.kind, d.kind)


def spec(s: QuerySpec) -> dict:
    by_name = {m.name: m for m in s.metrics}
    return {
        "groups": [dimension(d) for d in s.dimensions] or ["no grouping (one total)"],
        "numbers": [{"name": m.name, "label": m.label, "definition": metric(m, by_name)}
                    for m in s.metrics if m.visible],
        "active_medication": ("start_date on or before 31 Dec 2025 and end_date empty or on/after it"
                              if s.medication_active_rule == "start_and_end" else "end_date empty"),
    }


def cohort(c: CohortSpec) -> dict:
    return {
        "patients": criteria(c.criteria),
        "records": [r.table + (f" (only LOINC {', '.join(r.loinc)})" if r.loinc else "")
                    + (f" (columns: {', '.join(r.columns)})" if r.columns else "") for r in c.records],
    }
