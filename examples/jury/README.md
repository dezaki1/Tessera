# Jury-style requests (independent review)

Eight requests written by an independent reviewer in the style of `requests/`, covering grouping by
hospital (`data_holder`), inpatient stays filtered by main diagnosis (`primary_icd10`), both combined,
percentages, means (latest measurement, length of stay), age bands, counting patients with a stay,
and one access application (J07, SPE).

`expected.json` was computed twice, independently, with plain pandas reading `data/*.csv` (no app code):
once by the reviewer and once more before it was added here; both agree.

`python -m evaluation.run_eval` scores this set after the official one.
