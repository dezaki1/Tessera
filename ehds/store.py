"""Loads the hospital CSVs into DuckDB with opted-out patients removed.

The raw files are read once into temporary tables. Only patients who have not
opted out (Art. 71), and their records, are copied into the working tables;
the raw tables are then dropped. Every query, statistic and SPE copy in the
app reads from this store, so opted-out patients cannot reach any output.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

REFERENCE_DATE = date(2025, 12, 31)
REFERENCE_YEAR = REFERENCE_DATE.year

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Column types are fixed so that values are exact: dates as DATE, measurement
# values as DECIMAL (sums and means are computed without floating point).
_RAW_SCHEMAS = {
    "patients": {
        "patient_id": "VARCHAR",
        "sex": "VARCHAR",
        "birth_date": "DATE",
        "region": "VARCHAR",
        "data_holder": "VARCHAR",
        "opted_out": "BOOLEAN",
    },
    "conditions": {
        "patient_id": "VARCHAR",
        "icd10": "VARCHAR",
        "description": "VARCHAR",
        "onset_date": "DATE",
    },
    "medications": {
        "patient_id": "VARCHAR",
        "atc": "VARCHAR",
        "name": "VARCHAR",
        "start_date": "DATE",
        "end_date": "DATE",
    },
    "observations": {
        "patient_id": "VARCHAR",
        "loinc": "VARCHAR",
        "name": "VARCHAR",
        "value": "DECIMAL(9,2)",
        "unit": "VARCHAR",
        "date": "DATE",
    },
    "encounters": {
        "encounter_id": "VARCHAR",
        "patient_id": "VARCHAR",
        "data_holder": "VARCHAR",
        "type": "VARCHAR",
        "start_date": "DATE",
        "end_date": "DATE",
        "primary_icd10": "VARCHAR",
    },
}

RECORD_TABLES = ("conditions", "medications", "observations", "encounters")


@dataclass(frozen=True)
class Vocabulary:
    """Code lists and category values present in the data (no patient-level content)."""

    regions: tuple[str, ...]
    data_holders: tuple[str, ...]
    sexes: tuple[str, ...]
    encounter_types: tuple[str, ...]
    encounter_years: tuple[int, ...]
    icd10: dict[str, str]  # code -> description (conditions.csv)
    primary_icd10: tuple[str, ...]  # codes used as main diagnosis of inpatient stays
    atc: dict[str, str]  # code -> drug name
    loinc: dict[str, tuple[str, str]]  # code -> (name, unit)


@dataclass
class Store:
    con: duckdb.DuckDBPyConnection
    vocabulary: Vocabulary
    eligible_patients: int
    opted_out_patients: int
    load_seconds: float
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """A cursor for one query; DuckDB cursors are safe to use from one thread each."""
        with self._lock:
            return self.con.cursor()


def _read_csv_sql(path: Path, columns: dict[str, str]) -> str:
    cols = ", ".join(f"'{name}': '{typ}'" for name, typ in columns.items())
    return (
        f"read_csv('{path.as_posix()}', header=true, columns={{{cols}}}, "
        "nullstr='', dateformat='%Y-%m-%d')"
    )


def load_store(data_dir: Path | str = DEFAULT_DATA_DIR) -> Store:
    import time

    started = time.perf_counter()
    data_dir = Path(data_dir)
    con = duckdb.connect(":memory:")
    con.execute("SET threads TO 4")

    for table, columns in _RAW_SCHEMAS.items():
        con.execute(
            f"CREATE TEMP TABLE raw_{table} AS SELECT * FROM "
            f"{_read_csv_sql(data_dir / f'{table}.csv', columns)}"
        )

    # Category domains come from the full file so that a group is still listed
    # (as "<10") even if all of its patients opted out.
    vocabulary = _build_vocabulary(con)
    opted_out = con.execute("SELECT count(*) FROM raw_patients WHERE opted_out").fetchone()[0]

    con.execute(
        f"""
        CREATE TABLE patients AS
        SELECT patient_id, sex, birth_date, region, data_holder,
               {REFERENCE_YEAR} - year(birth_date) AS age
        FROM raw_patients
        WHERE NOT coalesce(opted_out, false)
        """
    )
    for table in RECORD_TABLES:
        con.execute(
            f"CREATE TABLE {table} AS SELECT r.* FROM raw_{table} r "
            f"WHERE r.patient_id IN (SELECT patient_id FROM patients)"
        )
    for table in ("patients", *RECORD_TABLES):
        con.execute(f"DROP TABLE raw_{table}")

    con.execute("CREATE INDEX idx_patients_id ON patients(patient_id)")
    eligible = con.execute("SELECT count(*) FROM patients").fetchone()[0]

    return Store(
        con=con,
        vocabulary=vocabulary,
        eligible_patients=eligible,
        opted_out_patients=opted_out,
        load_seconds=time.perf_counter() - started,
    )


def _build_vocabulary(con: duckdb.DuckDBPyConnection) -> Vocabulary:
    def column(sql: str) -> list:
        return [row[0] for row in con.execute(sql).fetchall()]

    return Vocabulary(
        regions=tuple(column("SELECT DISTINCT region FROM raw_patients ORDER BY 1")),
        data_holders=tuple(column("SELECT DISTINCT data_holder FROM raw_patients ORDER BY 1")),
        sexes=tuple(column("SELECT DISTINCT sex FROM raw_patients ORDER BY 1")),
        encounter_types=tuple(column("SELECT DISTINCT type FROM raw_encounters ORDER BY 1")),
        encounter_years=tuple(
            column("SELECT DISTINCT year(start_date) FROM raw_encounters ORDER BY 1")
        ),
        icd10=dict(
            con.execute(
                "SELECT icd10, min(description) FROM raw_conditions GROUP BY 1 ORDER BY 1"
            ).fetchall()
        ),
        primary_icd10=tuple(
            column(
                "SELECT DISTINCT primary_icd10 FROM raw_encounters "
                "WHERE primary_icd10 IS NOT NULL ORDER BY 1"
            )
        ),
        atc=dict(
            con.execute("SELECT atc, min(name) FROM raw_medications GROUP BY 1 ORDER BY 1").fetchall()
        ),
        loinc={
            code: (name, unit)
            for code, name, unit in con.execute(
                "SELECT loinc, min(name), min(unit) FROM raw_observations GROUP BY 1 ORDER BY 1"
            ).fetchall()
        },
    )
