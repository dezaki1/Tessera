# Tessera — an automated EHDS Health Data Access Body

A prototype access body for the European Health Data Space (Track 3). It takes a request as JSON and
automatically:

1. decides `REJECT`, `APPROVE_STATISTICAL` or `APPROVE_SPE` and cites the articles of Regulation (EU) 2025/327;
2. for statistics, generates the query from the request's own definitions, runs it and applies the small-cell rule;
3. for a secure processing environment (SPE), copies only the selected patients and the record types the
   application names into a separate database, reports the exact patient count and blocks downloads;
4. shows the decision and its reasons to the applicant and, in more detail, to staff.

Everything runs locally and offline. No LLM is needed. An optional LLM step can help with definitions the
rules can't parse; only the request text is sent, never patient data.

## Results on the 10 sample requests

`python -m evaluation.run_eval` submits every request through the API in 3 fresh processes (each with a
different hash seed) and scores the README metrics:

| Metric | Result | Target |
|---|---|---|
| Correct decisions | 10/10 | 10 of 10 |
| Correct numbers | 6/6 | 6 of 6 |
| Privacy leaks | 0 | 0 |
| Consistency (3 runs) | 10/10 | 10 of 10 |
| Slowest time to answer | 0.09 s | under 60 s |

Loading the data takes about 1 s, once, when the app starts. The full per-request table is written to
`runs/report.md`. `expected.json` is read only by the evaluation script, never by the app.

How the checks work:

- **Correct numbers.** Every expected value must appear under its group, as the README defines it.
- **Privacy leaks.** The script re-runs each query on its own and compares every released cell with the raw
  numbers. A count under 10 that isn't `<10` counts as a leak. So does a percentage or mean resting on fewer
  than 10 patients that isn't `suppressed`, a group that's missing, a patient ID anywhere in a statistical
  answer, or an opted-out patient in the store or in any SPE copy. `tests/test_evaluation.py` checks that
  these checks catch tampered answers.
- **Consistency.** Compares the full applicant and staff output (everything except the timing) across the
  3 runs.

**Generalisation.** `examples/heldout/` holds 8 extra requests that use things none of the samples use:
grouping by hospital and by hospital × sex, stays filtered by `primary_icd10` (by code and by name), names
without codes (SGLT2 inhibitor, eGFR, pneumonia), an HbA1c threshold, a region filter that appears only in
the question, a different SPE cohort, and alcohol marketing. `tests/test_heldout.py` checks every released
value against an independent pandas implementation. All 8 pass.

## Run it

Python 3.11+.

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Open http://localhost:8000:

- **Applicant portal** (`/`). Submit a sample request or paste your own JSON.
- **Staff desk** (`/staff`). "Process all sample requests", then open any request.
- **SPE workspace** (`/spe/R05`). After R05 has been submitted: preview tables, run read-only SQL, and see the
  download button refused and logged.

Evaluation and tests:

```bash
.venv/bin/python -m evaluation.run_eval
```

```bash
.venv/bin/python -m pytest -q
```

The JSON API, which the evaluation also uses: `POST /api/requests` (applicant view),
`GET /api/staff/requests/{id}` (full staff detail), `GET /api/spe/{id}/download` (always `403`, logged).

## Architecture

```
request JSON
   │
   ├─► decision.py ──── explicit rules, in order ─────────────► decision, articles, reason, rule trace
   │
   ├─► parser.py (+ terminology.py) ── definitions ─► QuerySpec / CohortSpec      (llm.py: optional fallback)
   │
   ├─ REJECT ─────────► nothing is queried
   ├─ STATISTICAL ────► engine.py ─ SQL ─► store.py (DuckDB) ─► RawResult ─► disclosure.py ─► SafeAnswer
   └─ SPE ────────────► spe.py ─ copies cohort + named record types ─► spe/<id>/workspace.duckdb
                                                                        (browse/query inside; download → 403 + audit log)
   ▼
pipeline.py ─► applicant view / staff view ─► app/main.py (FastAPI + Jinja2 pages, JSON API)
```

