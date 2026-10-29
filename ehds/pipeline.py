"""End to end: request JSON -> decision -> safe statistics or SPE -> applicant and staff views."""

from __future__ import annotations

import time
from pathlib import Path

from . import explain
from .audit import AuditLog
from .decision import ARTICLE_TITLES, decide
from .disclosure import disclose, find_violations
from .engine import Engine, SpecError
from .parser import ParsedRequest, RuleParser
from .spe import SPEManager
from .store import DEFAULT_DATA_DIR, Store, load_store
from .terminology import build_terminology

ROOT = Path(__file__).resolve().parent.parent

# Fields an applicant sees. Everything else (trace, spec, SQL, flags) is staff-only.
APPLICANT_FIELDS = ("request_id", "applicant", "decision", "articles", "article_titles", "reason", "status",
                    "answer", "answer_table", "how_we_counted", "notes", "spe")


class AccessBody:
    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR, workdir: Path | str = ROOT,
                 store: Store | None = None, parser=None):
        self.store = store or load_store(data_dir)
        self.engine = Engine(self.store)
        self.terminology = build_terminology(self.store.vocabulary)
        if parser is None:
            from .llm import HybridParser, LLMClient
            parser = HybridParser(RuleParser(self.terminology), LLMClient(self.store.vocabulary))
        self.parser = parser
        workdir = Path(workdir)
        self.audit = AuditLog(workdir / "audit" / "audit.jsonl")
        self.spe = SPEManager(self.engine, workdir / "spe", self.audit)
        self.results: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}

    def process(self, request: dict, actor: str = "applicant") -> dict:
        started = time.perf_counter()
        request_id = str(request.get("id") or "request")
        decision = decide(request)
        parsed: ParsedRequest = self.parser.parse(request)

        result: dict = {
            "request_id": request_id,
            "applicant": request.get("applicant"),
            "decision": decision.decision,
            "articles": decision.articles,
            "article_titles": {a: ARTICLE_TITLES.get(a, "") for a in decision.articles},
            "reason": decision.reason,
            "status": "",
            "answer": None,
            "answer_table": None,
            "how_we_counted": None,
            "notes": [],
            "spe": None,
            "staff": {
                "trace": [f.__dict__ for f in decision.trace],
                "purposes": decision.purposes,
                "flags": list(decision.staff_flags),
                "parse": parsed.to_dict(),
                "sql": [],
                "opted_out_excluded": self.store.opted_out_patients,
            },
        }
        notes = result["notes"]
        notes.append("Patients who opted out of secondary use (Art. 71) are excluded from every number.")

        if decision.decision == "REJECT":
            result["status"] = "rejected"
            notes.append("No data was queried for this request.")
        elif decision.decision == "APPROVE_SPE":
            self._spe(request_id, parsed, result)
        else:
            self._statistics(parsed, result)

        if parsed.unresolved:
            result["staff"]["flags"].append("Parts of the request were not interpreted: " + "; ".join(parsed.unresolved))
        result["staff"]["timing_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self.audit.write("decision", request_id, actor, decision=result["decision"], status=result["status"])
        self.results[request_id] = result
        self.requests[request_id] = request
        return result

    def _statistics(self, parsed: ParsedRequest, result: dict) -> None:
        notes = result["notes"]
        if parsed.statistics is None:
            result["status"] = "needs_review"
            if parsed.patient_level_output:
                notes.append("The request asks for patient-level rows, which an anonymised statistical answer "
                             "cannot contain. A staff member will contact you.")
            else:
                notes.append("We could not interpret every definition automatically. A staff member will review "
                             "the request before the numbers are released.")
            return
        try:
            raw, sqls = self.engine.run(parsed.statistics)
        except SpecError as exc:
            result["status"] = "needs_review"
            result["staff"]["flags"].append(f"Query could not run: {exc}")
            notes.append("The request refers to something not present in the data. A staff member will review it.")
            return
        answer = disclose(raw)
        problems = find_violations(answer)
        if problems:  # cannot happen through disclose(); a last line of defence
            raise RuntimeError(f"disclosure check failed: {problems}")
        result["staff"]["sql"] = sqls
        result["answer"] = answer.to_nested()
        result["answer_table"] = answer.to_table()
        result["how_we_counted"] = explain.spec(parsed.statistics) | {"assumptions": parsed.assumptions}
        result["status"] = "answered_with_warnings" if parsed.unresolved else "answered"
        notes.append("Counts below 10 are shown as \"<10\"; percentages and means based on fewer than 10 "
                     "patients are \"suppressed\". Values are rounded to one decimal, halves up.")
        if parsed.unresolved:
            notes.append("Some sentences of the request could not be used; staff have been notified: "
                         + "; ".join(parsed.unresolved))

    def _spe(self, request_id: str, parsed: ParsedRequest, result: dict) -> None:
        if parsed.cohort is None:
            result["status"] = "needs_review"
            result["notes"].append("The cohort or record types could not be determined automatically. "
                                   "A staff member will set up the secure processing environment.")
            return
        manifest = self.spe.build(request_id, parsed.cohort)
        result["status"] = "spe_ready"
        result["answer"] = {"cohort_size": manifest["cohort_size"]}
        result["spe"] = {
            "cohort_size": manifest["cohort_size"],
            "tables": manifest["tables"],
            "contents": explain.cohort(parsed.cohort),
            "download": "blocked",
            "url": f"/spe/{request_id}",
        }
        result["how_we_counted"] = {"assumptions": parsed.assumptions}
        result["staff"]["sql"] = [manifest["cohort_sql"]]
        result["notes"].append("The cohort size is exact. The records stay inside the secure processing "
                               "environment; downloads are blocked and logged (Art. 73).")


def applicant_view(result: dict) -> dict:
    return {k: result.get(k) for k in APPLICANT_FIELDS}
