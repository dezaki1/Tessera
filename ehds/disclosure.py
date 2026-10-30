"""Small-cell rule: the only way raw query results become an answer.

The engine returns `RawResult`, which has no serialiser. `disclose()` is the
only function that can create a `SafeAnswer`, and it applies the rule to
every value:

* every requested group is listed, even if no patient falls into it;
* counts below 10 (including 0) are shown as "<10";
* a percentage is "suppressed" when its numerator or denominator is below 10;
* a mean is "suppressed" when it is based on fewer than 10 patients;
* everything else is rounded to one decimal, halves up.

Arithmetic uses `Decimal` on exact inputs, so 37.05 rounds to 37.1 rather than
to whatever binary floating point makes of it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal, Union

SMALL_CELL_THRESHOLD = 10
LESS_THAN = "<10"
SUPPRESSED = "suppressed"

MetricKind = Literal["count", "percentage", "mean"]
SafeValue = Union[int, float, str]


@dataclass(frozen=True)
class MetricDef:
    name: str
    label: str
    kind: MetricKind
    visible: bool = True
    numerator: str | None = None  # percentage: name of the count metric on top
    denominator: str | None = None  # percentage: name of the count metric below


@dataclass(frozen=True)
class RawMean:
    total: Decimal
    n: int  # values averaged
    n_patients: int  # distinct patients behind those values


@dataclass
class RawCell:
    counts: dict[str, int] = field(default_factory=dict)
    means: dict[str, RawMean] = field(default_factory=dict)
    # For counts of stays: how many distinct patients those stays belong to.
    patients: dict[str, int] = field(default_factory=dict)


@dataclass
class RawResult:
    """Unrounded, unsuppressed numbers. Never leaves the app; has no serialiser on purpose."""

    dimensions: list[str]
    group_labels: list[list[str]]  # ordered domain of every dimension
    metrics: list[MetricDef]
    cells: dict[tuple[str, ...], RawCell]

    def __repr__(self) -> str:  # keep raw values out of logs and tracebacks
        return f"RawResult(dimensions={self.dimensions}, cells={len(self.cells)})"


_ISSUED = object()


@dataclass(frozen=True)
class SafeAnswer:
    dimensions: tuple[str, ...]
    columns: tuple[MetricDef, ...]
    rows: tuple[tuple[tuple[str, ...], tuple[SafeValue, ...]], ...]
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUED:
            raise TypeError("SafeAnswer can only be created by disclosure.disclose()")

    def to_nested(self) -> dict:
        """{group: {subgroup: {metric: value}}}; a plain {metric: value} without groups."""
        out: dict = {}
        for group, values in self.rows:
            metrics = {col.name: value for col, value in zip(self.columns, values)}
            if not group:
                return metrics
            node = out
            for label in group[:-1]:
                node = node.setdefault(label, {})
            node[group[-1]] = metrics
        return out

    def to_table(self) -> dict:
        return {
            "dimensions": list(self.dimensions),
            "columns": [
                {"name": c.name, "label": c.label, "kind": c.kind} for c in self.columns
            ],
            "rows": [
                {"group": list(group), "values": list(values)} for group, values in self.rows
            ],
        }


def round_half_up(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def safe_count(n: int, n_patients: int | None = None) -> SafeValue:
    """A count below 10 is "<10". A count of stays is also "<10" when it comes from fewer than
    10 patients: 11 stays of 5 patients describe those 5 people as precisely as a count of 5."""
    if n < SMALL_CELL_THRESHOLD or (n_patients is not None and n_patients < SMALL_CELL_THRESHOLD):
        return LESS_THAN
    return int(n)


def safe_percentage(numerator: int, denominator: int,
                    numerator_patients: int | None = None, denominator_patients: int | None = None) -> SafeValue:
    small = [x for x in (numerator, denominator, numerator_patients, denominator_patients) if x is not None]
    if min(small) < SMALL_CELL_THRESHOLD:
        return SUPPRESSED
    with localcontext() as ctx:
        ctx.prec = 50
        return round_half_up(Decimal(numerator) * 100 / Decimal(denominator))


def safe_mean(raw: RawMean) -> SafeValue:
    if raw.n < SMALL_CELL_THRESHOLD or raw.n_patients < SMALL_CELL_THRESHOLD:
        return SUPPRESSED
    with localcontext() as ctx:
        ctx.prec = 50
        return round_half_up(Decimal(raw.total) / Decimal(raw.n))


def disclose(raw: RawResult) -> SafeAnswer:
    by_name = {m.name: m for m in raw.metrics}
    for m in raw.metrics:
        if m.kind == "percentage":
            for ref in (m.numerator, m.denominator):
                if ref not in by_name or by_name[ref].kind != "count":
                    raise ValueError(f"percentage {m.name!r} must refer to count metrics, got {ref!r}")

    visible = tuple(m for m in raw.metrics if m.visible)
    rows = []
    # The grid is built here from the requested domains, not from the query
    # result, so a group with no patients is still listed.
    for group in itertools.product(*raw.group_labels):
        cell = raw.cells.get(tuple(group), RawCell())
        values: list[SafeValue] = []
        for m in visible:
            if m.kind == "count":
                values.append(safe_count(cell.counts.get(m.name, 0), cell.patients.get(m.name)))
            elif m.kind == "percentage":
                values.append(
                    safe_percentage(
                        cell.counts.get(m.numerator, 0), cell.counts.get(m.denominator, 0),
                        cell.patients.get(m.numerator), cell.patients.get(m.denominator),
                    )
                )
            elif m.kind == "mean":
                values.append(safe_mean(cell.means.get(m.name, RawMean(Decimal(0), 0, 0))))
            else:
                raise ValueError(f"unknown metric kind {m.kind!r}")
        rows.append((tuple(group), tuple(values)))

    unknown = set(raw.cells) - set(itertools.product(*raw.group_labels))
    if unknown:
        raise ValueError(f"result has groups outside the requested domains: {sorted(unknown)[:3]}")

    return SafeAnswer(
        dimensions=tuple(raw.dimensions), columns=visible, rows=tuple(rows), _issuer=_ISSUED
    )


def find_violations(answer: SafeAnswer) -> list[str]:
    """Independent re-check of a finished answer; used before release and by the evaluation."""
    problems = []
    for group, values in answer.rows:
        for col, value in zip(answer.columns, values):
            where = f"{'/'.join(group) or 'total'}.{col.name}"
            if col.kind == "count":
                if isinstance(value, str):
                    if value != LESS_THAN:
                        problems.append(f"{where}: unexpected text {value!r}")
                elif not isinstance(value, int) or value < SMALL_CELL_THRESHOLD:
                    problems.append(f"{where}: count {value!r} is below {SMALL_CELL_THRESHOLD}")
            elif isinstance(value, str) and value != SUPPRESSED:
                problems.append(f"{where}: unexpected text {value!r}")
    return problems