| Component | What it does |
|---|---|
| `ehds/store.py` | Loads the 5 CSVs into in-memory DuckDB with exact types (`DATE`, `DECIMAL`). Only patients who did not opt out, and their records, are copied into the working tables. The raw tables are then dropped, so nothing downstream can reach an opted-out patient. Also records the data's vocabulary (regions, hospitals, code lists) so empty groups can still be listed. |
| `ehds/terminology.py` | Maps plain-English names to codes: ICD-10 categories, ATC classes and drug names, LOINC measurements, regions (including English names such as "Vienna"), hospitals, sex words, adult/child, and visit types. Codes written in the request always take precedence. |
| `ehds/parser.py` | Rule-based parser. It reads concept definitions (`X = …`), age bands, the medication-activity rule, "use the latest …", "count stays (not patients)" and the `Report per …:` line, whose numbers can be counts, percentages and means, with back-references like "of them", "that share" and "their". A main diagnosis maps to `primary_icd10` only when the request names it; otherwise a diagnosis is a row in `conditions.csv`. Restrictions that appear only in the question (e.g. "women *with breast cancer*") are applied to every number and recorded as assumptions. Anything not understood is listed, never guessed. |
| `ehds/spec.py` | The QuerySpec/CohortSpec schema (Pydantic). Codes are validated by pattern; this is also the JSON schema the optional LLM must answer in. |
| `ehds/engine.py` | Compiles a spec into SQL: one query per counting unit (patients or stays), with the requested groups as `VALUES` tables cross-joined and then left-joined to the units, so empty groups come back as zeros. Category values are checked against the data before being quoted into SQL. |
| `ehds/disclosure.py` | The small-cell rule, and the only code that can create a `SafeAnswer`. It builds the full grid of requested groups itself, shows counts under 10 as `<10`, suppresses percentages whose numerator or denominator is under 10 and means of fewer than 10 patients, and rounds half up using `Decimal` on exact inputs. `RawResult` has no serialiser. Unit-tested separately, including the rounding edge cases. |
| `ehds/decision.py` | The four ordered rules. Prohibited purposes (insurance/credit pricing, employment decisions, advertising/marketing, harmful products such as nicotine or alcohol, detrimental decisions) need a trigger *and* a context. Exceptions cover cessation research, marketing authorisation, occupational health and health promotion. SPE approval needs an explicit statement that statistics are not enough. Every step is traced with the text it matched; weak signals and re-identification risks are flagged for staff. |
| `ehds/spe.py`, `ehds/audit.py` | One DuckDB file per approved SPE application, holding only the cohort and the named record types (e.g. only HbA1c rows for R05). Read-only browsing and SELECT queries work inside it. File and network functions, `COPY` and downloads are refused. Every event is written to an append-only audit log. |
| `ehds/pipeline.py`, `ehds/explain.py` | Orchestration plus a plain-English explanation of every number ("How we counted"). The applicant view leaves out staff-only detail (rule trace, spec, SQL, flags). |
| `ehds/llm.py` | Optional fallback, off by default (see below). |
| `app/` | FastAPI app with plain HTML templates: applicant portal and decision page, staff desk and request detail, SPE workspace. |
| `evaluation/` | The metric harness described above. |

**Fixed rules that apply everywhere.** The reference date is 31 Dec 2025, and age = 2025 − birth year. A
medication is active if its `start_date` ≤ 31 Dec 2025 and its `end_date` is empty or ≥ that date (the
requests' wording; on this data it's equivalent to "end_date empty"). The latest measurement is used per
patient. Opted-out patients are excluded from every number, denominator and SPE copy.

### Models and services

- **No model is needed** for any result above. Decisions, parsing and statistics are deterministic rules and
  SQL: DuckDB 2.0, FastAPI, Jinja2, Pydantic.
- **Optional LLM fallback.** `ehds/llm.py`, using Claude via the Anthropic Python SDK, model `claude-opus-5-5`
  (override with `EHDS_LLM_MODEL`). It's used only when the rule parser leaves something unresolved.
  - `EHDS_LLM=off` (default): rules only.
  - `EHDS_LLM=cache`: also reuse stored interpretations from `llm_cache/`, with no network calls.
  - `EHDS_LLM=on`: call Claude on a cache miss and store the answer.

  Only the request text and the data dictionary (column names, category values, code lists) are sent. The
  model answers through structured outputs in the QuerySpec schema at low effort, and its answer is
  validated like rule output and marked "staff should confirm". Current Claude models don't accept a
  temperature setting, so consistency comes from the schema plus the cache: a request text is interpreted
  once and that interpretation is reused on every later run.

