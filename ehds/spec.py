"""Structured query specification produced from a request's definitions.

A `QuerySpec` says what to count, not how: groups (dimensions) and metrics,
each metric with the criteria a patient or hospital stay must meet. The rule
parser and the optional LLM both produce this format; the engine is the only
thing that turns it into SQL. Codes are validated here, so nothing from the
request text reaches SQL unchecked.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ICD10_RE = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{0,4})?$")
ATC_RE = re.compile(r"^[A-Z]([0-9]{2}([A-Z]([A-Z]([0-9]{2})?)?)?)?$")
LOINC_RE = re.compile(r"^[0-9]{1,7}-[0-9]$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_codes(values: list[str], pattern: re.Pattern, what: str) -> list[str]:
    cleaned = []
    for v in values:
        v = v.strip().upper()
        if not pattern.match(v):
            raise ValueError(f"not a valid {what} code prefix: {v!r}")
        cleaned.append(v)
    if not cleaned:
        raise ValueError(f"at least one {what} code is required")
    return cleaned


# --- Criteria -----------------------------------------------------------------


class AgeCriterion(_Model):
    kind: Literal["age"] = "age"
    min: int | None = Field(None, ge=0, le=150)
    max: int | None = Field(None, ge=0, le=150)
    negate: bool = False


class SexCriterion(_Model):
    kind: Literal["sex"] = "sex"
    values: list[str]
    negate: bool = False


class RegionCriterion(_Model):
    kind: Literal["region"] = "region"
    values: list[str]
    negate: bool = False


class DataHolderCriterion(_Model):
    """Hospital holding the record. For stays, the hospital of the stay."""

    kind: Literal["data_holder"] = "data_holder"
    values: list[str]
    negate: bool = False


class ConditionCriterion(_Model):
    """At least one row in conditions.csv with a matching ICD-10 code."""

    kind: Literal["condition"] = "condition"
    icd10_prefixes: list[str]
    onset_from: date | None = None
    onset_to: date | None = None
    negate: bool = False

    @field_validator("icd10_prefixes")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        return _check_codes(v, ICD10_RE, "ICD-10")


class MedicationCriterion(_Model):
    """A prescription with a matching ATC code; `active` means active on the reference date."""

    kind: Literal["medication"] = "medication"
    atc_prefixes: list[str]
    active: bool = False
    negate: bool = False

    @field_validator("atc_prefixes")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        return _check_codes(v, ATC_RE, "ATC")


class ObservationCriterion(_Model):
    """A measurement with this LOINC code, optionally compared with a threshold."""

    kind: Literal["observation"] = "observation"
    loinc: str
    which: Literal["latest", "any"] = "latest"
    comparator: Literal[">", ">=", "<", "<=", "="] | None = None
    threshold: Decimal | None = None
    date_from: date | None = None
    date_to: date | None = None
    negate: bool = False

    @field_validator("loinc")
    @classmethod
    def _loinc(cls, v: str) -> str:
        return _check_codes([v], LOINC_RE, "LOINC")[0]

    @model_validator(mode="after")
    def _threshold(self):
        if (self.comparator is None) != (self.threshold is None):
            raise ValueError("comparator and threshold go together")
        return self


class EncounterFilter(_Model):
    types: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    primary_icd10_prefixes: list[str] | None = None
    data_holders: list[str] | None = None

    @field_validator("primary_icd10_prefixes")
    @classmethod
    def _codes(cls, v):
        return None if v is None else _check_codes(v, ICD10_RE, "ICD-10")


class HasEncounterCriterion(_Model):
    """The patient has at least one visit matching the filter."""

    kind: Literal["has_encounter"] = "has_encounter"
    filter: EncounterFilter
    negate: bool = False


class EncounterCriterion(_Model):
    """The counted visit itself matches the filter (only for metrics counting visits)."""

    kind: Literal["encounter"] = "encounter"
    filter: EncounterFilter
    negate: bool = False


Criterion = Annotated[
    Union[
        AgeCriterion,
        SexCriterion,
        RegionCriterion,
        DataHolderCriterion,
        ConditionCriterion,
        MedicationCriterion,
        ObservationCriterion,
        HasEncounterCriterion,
        EncounterCriterion,
    ],
    Field(discriminator="kind"),
]


# --- Groups -------------------------------------------------------------------


class AgeBand(_Model):
    label: str
    min: int = Field(ge=0, le=150)
    max: int | None = Field(None, ge=0, le=150)


class Category(_Model):
    label: str
    criteria: list[Criterion]


class Dimension(_Model):
    kind: Literal[
        "region", "data_holder", "sex", "age_band", "encounter_type", "encounter_year", "category"
    ]
    label: str = ""
    values: list[str] | None = None  # explicit subset/order; default: every value in the data
    bands: list[AgeBand] | None = None  # age_band
    categories: list[Category] | None = None  # category

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "age_band" and not self.bands:
            raise ValueError("age_band needs bands")
        if self.kind == "category" and not self.categories:
            raise ValueError("category needs categories")
        return self


# --- Metrics ------------------------------------------------------------------


class ValueSource(_Model):
    source: Literal["observation", "age", "length_of_stay"]
    loinc: str | None = None
    which: Literal["latest"] = "latest"

    @model_validator(mode="after")
    def _loinc(self):
        if self.source == "observation":
            if not self.loinc:
                raise ValueError("observation value needs a LOINC code")
            _check_codes([self.loinc], LOINC_RE, "LOINC")
        return self


class Metric(_Model):
    name: str
    label: str
    kind: Literal["count", "percentage", "mean"]
    unit: Literal["patient", "encounter"] = "patient"
    criteria: list[Criterion] = Field(default_factory=list)
    visible: bool = True
    numerator: str | None = None
    denominator: str | None = None
    value: ValueSource | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError(f"metric name must be snake_case: {v!r}")
        return v

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "percentage" and not (self.numerator and self.denominator):
            raise ValueError("percentage needs numerator and denominator")
        if self.kind == "mean" and self.value is None:
            raise ValueError("mean needs a value source")
        return self


MedicationActiveRule = Literal["start_and_end", "end_empty"]


class QuerySpec(_Model):
    dimensions: list[Dimension] = Field(default_factory=list)
    metrics: list[Metric]
    medication_active_rule: MedicationActiveRule = "start_and_end"

    @model_validator(mode="after")
    def _references(self):
        by_name = {m.name: m for m in self.metrics}
        if len(by_name) != len(self.metrics):
            raise ValueError("metric names must be unique")
        for m in self.metrics:
            if m.kind == "percentage":
                for ref in (m.numerator, m.denominator):
                    other = by_name.get(ref)
                    if other is None or other.kind != "count":
                        raise ValueError(f"{m.name}: {ref!r} is not a count metric")
                    if other.unit != m.unit:
                        raise ValueError(f"{m.name}: numerator and denominator must count the same unit")
            if m.unit == "patient" and any(c.kind == "encounter" for c in m.criteria):
                raise ValueError(f"{m.name}: visit criteria need a metric that counts visits")
            if m.value and m.value.source == "length_of_stay" and m.unit != "encounter":
                raise ValueError(f"{m.name}: length of stay is a property of visits")
        return self


# --- Secure processing environment ---------------------------------------------

RecordTable = Literal["patients", "conditions", "medications", "observations", "encounters"]


class RecordSelection(_Model):
    table: RecordTable
    loinc: list[str] | None = None  # observations: only these measurements
    columns: list[str] | None = None  # patients: only these columns (patient_id always kept)


class CohortSpec(_Model):
    criteria: list[Criterion]
    records: list[RecordSelection]
    medication_active_rule: MedicationActiveRule = "start_and_end"
