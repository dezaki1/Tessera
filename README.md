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

The same script then scores a second set, `examples/jury/`: 8 requests written by an independent reviewer
in the jury's style (grouping by hospital, inpatient stays filtered by `primary_icd10`, both combined,
shares, means, age bands, an SPE application). Their expected answers were computed twice, independently,
with plain pandas (no app code):

| Metric (jury-style set) | Result | Target |
|---|---|---|
| Correct decisions | 8/8 | 8 of 8 |
| Correct numbers | 8/8 | 8 of 8 |
| Privacy leaks | 0 | 0 |
| Consistency (3 runs) | 8/8 | 8 of 8 |
| Slowest time to answer | 0.07 s | under 60 s |

Before the fixes described under "Independent review" below, only 3 of these 8 were fully correct.

A third set, `examples/verifier/`, comes from a second independent verifier that never saw the code: plain-English
terms, stays by `primary_icd10`, a justified SPE (cohort 505), and two prohibited purposes in new wording.
Score: decisions 8/8, numbers 6/6, privacy leaks 0, consistency 8/8. Before the fix in the next section, it
approved the two prohibited ones (6/8).

How the checks work:

- **Correct numbers.** Every expected value must appear under its group, as the README defines it.
- **Privacy leaks.** The script re-runs each query on its own and compares every released cell with the raw
  numbers. A count under 10 that isn't `<10` counts as a leak. So does a percentage or mean resting on fewer
  than 10 patients that isn't `suppressed`, a count or share of stays that come from fewer than 10 patients,
  a group that's missing, a patient ID anywhere in a statistical answer, or an opted-out patient in the
  store or in any SPE copy. `tests/test_evaluation.py` checks that
  these checks catch tampered answers.
- **Consistency.** Compares the full applicant and staff output (everything except the timing) across the
  3 runs.

**Generalisation.** `examples/heldout/` holds 8 extra requests that use things none of the samples use:
grouping by hospital and by hospital × sex, stays filtered by `primary_icd10` (by code and by name), names
without codes (SGLT2 inhibitor, eGFR, pneumonia), an HbA1c threshold, a region filter that appears only in
the question, a different SPE cohort, and alcohol marketing. `tests/test_heldout.py` checks every released
value against an independent pandas implementation. All 8 pass.

**Stress test.** `tests/test_stress.py` sends 39 more requests written the way a juror might write them:
"split by gender", "per Bundesland", per hospital and year, region × hospital × sex, definitions as
"X: ICD-10 I50" or "X means …", "older than 65", "a BMI of 30 or more", mean age and length of stay,
patients with an emergency visit in 2024, hospital filters on stays, requests with no Report line (read
from the question), and commercial but allowed purposes. Each is checked against an independent pandas
calculation. 35 of 39 passed at first; the parser was fixed for the other 4 and all now pass. The purpose
check has its own adversarial set (`tests/test_purpose_robustness.py`, 100 cases, built with IBM Bob, see
`docs/bob/`) plus indirect wordings in `tests/test_decision.py`.

**Audit.** A later audit wrote two more requests from scratch (atrial fibrillation per hospital with a
share and a mean; stays with main diagnosis J18 or N39 by age band with length of stay) and compared them
with plain pandas. Both were silently wrong: a ", and their mean …" merged two numbers into one, and a
diagnosis named only in the question was added as a filter. The parser now splits on ", and", applies
question-only qualifiers automatically only for age, sex, region, hospital and defined concepts (anything
else is listed for staff), lets a population named once apply to every mean, and flags any phrase that
seems to ask for more than one number. Both requests now match pandas exactly and are part of the
tests.

**Independent review.** Four read-only reviewers were then run in parallel, and every finding was
reproduced before it was fixed:

- A **privacy attacker** found that overlapping age bands (18-91 and 18-92) revealed a hidden `<10` count
  by subtraction, and that stays coming from fewer than 10 patients were released (11 stays of 5 people).
  Both are now blocked centrally: the engine refuses overlapping bands, and `disclosure.py` hides stay counts
  and shares behind fewer than 10 patients. Its other attacks failed: file and network access from the SPE
  query box, opted-out patients, patient-level rows and downloads.
- A **jury simulator** wrote the 8 requests above. Three gave wrong numbers with no warning: "women and men"
  emptied every group, an HbA1c threshold was ignored, and an SPE cohort lost its stay condition (3,574
  patients instead of 10). All are fixed. As a safety net, the pipeline now builds no SPE when the cohort and
  the requested count disagree.
- A **legal reviewer** checked reasons against Chapter IV. Reasons now cite the exact point of Art. 53(1)
  or 54 and quote the applicant's sentence on rejection. A private body relying only on Art. 53(1)(a)–(c)
  is flagged (Art. 53(2)). An application announcing re-identification gets no SPE. No reason claims an
  allowed purpose when none was recognised.
