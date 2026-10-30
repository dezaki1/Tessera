# Independent verifier requests (N01-N08)

Written by an independent verifier who did not see the app's code. Expected answers were computed
with plain pandas from the official data (opted-out patients removed, age = 2025 minus birth year,
active medication = empty end_date, small-cell rule and half-up rounding applied by hand).

- N01: plain-English terms only (heart failure, beta blocker), grouped by hospital.
- N02: inpatient stays filtered by primary_icd10 (K35) in 2024, per region, with a mean age. Several suppressed cells.
- N03: plain-English measurement (kidney function = eGFR), latest value, threshold, by sex.
- N04: access application without a justification for records -> statistics, Art. 53, 66, 69.
- N05: advertising for a weight-loss shake ("online ads") -> REJECT, Art. 54(c).
- N06: insurer recalibrating regional tariffs of private health cover -> REJECT, Art. 54(b).
- N07: justified SPE application, plain-English sertraline, cohort of 505.
- N08: emergency visits per hospital and year.

At verification time (commit b25eb4b) the app got N01-N04, N07 and N08 right and approved N05 and N06.

Added to the evaluation (`python -m evaluation.run_eval`) as the third set. The verifier's expected answers
for N01-N04, N07 and N08 agree with the app; N05 and N06 (prohibited purposes in new wording) were approved
before the fix and are now rejected under Art. 54(c) and 54(b).
