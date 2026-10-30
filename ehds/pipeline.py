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
from .spe import SAFE_ID, SPEManager
from .store import DEFAULT_DATA_DIR, Store, load_store
from .terminology import build_terminology

ROOT = Path(__file__).resolve().parent.parent

class InvalidRequestId(ValueError):
    pass


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

    def free_id(self, wanted: str) -> str:
        """A new submission never replaces an earlier result or SPE: a used id gets a suffix."""
        def taken(i: str) -> bool:
            return i in self.results or self.spe.manifest(i) is not None
        if not taken(wanted):
            return wanted
        n = 2
        while taken(f"{wanted}-{n}"):
            n += 1
        return f"{wanted}-{n}"

    def process(self, request: dict, actor: str = "applicant") -> dict:
        started = time.perf_counter()
        wanted = request.get("id")
        if not isinstance(wanted, str) or not SAFE_ID.match(wanted):
            raise InvalidRequestId(
                f"Request id {wanted!r} is not valid: use 1-64 letters, digits, '-' or '_'.")
        request_id = self.free_id(wanted)
        if request_id != wanted:
            request = {**request, "id": request_id}
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
        if request_id != wanted:
            notes.append(f"A request with id {wanted} already exists, so this submission is stored as "
                         f"{request_id}. The earlier result was not changed.")
        if decision.decision != "REJECT":
            notes.append("Patients who opted out of secondary use are excluded from every number (Art. 71).")

        if decision.decision == "REJECT":
            result["status"] = "rejected"
            notes.append("No data was queried for this request.")
        elif decision.hold_for_review:
            # Fail safe: a commercial applicant whose purpose matched no Art. 53(1) purpose open to it.
            result["status"] = "needs_review"
            notes.append("We could not match your purpose to a purpose in Art. 53(1) that is open to your "
                         "organisation, so no data has been queried or released yet. A staff member will review "
                         "the purpose and contact you.")
        elif decision.decision == "APPROVE_SPE" and decision.reidentification:
            result["status"] = "needs_review"
            result["staff"]["flags"].append("No SPE was set up: the application states an intent to re-identify "
                                            "or link the people in the data, which Chapter IV prohibits.")
            notes.append("Your application says the data would be linked to your own records or used to "
                         "re-identify people. Re-identification is prohibited under Chapter IV of Regulation "
                         "(EU) 2025/327, so no secure processing environment was set up. A staff member will "
                         "contact you.")
        elif decision.decision == "APPROVE_SPE":
            self._spe(request_id, parsed, result)
        else:
            self._statistics(parsed, result)

        if parsed.unresolved:
            result["staff"]["flags"].append("Parts of the request were not interpreted: " + "; ".join(parsed.unresolved))
        if parsed.cohort_unresolved and decision.decision == "APPROVE_SPE":
            result["staff"]["flags"].append("SPE cohort: " + "; ".join(parsed.cohort_unresolved))
        if result["status"] == "needs_review" and decision.decision != "REJECT":
            # The reason must not say the answer was given when it is being held.
            result["reason"] = (result["reason"]
                                .replace("The question is answered with anonymised statistics (Art. 69).",
                                         "The answer will be given as anonymised statistics (Art. 69) once a staff "
                                         "member has reviewed the request; nothing has been released yet.")
                                .replace("Access is granted only under a data permit (Art. 68)",
                                         "Access can only be granted under a data permit (Art. 68), after staff review,"))
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

    def _cohort_mismatch(self, parsed: ParsedRequest) -> str | None:
        """Cross-check: the SPE cohort must have the size the request's own numbers describe.

        The cohort and the requested count are parsed separately; if they disagree, one of them
        is wrong, and copying patient-level data on a guess is not acceptable.
        """
        stats = parsed.statistics
        if stats is None or stats.dimensions:
            return None
        counts = [m for m in stats.metrics if m.kind == "count" and m.visible and m.unit == "patient"]
        if len(counts) != 1:
            return None
        try:
            raw, _ = self.engine.run(stats.model_copy(update={"metrics": counts}))
            requested = raw.cells[()].counts[counts[0].name]
            cohort = len(self.store.cursor().execute(self.engine.cohort_sql(parsed.cohort)).fetchall())
        except SpecError:
            return None
        if requested != cohort:
            return (f"SPE cohort check failed: the cohort definition selects {cohort} patients but the requested "
                    f"count ('{counts[0].label}') is {requested}. No data was copied.")
        return None

    def _spe(self, request_id: str, parsed: ParsedRequest, result: dict) -> None:
        if parsed.cohort is None:
            result["staff"]["flags"].append("SPE not set up: " + ("; ".join(parsed.cohort_unresolved) or "no cohort"))
            result["status"] = "needs_review"
            result["notes"].append("The cohort or record types could not be determined automatically. "
                                   "A staff member will set up the secure processing environment.")
            return
        mismatch = self._cohort_mismatch(parsed)
        if mismatch:
            result["status"] = "needs_review"
            result["staff"]["flags"].append(mismatch)
            result["notes"].append("The patients to include could not be determined unambiguously. A staff member "
                                   "will check the cohort before the secure processing environment is set up.")
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
        result["how_we_counted"] = {"cohort": explain.cohort(parsed.cohort), "assumptions": parsed.assumptions}
        result["staff"]["sql"] = [manifest["cohort_sql"]]
        result["notes"].append("The cohort size is exact. The records stay inside the secure processing "
                               "environment; downloads are blocked and logged (Art. 73).")


def applicant_view(result: dict) -> dict:
    return {k: result.get(k) for k in APPLICANT_FIELDS}