## Limitations

- **Live LLM path not exercised.** The sandbox this was built in has no API access. The cache and fallback
  paths are unit-tested; a live call stops at authentication.
- **Unverified legal sub-points.** EUR-Lex was not reachable from the build environment. Reasons therefore
  cite article numbers (53, 54, 66, 68, 69, 73) with the plain-language ground, but not sub-point letters.
- **Rule-only purpose classification.** It catches the sample cases and the tested variants, but new wording
  can slip past it. Weak signals are flagged to staff rather than decided.
- **Rule parser coverage.** It covers counts, percentages and means (of the latest measurement, age or
  length of stay), grouped by region, hospital, sex, age band, visit type and year. Other groupings or
  statistics (medians, postcodes) are reported as not interpreted and go to staff review, or to the LLM
  fallback if enabled.
- **No secondary suppression.** The small-cell rule is applied exactly as specified. A hidden small count can
  still sometimes be worked out by subtracting two published numbers (e.g. total minus a visible subgroup).
  A production system would add secondary suppression.
- **No output checking in the SPE.** The SPE blocks every download. A real one would also have an output
  checking step for releasing results such as model weights.

## Purpose check

`ehds/decision.py` applies deterministic regex rules in a fixed order. The same input always produces the
same output; no LLM is involved.

**Art. 54 categories covered:**

| # | Category | How it is detected |
|---|---|---|
| 1 | Insurance premiums / contributions | `insurer/insurance/life-insurance` near premium-setting words; standalone `underwriting`, `actuarial`, `policyholders`, `claimants` near pricing; `premium tiers/levels/adjust`; `price` near `insurance` |
| 2 | Employment decisions | `job applicants/candidates`; `candidates to shortlist`; contract renewal, hiring, dismissal, termination (verb and noun); `workers/employees/staff` near `dismiss/retain`; `redundancy` near `select/decision` |
| 3 | Advertising / marketing | Direct `advertis*`; `marketing campaign/strategy`; `sales/promotional campaign`; trigger (`marketing`, `campaign`, `brand`) + context (`product`, `launch`, `physicians`, `consumers`) in the same sentence |
| 4 | Harmful products (tobacco, alcohol, illicit drugs, gambling, weapons) | Substance/product trigger + commercial context (`develop`, `launch`, `product`, `app`, `platform`, `brand`, `design`) in the same sentence |
| 5 | Detrimental decisions against individuals | `deny/refuse/blacklist` near a person group; `exclude … from our/their coverage/services/products/programme`; `decide … to exclude/blacklist/deny` |

**Exceptions that prevent a false positive (fire before pattern-matching):**

- Insurance/credit: `marketing authorisation`, `health promotion`, `cessation`, `quit`, `harms of`, `addiction treatment`, `tobacco control`, `public health`, `epidemiolog*`.
- Employment: `occupational health/disease/safety`.
- Decisions against individuals: `from the analysis/study/cohort/sample`, `excluded due to missing/age/criteria`.

**Test coverage:** `tests/test_purpose_robustness.py` holds **43 prohibited** and **57 allowed** purpose texts
(100 cases total) covering all five Art. 54 categories with varied wording, indirect phrasing, and some
non-native English. Hard cases include innocent-sounding prohibited purposes ("optimise our underwriting
model", "redundancy selections", "job candidates to shortlist", "premium tiers") and alarming-sounding
legitimate ones ("insurance fund cost study", "smoking cessation app", "marketing authorisation",
"occupational health study of employees").

**Known blind spots:**

- Purposes phrased entirely around financial concepts without insurance/credit vocabulary (e.g. "we want to
  assess the fiscal risk of our portfolio using diagnosis codes") may slip past the insurance rule.
- Novel product categories not in the substance list (e.g. a new psychoactive substance by brand name) are
  not caught unless the commercial context words appear.
- Very indirect employment decisions ("help HR understand attendance patterns") are only flagged for staff,
  not rejected outright.
- Art. 54(5) (activities conflicting with national ethical provisions) is not checked by rule; such requests
  would need staff review.

## Data

`data/` and `requests/` are copied unchanged from
[AIM-Austria/track-3-ehds](https://github.com/AIM-Austria/track-3-ehds). All data is synthetic.
