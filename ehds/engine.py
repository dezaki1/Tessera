"""Turns a QuerySpec into SQL, runs it on the store and returns a RawResult.

Every query is generated from the spec; there is no per-request SQL. String
values are checked against the data's own vocabulary (or, for codes, against
the patterns in spec.py) before they are quoted into SQL.

Shape of a statistics query:

    WITH units AS (one row per patient or stay, with a boolean column per
                   metric saying whether the row meets the metric's criteria,
                   and a value column per mean),
         d0 AS (VALUES ...)            -- one table per requested dimension
    SELECT d0.label, count(...) FILTER (WHERE f_0), ...
    FROM d0 CROSS JOIN d1 LEFT JOIN units u ON <unit falls into the group>

Starting from the dimension tables means every requested group appears, with
zeros where nobody matches. Suppression happens later, in disclosure.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .disclosure import MetricDef, RawCell, RawMean, RawResult
from .spec import (
    CohortSpec,
    Criterion,
    Dimension,
    EncounterFilter,
    MedicationActiveRule,
    Metric,
    QuerySpec,
)
from .store import REFERENCE_DATE, Store


class SpecError(ValueError):
    """The spec cannot be run on this data (unknown value, impossible combination)."""


def lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def date_lit(d) -> str:
    return f"DATE '{d.isoformat()}'"


REF = date_lit(REFERENCE_DATE)


@dataclass
class _Compiler:
    store: Store
    active_rule: MedicationActiveRule
    latest: dict[tuple, str] = field(default_factory=dict)  # (loinc, from, to) -> CTE name

    # -- vocabulary checks --------------------------------------------------
    def _known(self, values: list[str], domain: tuple[str, ...], what: str) -> list[str]:
        lookup = {v.casefold(): v for v in domain}
        out = []
        for v in values:
            if v.casefold() not in lookup:
                raise SpecError(f"unknown {what} {v!r}; known: {', '.join(domain)}")
            out.append(lookup[v.casefold()])
        return out

    def _in(self, column: str, values: list[str], domain: tuple[str, ...], what: str) -> str:
        known = self._known(values, domain, what)
        return f"{column} IN ({', '.join(lit(v) for v in known)})"

    @staticmethod
    def _prefixes(column: str, prefixes: list[str]) -> str:
        return "(" + " OR ".join(f"starts_with({column}, {lit(p)})" for p in prefixes) + ")"

    # -- measurements -------------------------------------------------------
    def latest_cte(self, loinc: str, date_from=None, date_to=None) -> str:
        key = (loinc, date_from, date_to)
        if key not in self.latest:
            self.latest[key] = f"latest_{len(self.latest)}"
        return self.latest[key]

    def ctes(self) -> list[str]:
        out = []
        for (loinc, date_from, date_to), name in self.latest.items():
            where = [f"loinc = {lit(loinc)}"]
            if date_from:
                where.append(f"date >= {date_lit(date_from)}")
            if date_to:
                where.append(f"date <= {date_lit(date_to)}")
            # At most one value per patient, test and day, so the latest is unique.
            out.append(
                f"{name} AS (SELECT patient_id, arg_max(value, date) AS value "
                f"FROM observations WHERE {' AND '.join(where)} GROUP BY patient_id)"
            )
        return out

    # -- criteria -----------------------------------------------------------
    def encounter_filter(self, alias: str, f: EncounterFilter) -> str:
        v = self.store.vocabulary
        parts = []
        if f.types:
            parts.append(self._in(f"{alias}.type", f.types, v.encounter_types, "visit type"))
        if f.date_from:
            parts.append(f"{alias}.start_date >= {date_lit(f.date_from)}")
        if f.date_to:
            parts.append(f"{alias}.start_date <= {date_lit(f.date_to)}")
        if f.primary_icd10_prefixes:
            parts.append(self._prefixes(f"{alias}.primary_icd10", f.primary_icd10_prefixes))
        if f.data_holders:
            parts.append(self._in(f"{alias}.data_holder", f.data_holders, v.data_holders, "hospital"))
        return " AND ".join(parts) or "TRUE"

    def criterion(self, c: Criterion, unit: str) -> str:
        v = self.store.vocabulary
        if c.kind == "age":
            parts = []
            if c.min is not None:
                parts.append(f"p.age >= {int(c.min)}")
            if c.max is not None:
                parts.append(f"p.age <= {int(c.max)}")
            sql = " AND ".join(parts) or "TRUE"
        elif c.kind == "sex":
            sql = self._in("p.sex", c.values, v.sexes, "sex")
        elif c.kind == "region":
            sql = self._in("p.region", c.values, v.regions, "region")
        elif c.kind == "data_holder":
            column = "e.data_holder" if unit == "encounter" else "p.data_holder"
            sql = self._in(column, c.values, v.data_holders, "hospital")
        elif c.kind == "condition":
            where = [self._prefixes("c.icd10", c.icd10_prefixes)]
            if c.onset_from:
                where.append(f"c.onset_date >= {date_lit(c.onset_from)}")
            if c.onset_to:
                where.append(f"c.onset_date <= {date_lit(c.onset_to)}")
            sql = (
                "EXISTS (SELECT 1 FROM conditions c WHERE c.patient_id = p.patient_id AND "
                + " AND ".join(where)
                + ")"
            )
        elif c.kind == "medication":
            where = [self._prefixes("m.atc", c.atc_prefixes)]
            if c.active:
                where.append(self.active_sql("m"))
            sql = (
                "EXISTS (SELECT 1 FROM medications m WHERE m.patient_id = p.patient_id AND "
                + " AND ".join(where)
                + ")"
            )
        elif c.kind == "observation":
            if c.comparator is not None and c.which == "latest":
                cte = self.latest_cte(c.loinc, c.date_from, c.date_to)
                sql = (
                    f"EXISTS (SELECT 1 FROM {cte} l WHERE l.patient_id = p.patient_id "
                    f"AND l.value {c.comparator} {Decimal(c.threshold)})"
                )
            else:
                where = [f"o.loinc = {lit(c.loinc)}"]
                if c.date_from:
                    where.append(f"o.date >= {date_lit(c.date_from)}")
                if c.date_to:
                    where.append(f"o.date <= {date_lit(c.date_to)}")
                if c.comparator is not None:
                    where.append(f"o.value {c.comparator} {Decimal(c.threshold)}")
                sql = (
                    "EXISTS (SELECT 1 FROM observations o WHERE o.patient_id = p.patient_id AND "
                    + " AND ".join(where)
                    + ")"
                )
        elif c.kind == "has_encounter":
            sql = (
                "EXISTS (SELECT 1 FROM encounters x WHERE x.patient_id = p.patient_id AND "
                + self.encounter_filter("x", c.filter)
                + ")"
            )
        elif c.kind == "encounter":
            if unit != "encounter":
                raise SpecError("visit criteria need a metric that counts visits")
            sql = self.encounter_filter("e", c.filter)
        else:  # pragma: no cover - the discriminated union makes this unreachable
            raise SpecError(f"unknown criterion {c.kind!r}")
        return f"(NOT ({sql}))" if c.negate else f"({sql})"

    def criteria(self, criteria: list[Criterion], unit: str) -> str:
        return " AND ".join(self.criterion(c, unit) for c in criteria) or "TRUE"

    def active_sql(self, alias: str) -> str:
        if self.active_rule == "end_empty":
            return f"{alias}.end_date IS NULL"
        return (
            f"{alias}.start_date <= {REF} AND "
            f"({alias}.end_date IS NULL OR {alias}.end_date >= {REF})"
        )

    def value(self, metric: Metric) -> str:
        src = metric.value
        if src.source == "age":
            return "p.age"
        if src.source == "length_of_stay":
            return "datediff('day', e.start_date, e.end_date)"
        return f"(SELECT l.value FROM {self.latest_cte(src.loinc)} l WHERE l.patient_id = p.patient_id)"


@dataclass
class _Dim:
    name: str
    labels: list[str]
    table_sql: str
    predicate: str  # SQL over u.* and the dimension alias


class Engine:
    def __init__(self, store: Store):
        self.store = store

    # -- dimensions -----------------------------------------------------------
    def _dimension(self, i: int, d: Dimension, unit: str, comp: _Compiler, flags: list[str]) -> _Dim:
        v = self.store.vocabulary
        alias = f"d{i}"
        name = d.label or d.kind

        def value_dim(column: str, domain, what: str) -> _Dim:
            labels = comp._known(d.values, tuple(map(str, domain)), what) if d.values else [str(x) for x in domain]
            rows = ", ".join(f"({k}, {lit(label)})" for k, label in enumerate(labels))
            return _Dim(name, labels, f"{alias}(ord, label) AS (VALUES {rows})",
                        f"CAST(u.{column} AS VARCHAR) = {alias}.label")

        if d.kind == "region":
            return value_dim("region", v.regions, "region")
        if d.kind == "data_holder":
            return value_dim("data_holder", v.data_holders, "hospital")
        if d.kind == "sex":
            return value_dim("sex", v.sexes, "sex")
        if d.kind in ("encounter_type", "encounter_year"):
            if unit != "encounter":
                raise SpecError(f"grouping by {d.kind} needs a metric that counts visits")
            domain = v.encounter_types if d.kind == "encounter_type" else v.encounter_years
            return value_dim(d.kind, domain, d.kind.replace("_", " "))
        if d.kind == "age_band":
            labels = [b.label for b in d.bands]
            if len(set(labels)) != len(labels):
                raise SpecError("age band labels must be unique")
            rows = ", ".join(
                f"({k}, {lit(b.label)}, {int(b.min)}, {'NULL' if b.max is None else int(b.max)})"
                for k, b in enumerate(d.bands)
            )
            return _Dim(
                name, labels, f"{alias}(ord, label, lo, hi) AS (VALUES {rows})",
                f"u.age >= {alias}.lo AND ({alias}.hi IS NULL OR u.age <= {alias}.hi)",
            )
        if d.kind == "category":
            whens = []
            for k, cat in enumerate(d.categories):
                flag = f"c{i}_{k}"
                flags.append(f"{comp.criteria(cat.criteria, unit)} AS {flag}")
                whens.append(f"WHEN {k} THEN u.{flag}")
            labels = [c.label for c in d.categories]
            rows = ", ".join(f"({k}, {lit(label)})" for k, label in enumerate(labels))
            return _Dim(name, labels, f"{alias}(ord, label) AS (VALUES {rows})",
                        f"CASE {alias}.ord {' '.join(whens)} END")
        raise SpecError(f"unknown dimension {d.kind!r}")

    # -- statistics -----------------------------------------------------------
    def build_sql(self, spec: QuerySpec, unit: str) -> tuple[str, list[_Dim], list[Metric]]:
        comp = _Compiler(self.store, spec.medication_active_rule)
        metrics = [m for m in spec.metrics if m.unit == unit and m.kind in ("count", "mean")]
        columns = [
            "p.patient_id AS pid", "p.age AS age", "p.sex AS sex", "p.region AS region",
        ]
        if unit == "encounter":
            columns += ["e.data_holder AS data_holder", "e.type AS encounter_type",
                        "year(e.start_date) AS encounter_year"]
            source = "encounters e JOIN patients p ON p.patient_id = e.patient_id"
        else:
            columns += ["p.data_holder AS data_holder"]
            source = "patients p"

        extra: list[str] = []
        dims = [self._dimension(i, d, unit, comp, extra) for i, d in enumerate(spec.dimensions)]
        for k, m in enumerate(metrics):
            columns.append(f"{comp.criteria(m.criteria, unit)} AS f_{k}")
            if m.kind == "mean":
                columns.append(f"{comp.value(m)} AS v_{k}")
        columns += extra

        aggregates = []
        for k, m in enumerate(metrics):
            if m.kind == "count":
                aggregates.append(f"count(u.pid) FILTER (WHERE u.f_{k}) AS f_{k}")
            else:
                has = f"u.f_{k} AND u.v_{k} IS NOT NULL"
                aggregates += [
                    f"sum(u.v_{k}) FILTER (WHERE {has}) AS s_{k}",
                    f"count(u.v_{k}) FILTER (WHERE {has}) AS n_{k}",
                    f"count(DISTINCT u.pid) FILTER (WHERE {has}) AS np_{k}",
                ]

        ctes = comp.ctes() + [
            "units AS (\n  SELECT " + ",\n         ".join(columns) + f"\n  FROM {source}\n)"
        ] + [d.table_sql for d in dims]
        with_sql = "WITH " + ",\n".join(ctes)
        if dims:
            labels = [f"d{i}.label AS g{i}" for i in range(len(dims))]
            joins = " CROSS JOIN ".join(f"d{i}" for i in range(len(dims)))
            on = " AND ".join(f"({d.predicate})" for d in dims)
            group = ", ".join(f"d{i}.ord, d{i}.label" for i in range(len(dims)))
            order = ", ".join(f"d{i}.ord" for i in range(len(dims)))
            sql = (
                f"{with_sql}\nSELECT {', '.join(labels + aggregates)}\n"
                f"FROM {joins} LEFT JOIN units u ON {on}\n"
                f"GROUP BY {group}\nORDER BY {order}"
            )
        else:
            sql = f"{with_sql}\nSELECT {', '.join(aggregates)}\nFROM units u"
        return sql, dims, metrics

    def run(self, spec: QuerySpec) -> tuple[RawResult, list[str]]:
        units = sorted({m.unit for m in spec.metrics if m.kind in ("count", "mean")})
        if not units:
            raise SpecError("the request asks for nothing that can be counted")
        cells: dict[tuple[str, ...], RawCell] = {}
        sqls: list[str] = []
        dims: list[_Dim] = []
        cur = self.store.cursor()
        for unit in units:
            sql, dims, metrics = self.build_sql(spec, unit)
            sqls.append(sql)
            n_dims = len(dims)
            for row in cur.execute(sql).fetchall():
                group = tuple(str(x) for x in row[:n_dims])
                cell = cells.setdefault(group, RawCell())
                pos = n_dims
                for m in metrics:
                    if m.kind == "count":
                        cell.counts[m.name] = int(row[pos])
                        pos += 1
                    else:
                        total, n, n_patients = row[pos : pos + 3]
                        cell.means[m.name] = RawMean(
                            Decimal(str(total)) if total is not None else Decimal(0),
                            int(n), int(n_patients),
                        )
                        pos += 3
        raw = RawResult(
            dimensions=[d.name for d in dims],
            group_labels=[d.labels for d in dims],
            metrics=[
                MetricDef(m.name, m.label, m.kind, m.visible, m.numerator, m.denominator)
                for m in spec.metrics
            ],
            cells=cells,
        )
        return raw, sqls

    # -- cohorts (secure processing environment) ------------------------------
    def cohort_sql(self, cohort: CohortSpec) -> str:
        comp = _Compiler(self.store, cohort.medication_active_rule)
        where = comp.criteria(cohort.criteria, "patient")
        ctes = comp.ctes()
        prefix = ("WITH " + ",\n".join(ctes) + "\n") if ctes else ""
        return f"{prefix}SELECT p.patient_id FROM patients p WHERE {where} ORDER BY p.patient_id"

    def active_medication_sql(self, rule: MedicationActiveRule, alias: str = "m") -> str:
        return _Compiler(self.store, rule).active_sql(alias)
