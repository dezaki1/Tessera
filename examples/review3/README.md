# Review round 3 cases

Blind cases from the four round-3 reviewers, written before they read the app:

- `RP-*`: 60 purposes from the purpose tester (30 prohibited, 30 legitimate), each with the same question.
- `RF-*`: 25 legitimate requests from the fail-safe check (2 access applications that justify an SPE).
- `RV-*`: 9 requests from the privacy attacker (opt-outs, complementary cells, wordings that were answered wrongly).

`expected.json` holds the reviewers' expected decisions. The numbers for RV-B1, RV-B3a, RV-B3b and RV-B5 come
from plain pandas in `make_review3.py`, which uses no app code. A held request counts as an approval with no
numbers released. One known shortfall is kept as is: RP-P1-15 is held for staff, not rejected.
