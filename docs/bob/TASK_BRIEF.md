# Bob task: make the purpose check robust to new wording

Paste everything below the line into IBM Bob, with the Tessera repository open.

---

You are working on Tessera, an automated Health Data Access Body for the European Health Data Space
(Regulation (EU) 2025/327). Read `README.md` first, then `ehds/decision.py` and `tests/test_decision.py`.

## Goal

`ehds/decision.py` decides whether a request has a prohibited purpose under Art. 54 (→ REJECT). It uses
regex rules: a trigger plus a context, with exceptions. It handles the 10 sample requests, but the jury
will test new requests in new wording. The worst possible failure is approving a prohibited purpose. The
second worst is rejecting a legitimate research request. Make the check robust against both.

Art. 54 prohibits using health data for:
1. decisions that harm a person or group (legal, social or economic effects);
2. decisions about job offers or employment, less favourable terms for goods or services, excluding people
   from insurance or credit, or changing their premiums, contributions or loan conditions;
3. advertising or marketing;
4. developing products or services that harm individuals, public health or society, e.g. tobacco and
   nicotine products, alcohol, illicit drugs, weapons, addictive products;
5. activities that conflict with ethical provisions of national law.

Allowed purposes (Art. 53) include public health, policy-making, official statistics, education, scientific
research (including developing products and training algorithms that benefit health), and improving care.
Commercial applicants are allowed when the purpose is allowed.

## Steps

1. **Write the adversarial test set first.** Create `tests/test_purpose_robustness.py` with at least
   **40 prohibited** and **40 allowed** purpose texts in realistic applicant language. Vary the wording,
   use indirect phrasing, and add a few that are non-native English. Cover every Art. 54 category.
   Include hard cases such as:
   - prohibited but innocent-sounding: "optimise our underwriting model", "segment customers for our
     new loyalty programme", "help our client decide which job candidates to shortlist", "find regions
     where our energy drink sells best", "design a more engaging betting app";
   - allowed but alarming-sounding: "insurance fund study of the cost of diabetes care", "smoking
     cessation trial", "research on harms of alcohol", "pharma company preparing a marketing
     authorisation", "occupational health study", "health promotion campaign by a ministry",
     "AI start-up training a sepsis early-warning model".

   Use `decide({"purpose": ..., "question": "How many patients ...?", "requested_as": "health data request"})`.
2. **Run the tests and record the failures** before changing anything. Put the failure count in your summary.
3. **Improve `ehds/decision.py`** until all of them pass:
   - extend the `PROHIBITED` rules (patterns, trigger/context pairs, exceptions) and `ALLOWED_PURPOSES`;
   - keep the rules explicit and deterministic: no LLM and no randomness; the same input must give the same
     output;
   - every rejection must keep a plain-English `explanation` that cites Art. 54 and names the category;
   - if a text is genuinely ambiguous, keep the decision but add a clear `staff_flags` entry instead of
     guessing.
4. **Do not change the decision order, the article lists or the reason texts for the 10 sample requests.**
   `tests/test_decision.py` must still pass unchanged.
5. **Run the full checks:**
   - `python -m pytest -q`: everything passes.
   - `python -m evaluation.run_eval`: still decisions 10/10, numbers 6/6, privacy leaks 0,
     consistency 10/10.
6. Add a short section "Purpose check" to `README.md`: the categories covered, the number of test
   cases, and the known blind spots that remain.

## Constraints

- Only touch `ehds/decision.py`, the new test file, and `README.md`.
- Do not hard-code the sample requests or `requests/expected.json` into the rules.
- Keep the code style of the file: regexes in the rule tables, short comments.

## Done when

- At least 80 new test cases pass, plus all existing tests.
- The evaluation metrics are unchanged.
- Commit with a clear message and push.

Afterwards, export the Bob task session report to `docs/bob/` and commit it.
