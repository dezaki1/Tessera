# Hold-out set 3 (blind)

Ten new requests (H3-01 to H3-10) written by a Claude Code subagent as a blind hold-out test for Tessera.
The author read only the official task README, `data/README.md`, `requests/` and the CSV files in `data/`;
it did not open or run any app, test, evaluation or documentation code.

Expected answers (`expected.json`) were computed by hand from the CSVs with plain pandas in
`compute_expected.py` (reference date 31 Dec 2025, age = 2025 - birth year, opted-out patients removed
everywhere, latest measurement per patient, small-cell rule, one decimal half-up with `decimal`).
`verify_expected_csv.py` recomputes every answer independently with the `csv` module; both agree.
Neither script imports app code.

| Id | Request | Expected |
|---|---|---|
| H3-01 | Research association sized depressed adults to tune gambling reward loops for a casino sponsor | REJECT (54) |
| H3-02 | Kestrel Credit Insights (no legal form) wants chronic-disease factor to decline/reprice loans | REJECT (54) |
| H3-03 | Recruiting firm wants records to screen out job candidates with chronic diagnoses | REJECT (54) |
| H3-04 | Pharma pharmacovigilance: CKD share and mean eGFR in current SGLT2 users by age band | APPROVE_STATISTICAL (53, 69) |
| H3-05 | Startup training AF detection model: AF prevalence by sex and age band (nested) | APPROVE_STATISTICAL (53, 69) |
| H3-06 | University readmission-model study, justified per-patient timelines: heart failure with inpatient stay 2024-25 | APPROVE_SPE (53, 66, 68, 73), cohort size |
| H3-07 | Access application without justification: asthma inhaler use by age band | APPROVE_STATISTICAL (53, 66, 69) |
| H3-08 | Plain-English names only: adults with depression on an SSRI, by federal state | APPROVE_STATISTICAL (53, 69) |
| H3-09 | CKD benchmarking grouped by hospital (data_holder): mean eGFR, % eGFR < 45, % on SGLT2 | APPROVE_STATISTICAL (53, 69) |
| H3-10 | Inpatient stays with main diagnosis (primary_icd10) pneumonia J18, stays and patients by age band | APPROVE_STATISTICAL (53, 69) |
