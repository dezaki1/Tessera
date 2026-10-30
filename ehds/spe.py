"""Secure processing environment (Art. 73): one separate DuckDB database per approved application.

Only the selected patients (opted-out patients are already absent from the
store) and only the record types the application names are copied in, with
their pseudonymous patient IDs. Applicants can browse and query the data
inside the SPE, but any attempt to download it is refused and logged.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

import duckdb

from .audit import AuditLog
from .engine import Engine
from .spec import CohortSpec

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PATIENT_COLUMNS = ("sex", "birth_date", "age", "region", "data_holder")
QUERY_TIMEOUT_S = 10
MAX_SQL = 10_000
MAX_CELL = 200        # characters per shown value: aggregating many rows into one text cell does not get them out
# File/network access and functions that expose the server or pack many rows into one value.
BLOCKED = re.compile(r"\b(copy|export|attach|detach|install|load|read_\w+|sniff_csv|parquet_scan|glob|httpfs|pragma\w*|"
                     r"duckdb_\w+|string_agg|group_concat|listagg|array_agg|list|json_group_\w+|histogram|"
                     r"current_setting|getenv)\b", re.I)


class DownloadBlocked(PermissionError):
    pass


class SPEManager:
    def __init__(self, engine: Engine, root: Path | str, audit: AuditLog):
        self.engine = engine
        self.root = Path(root)
        self.audit = audit

    def _dir(self, request_id: str) -> Path:
        if not SAFE_ID.match(request_id):
            raise ValueError(f"invalid request id {request_id!r}")
        return self.root / request_id

    def build(self, request_id: str, cohort: CohortSpec) -> dict:
        folder = self._dir(request_id)
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        db_path = folder / "workspace.duckdb"
        alias = f"spe_{request_id.lower().replace('-', '_')}"
        cohort_sql = self.engine.cohort_sql(cohort)
        tables: dict[str, int] = {}
        store = self.engine.store
        with store._lock:
            con = store.con
            con.execute(f"ATTACH '{db_path.as_posix()}' AS {alias}")
            try:
                con.execute(f"CREATE TABLE {alias}.cohort AS {cohort_sql}")
                for sel in cohort.records:
                    if sel.table == "patients":
                        cols = ["patient_id"] + [c for c in (sel.columns or []) if c in PATIENT_COLUMNS]
                        select = ", ".join(f"p.{c}" for c in cols)
                        con.execute(f"CREATE TABLE {alias}.patients AS SELECT {select} FROM patients p "
                                    f"JOIN {alias}.cohort USING (patient_id) ORDER BY p.patient_id")
                        continue
                    where = ""
                    if sel.table == "observations" and sel.loinc:
                        codes = ", ".join("'" + c.replace("'", "") + "'" for c in sel.loinc)
                        where = f"WHERE t.loinc IN ({codes})"
                    order = {"conditions": "patient_id, onset_date, icd10",
                             "medications": "patient_id, start_date, atc",
                             "observations": "patient_id, date, loinc",
                             "encounters": "patient_id, start_date, encounter_id"}[sel.table]
                    con.execute(f"CREATE TABLE {alias}.{sel.table} AS SELECT t.* FROM {sel.table} t "
                                f"JOIN {alias}.cohort USING (patient_id) {where} ORDER BY {order}")
                for (name,) in con.execute(
                    "SELECT table_name FROM duckdb_tables() WHERE database_name = ? ORDER BY table_name", [alias]
                ).fetchall():
                    tables[name] = con.execute(f"SELECT count(*) FROM {alias}.{name}").fetchone()[0]
            finally:
                con.execute(f"DETACH {alias}")

        manifest = {
            "request_id": request_id,
            "cohort_size": tables["cohort"],
            "tables": tables,
            "records": [r.model_dump(mode="json") for r in cohort.records],
            "cohort_sql": cohort_sql,
            "download": "blocked",
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        self.audit.write("spe_created", request_id, tables=tables)
        return manifest

    def manifest(self, request_id: str) -> dict | None:
        path = self._dir(request_id) / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _connect(self, request_id: str) -> duckdb.DuckDBPyConnection:
        path = self._dir(request_id) / "workspace.duckdb"
        if not path.exists():
            raise FileNotFoundError(request_id)
        return duckdb.connect(path.as_posix(), read_only=True)

    def preview(self, request_id: str, table: str, limit: int = 20) -> dict:
        manifest = self.manifest(request_id)
        if not manifest or table not in manifest["tables"]:
            raise KeyError(table)
        with self._connect(request_id) as con:
            cur = con.execute(f'SELECT * FROM "{table}" LIMIT {int(limit)}')
            cols = [d[0] for d in cur.description]
            return {"columns": cols, "rows": [[str(v) if v is not None else "" for v in r] for r in cur.fetchall()]}

    def query(self, request_id: str, sql: str, actor: str, limit: int = 200) -> dict:
        """Analysis inside the SPE: read-only SELECT on the workspace only."""
        sql = sql or ""
        logged = sql if len(sql) <= MAX_SQL else sql[:MAX_SQL] + "...[truncated]"
        if not sql.strip() or len(sql) > MAX_SQL:
            self.audit.write("spe_query_refused", request_id, actor, sql=logged,
                             error="empty query" if not sql.strip() else f"query longer than {MAX_SQL} characters")
            raise ValueError(f"Enter a single SELECT statement of at most {MAX_SQL} characters.")
        blocked = BLOCKED.search(sql)
        if blocked:
            self.audit.write("spe_query_refused", request_id, actor, sql=logged,
                             error=f"blocked keyword or function: {blocked.group(0)}")
            raise DownloadBlocked(f"'{blocked.group(0)}' is not available inside the SPE (no file or network access, "
                                  "no system functions, no packing of rows into one value). This attempt has been logged.")
        if not re.match(r"^\s*(select|with)\b", sql, re.I) or ";" in sql.strip().rstrip(";"):
            self.audit.write("spe_query_refused", request_id, actor, sql=logged, error="not a single SELECT statement")
            raise ValueError("Only a single SELECT statement can be run inside the SPE.")
        try:
            with self._connect(request_id) as con:
                con.execute("SET enable_external_access = false")
                timer = threading.Timer(QUERY_TIMEOUT_S, con.interrupt)
                timer.start()
                try:
                    cur = con.execute(sql.strip().rstrip(";"))
                    cols = [d[0] for d in cur.description]
                    rows = [[(str(v) if v is not None else "")[:MAX_CELL] for v in r] for r in cur.fetchmany(limit)]
                finally:
                    timer.cancel()
        except duckdb.Error as exc:  # includes DuckDB's own refusal of file and network access, and the timeout
            error = str(exc).splitlines()[0][:200] if str(exc).strip() else "no message"
            if isinstance(exc, duckdb.InterruptException):
                error = f"stopped after {QUERY_TIMEOUT_S} s"
            self.audit.write("spe_query_refused", request_id, actor, sql=logged,
                             error=f"{type(exc).__name__}: {error}")
            raise
        self.audit.write("spe_query", request_id, actor, sql=logged, rows_shown=len(rows))
        return {"columns": cols, "rows": rows}

    def download(self, request_id: str, actor: str, what: str = "workspace") -> None:
        self.audit.write("download_blocked", request_id, actor, target=what,
                         rule="Art. 73: data in a secure processing environment cannot be downloaded")
        raise DownloadBlocked(
            "Downloads are blocked: data in a secure processing environment can only be processed inside it "
            "(Art. 73). Only anonymised results, such as model weights, may leave after review. "
            "This attempt has been logged.")