- A **demo critic** reviewed the UI and this README. Answers now show "Hospital" and names with codes
  ("breast cancer (ICD-10 C50*)"), the privacy legend sits above the table with a count of protected values,
  the SPE decision explains its cohort, and the staff tables scroll on phones.

`tests/test_review_findings.py` holds a regression test for every finding.

**Second verifier.** A second independent verifier found five more problems, all reproduced and fixed
(`tests/test_verifier_findings.py`):

1. **The purpose check missed plain business wording**, such as "online ads for our weight-loss shake",
   "recalibrate the regional tariffs of our private health cover", or "our HR department … when hiring
   staff". Before the fix, 0 of its 7 wordings and 4 of 30 new prohibited texts (`tests/test_purpose_business.py`)
   were rejected. The rules now match the activity itself (ads, marketing, promotion; premiums, tariffs,
   cover, contracts, "what customers pay"; hiring, HR) in any word order. As a fail-safe, a commercial
   applicant (GmbH, AG, company, insurer…) whose purpose matches no Art. 53(1) purpose open to it gets
   nothing released until staff review it. After the fix: 7/7 and 28/30 rejected, 2/30 held for review,
   none released. Of 30 legitimate business purposes, 28 are released and 2 borderline ones are held
   (none is rejected). "Quality of care", "treatment optimisation" and "guideline adherence" now count as
   Art. 53(1)(f).
2. **Request ids.** A resubmission with a used id could replace an earlier result and its SPE. Now it is
   stored under a new id ("R05-2") and the earlier one is never touched.
3. **An invalid id gave HTTP 500.** Now it returns a clear 422.
4. **Not every refused SPE query was audited.** Every refused SPE query is now written to the audit log,
   including errors raised by DuckDB.
5. **Statistics applicants saw an internal note about the SPE cohort.** That note is gone, and a reason
   no longer says "answered" while the answer is held.

**Review round 3.** Four more read-only reviewers wrote blind cases before reading the code. Every finding
was reproduced before it was fixed (`tests/test_review3.py`), and their cases are now a scored set,
`examples/review3/` (94 requests).

- A **purpose tester** wrote 60 purposes. 15 prohibited ones were released, because the fail-safe only covered
  commercial applicants: a tenants' committee, a football club's payroll, interest rates on loans, a sugar-free
  beer, a pistol grip, loot boxes. 8 legitimate ones were rejected, such as a ministry's public health-promotion
  campaign, insurance coverage research and a nursing staffing study. Now the fail-safe holds **any**
  applicant with no citable Art. 53(1) purpose when the purpose contains a warning word (customers, fees,
  loans, hiring, tenancy…). New activities and products were added, along with narrow exceptions. Public
  insurers (ÖGK, AUVA), Länder and state health offices count as public bodies. Result: 59 of 60 correct; 1
  prohibited text is held for staff instead of rejected.
- A **fail-safe check** sent 25 legitimate requests. None is rejected. "Sponsor", "select trial sites" and "hire
  a statistician" no longer trigger the rules. "Aggregates will not give us…" is recognised as a reason for an
  SPE. A description in brackets ("PhD student, no company affiliation") no longer makes an applicant
  commercial.
- An **API tester** found 500 errors on malformed input, a race between two submissions with the same id, no
  size limits, and SPE queries without a timeout. Now malformed requests get 422, oversized bodies get 413,
  requests are processed one at a time, and SPE queries stop after 10 s. Functions that expose the server
  (`duckdb_*`) or pack many rows into one value (`string_agg`, `list`) are blocked. Shown values are capped at
  200 characters. Every refusal is logged with a reason.
- A **privacy attacker** ran 26 attacks and found no hard-rule break: no small count, unsuppressed mean or
  percentage, patient ID or opted-out patient was released. It found three wrong answers released as
  `answered`, which are now held for staff:
  - a share of a total across regions came out as 100% per region;
  - a second Report line was dropped;
  - a main-diagnosis filter in a counting rule was dropped.

  "Patients who have opted out" used to be answered with the eligible population. It now goes to staff
  review. Secondary disclosure (below) remains a known limitation.

**Blind hold-out set.** `examples/holdout3/` (10 requests with pandas answers) was written at the start of
round 3 and nobody read or ran it until the final scoring, after all fixes. The table shows its blind score.
After that run, one gap it exposed was fixed because it could copy the wrong patients into an SPE: a stay
period given as exact dates (H3-06). With that fix it scores numbers 4/7. Its other misses are not fixed and
are listed under Limitations.

