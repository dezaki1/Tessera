"""Evaluation harness: runs every request 3 times and reports the README metrics.

    python -m evaluation.run_eval

* Correct decisions: decision and articles match expected.json (extra Art. 67/71 allowed).
* Correct numbers:   approved requests where every expected value appears under its group.
* Privacy leaks:     statistical answers with a patient-level record, a count below 10, a value
                     that should have been suppressed, or opted-out patients (also checked in SPE copies).
* Consistency:       requests whose answer is identical in all runs.
* Time to answer:    slowest request, from submitting to the API until the answer arrives.

expected.json is read here only, for scoring. The app never sees it.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATIENT_ID = re.compile(r"\bp-[0-9a-f]{8}\b")
ALLOWED_EXTRA_ARTICLES = {"67", "71"}


# --- helpers -------------------------------------------------------------------------


def leaves(node) -> list:
    if isinstance(node, dict):
        return [x for v in node.values() for x in leaves(v)]
    if isinstance(node, list):
        return [x for v in node for x in leaves(v)]
    return [node]


def same_value(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(float(a) - float(b)) < 1e-9


def numbers_match(expected: dict, ours) -> list[str]:
    """Every expected value must appear under its group in our answer (layout may differ)."""
    missing = []
    if not isinstance(ours, dict):
        return ["no answer"]
    for group, value in expected.items():
        if group in ours:
            pool = leaves(ours[group])
        else:  # e.g. {"cohort_size": 1152}: a value without a group
            pool = leaves(ours)
        for v in leaves(value):
            if not any(same_value(v, p) for p in pool):
                missing.append(f"{group}: {v}")
    return missing


def canonical(record: dict) -> str:
    """Everything the applicant and staff see, except the measured time."""
    full = json.loads(json.dumps(record["staff"]))
    (full.get("staff") or {}).pop("timing_ms", None)
    return json.dumps({"applicant": record["applicant"], "staff": full}, sort_keys=True, ensure_ascii=False)


# --- privacy checks ----------------------------------------------------------------------


def privacy_problems(rid: str, record: dict, engine, opted_out: set[str], workdir: Path) -> list[str]:
    from ehds.disclosure import LESS_THAN, SUPPRESSED, SMALL_CELL_THRESHOLD
    from ehds.spec import QuerySpec

    problems = []
    applicant = record["applicant"]
    text = json.dumps(applicant, ensure_ascii=False)
    decision = applicant["decision"]

    if decision == "REJECT" and applicant.get("answer") is not None:
        problems.append("rejected request returned data")
    if decision == "APPROVE_STATISTICAL":
        if PATIENT_ID.search(text):
            problems.append("patient ID in a statistical answer")
        table = applicant.get("answer_table")
        if table:
            kinds = [c["kind"] for c in table["columns"]]
            names = [c["name"] for c in table["columns"]]
            for row in table["rows"]:
                for kind, value in zip(kinds, row["values"]):
                    if kind == "count" and not (value == LESS_THAN or (isinstance(value, int) and value >= SMALL_CELL_THRESHOLD)):
                        problems.append(f"count {value!r} shown in {row['group']}")
                    if kind != "count" and isinstance(value, str) and value != SUPPRESSED:
                        problems.append(f"unexpected text {value!r}")
            # Re-run the query independently and check every cell against the raw numbers.
            spec = QuerySpec.model_validate(record["staff"]["staff"]["parse"]["statistics"])
            raw, _ = engine.run(spec)
            metrics = {m.name: m for m in raw.metrics}
            shown = {tuple(row["group"]): dict(zip(names, row["values"])) for row in table["rows"]}
            for group, cell in raw.cells.items():
                for name, value in shown.get(group, {}).items():
                    m = metrics[name]
                    if m.kind == "count" and cell.counts.get(name, 0) < SMALL_CELL_THRESHOLD and value != LESS_THAN:
                        problems.append(f"{group}.{name}: small count not hidden")
                    if m.kind == "percentage" and min(cell.counts.get(m.numerator, 0), cell.counts.get(m.denominator, 0)) < SMALL_CELL_THRESHOLD and value != SUPPRESSED:
                        problems.append(f"{group}.{name}: percentage not suppressed")
                    if m.kind == "mean":
                        mean = cell.means.get(name)
                        if (mean is None or mean.n_patients < SMALL_CELL_THRESHOLD) and value != SUPPRESSED:
                            problems.append(f"{group}.{name}: mean not suppressed")
            if len(table["rows"]) < len(raw.cells):
                problems.append("requested groups missing")
    if decision == "APPROVE_SPE":
        import duckdb
        db = workdir / "spe" / rid / "workspace.duckdb"
        if not db.exists():
            problems.append("SPE workspace missing")
        else:
            with duckdb.connect(db.as_posix(), read_only=True) as con:
                for (table,) in con.execute("SELECT table_name FROM duckdb_tables()").fetchall():
                    ids = {r[0] for r in con.execute(f'SELECT DISTINCT patient_id FROM "{table}"').fetchall()}
                    if ids & opted_out:
                        problems.append(f"opted-out patients in SPE table {table}")
    return problems


# --- main ----------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--requests", default=str(ROOT / "requests"))
    ap.add_argument("--out", default=str(ROOT / "runs"))
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    runs = []
    for i in range(1, args.runs + 1):
        workdir = out / f"work{i}"
        env = {**os.environ, "PYTHONHASHSEED": str(1000 + i)}
        subprocess.run([sys.executable, "-m", "evaluation.run_once", "--requests", args.requests,
                        "--workdir", str(workdir), "--out", str(out / f"run{i}.json")],
                       cwd=ROOT, env=env, check=True)
        runs.append((workdir, json.loads((out / f"run{i}.json").read_text(encoding="utf-8"))))

    expected = json.loads((Path(args.requests) / "expected.json").read_text(encoding="utf-8"))
    with open(ROOT / "data" / "patients.csv", newline="", encoding="utf-8") as f:
        opted_out = {row["patient_id"] for row in csv.DictReader(f) if row["opted_out"] == "true"}

    from ehds.engine import Engine
    from ehds.store import load_store

    store = load_store(ROOT / "data")
    engine = Engine(store)
    store_ids = {r[0] for r in store.cursor().execute("SELECT patient_id FROM patients").fetchall()}

    first_workdir, first = runs[0]
    ids = sorted(first["results"])
    rows = []
    totals = {"decisions": 0, "numbers": 0, "numbers_of": 0, "leaks": 0, "consistent": 0}
    slowest = 0.0
    for rid in ids:
        exp = expected.get(rid)
        rec = first["results"][rid]
        app = rec["applicant"]
        row = {"id": rid, "decision": app["decision"], "articles": ",".join(app["articles"]), "status": app["status"]}

        if exp:
            ours, theirs = set(app["articles"]), set(exp["articles"])
            ok = app["decision"] == exp["decision"] and theirs <= ours and ours - theirs <= ALLOWED_EXTRA_ARTICLES
            row["decision_ok"] = ok
            totals["decisions"] += ok
            if exp["decision"] != "REJECT" and exp.get("answer"):
                totals["numbers_of"] += 1
                missing = numbers_match(exp["answer"], app.get("answer"))
                row["numbers_ok"] = not missing
                row["missing"] = missing[:5]
                totals["numbers"] += not missing

        problems = []
        for workdir, run in runs:
            problems += privacy_problems(rid, run["results"][rid], engine, opted_out, workdir)
        row["leaks"] = sorted(set(problems))
        totals["leaks"] += bool(problems)

        variants = {canonical(run["results"][rid]) for _, run in runs}
        row["consistent"] = len(variants) == 1
        totals["consistent"] += row["consistent"]
        row["seconds"] = max(run["results"][rid]["seconds"] for _, run in runs)
        slowest = max(slowest, row["seconds"])
        rows.append(row)

    opted_out_in_store = len(store_ids & opted_out)
    n = len(ids)
    with_expected = sum(1 for r in ids if r in expected)
    summary = {
        "correct_decisions": f"{totals['decisions']}/{with_expected}",
        "correct_numbers": f"{totals['numbers']}/{totals['numbers_of']}",
        "privacy_leaks": totals["leaks"] + (1 if opted_out_in_store else 0),
        "consistency": f"{totals['consistent']}/{n}",
        "slowest_seconds": round(slowest, 3),
        "startup_seconds": round(max(run["startup_seconds"] for _, run in runs), 2),
        "opted_out_patients_in_store": opted_out_in_store,
        "runs": len(runs),
    }

    lines = ["| Request | Decision | Articles | Status | Decision ok | Numbers ok | Leaks | Consistent | Seconds (max of runs) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        num = "" if "numbers_ok" not in r else ("yes" if r["numbers_ok"] else "NO " + "; ".join(r["missing"]))
        lines.append(f"| {r['id']} | {r['decision']} | {r['articles']} | {r['status']} | "
                     f"{'yes' if r.get('decision_ok') else 'NO' if 'decision_ok' in r else ''} | {num} | "
                     f"{len(r['leaks']) or 0}{' ' + '; '.join(r['leaks']) if r['leaks'] else ''} | "
                     f"{'yes' if r['consistent'] else 'NO'} | {r['seconds']:.3f} |")
    report = "\n".join([
        "## Metrics", "",
        "| Metric | Result | Target |", "|---|---|---|",
        f"| Correct decisions | {summary['correct_decisions']} | 10 of 10 |",
        f"| Correct numbers | {summary['correct_numbers']} | 6 of 6 |",
        f"| Privacy leaks | {summary['privacy_leaks']} | 0 |",
        f"| Consistency ({summary['runs']} runs) | {summary['consistency']} | 10 of 10 |",
        f"| Slowest time to answer | {summary['slowest_seconds']} s | under 60 s |",
        "", f"App start-up (data load, once): {summary['startup_seconds']} s. "
            f"Opted-out patients in the working store: {summary['opted_out_patients_in_store']}.",
        "", "## Per request", "", *lines, ""])
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(report)
    ok = (totals["decisions"] == with_expected and totals["numbers"] == totals["numbers_of"]
          and summary["privacy_leaks"] == 0 and totals["consistent"] == n)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