| Metric | Official (`requests/`) | Jury-style (`examples/jury/`) | Verifier (`examples/verifier/`) | Review 3 (`examples/review3/`) | Hold-out 3 (`examples/holdout3/`) |
|---|---|---|---|---|---|
| Correct decisions | 10/10 | 8/8 | 8/8 | 93/94 | 9/10 |
| Correct numbers | 6/6 | 8/8 | 6/6 | 4/4 | 3/7 blind (4/7 after the H3-06 fix) |
| Privacy leaks | 0 | 0 | 0 | 0 | 0 |
| Consistency (3 runs) | 10/10 | 8/8 | 8/8 | 94/94 | 10/10 |
| Slowest time to answer | 0.09 s | 0.06 s | 0.08 s | 0.15 s | 0.10 s |

```bash
python -m evaluation.run_eval --sets official,jury,verifier,review3,holdout3
```

In total 508 tests pass.

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
- **No login.** This is a demo. The staff desk and SPE workspaces are open to anyone who has the URL. A real
  access body would authenticate staff and tie each SPE to its permit holder.
- **Public-body and commercial checks are heuristics.** Art. 53(2) reserves points (a)–(c) for public sector
  bodies. The app guesses from the organisation name (and phrases like "commissioned by the ministry"). A
  commercial applicant with no matching purpose is held for staff review; nobody is rejected on that basis.
- **Purpose wording still matters.** The fail-safe now covers every applicant, but only when the purpose has
  a warning word or the applicant looks commercial. A prohibited purpose in wording the rules don't know, with
  no warning word, is still approved. Hold-out request H3-01 shows this: an association asked, for an online-casino
  sponsor, how many people with depression there are in order to maximise play time and in-app spend in betting
  mechanics. It was approved with numbers released, because the rules know "betting" only next to words like
  "develop" or "customers", not "tuning" or "release".
- **Rule-only purpose classification.** It catches the sample cases and the tested variants, but new wording
  can slip past it. Weak signals are flagged to staff rather than decided.
- **Rule parser coverage.** It covers counts, percentages and means (of the latest measurement, age or
  length of stay), grouped by region, hospital, sex, age band, visit type and year. Other groupings or
  statistics (medians, postcodes) are reported as not interpreted and go to staff review, or to the LLM
  fallback if enabled.
- **No secondary suppression.** Overlapping age bands are refused, but other overlaps are not. One answer
  can reveal a hidden cell: "patients, women and men with COPD" per region gives 12 / `<10` / 10, so the
  hidden value is 2; complementary shares by sex work the same way. Across requests, hidden region counts can
  be recovered exactly, and two means over cohorts that differ by one patient (age bands 18–79 and 18–81)
  reveal that patient's BMI. Each released value follows the small-cell rule, but a production system needs
  complementary suppression within an answer and tracking of what each applicant has already received.
- **No output checking in the SPE.** The SPE blocks every download and caps what a query shows (200 rows, 200
  characters per value, 10 s). Anyone with the SPE link can still page through the pseudonymised rows with
  OFFSET. A real SPE would tie access to the permit holder and add an output checking step for results
  such as model weights.
- **Parser gaps found by the hold-out set (not fixed, reported as found).**
  - H3-06 (fixed after the blind run): an SPE whose stays had a date range ("between 1 January 2024 and
    31 December 2025") ignored the range, so the cohort had 248 patients instead of 190. Exact date ranges are
    now applied as periods. As a safety net, no SPE is built when any part of the request was not understood.
    Tests use other requests and check the cohort size against pandas.
  - H3-09: "eGFR below 45 = latest eGFR value strictly below 45" was not applied, so the share was 100%.
  - H3-04: the definition "Currently on = prescription active on 31 December 2025…" was not understood. The
    SGLT2 filter was dropped and the numbers were released with a warning.
  - H3-10: "Pneumonia as main diagnosis = primary_icd10 of the stay starting with J18" and a 2023–2025 period
    were not applied. The numbers were released with a warning.

  None of these released a cell that breaks the small-cell rule, but the numbers are wrong.

## How we used IBM Bob

IBM Bob (the event's required AI development partner) did one self-contained task in this project: making
the Art. 54 purpose check robust to new wording. The task brief is in `docs/bob/TASK_BRIEF.md` and the session
report in `docs/bob/session_report_purpose_check.html`. Bob:

1. wrote `tests/test_purpose_robustness.py` first: 43 prohibited and 57 allowed purposes, with indirect and
   non-native wording and look-alikes (study exclusion criteria, cessation apps, insurance cost studies);
2. measured the baseline: 16 of the 100 failed, all of them prohibited purposes that were approved;
3. extended the rules in `ehds/decision.py` until all 100 passed, adding an allowed look-alike test for
   every rule it loosened, while keeping every existing test and the evaluation metrics unchanged;
4. wrote the "Purpose check" section below.

The rest of the app (data store, parser, engine, disclosure, SPE, UI and evaluation) was built with Claude
Code. Later reviews found three more prohibited wordings that slipped past, and they were fixed the same
way (`tests/test_decision.py`).

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
