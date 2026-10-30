import json
from pathlib import Path

import pytest

from ehds.parser import RuleParser
from ehds.terminology import build_terminology

REQUESTS = Path(__file__).resolve().parent.parent / "requests"


@pytest.fixture(scope="module")
def parser(store):
    return RuleParser(build_terminology(store.vocabulary))


def req(definitions, question="", requested_as="health data request", **extra):
    return {"id": "T", "requested_as": requested_as, "question": question,
            "purpose": "research", "definitions": definitions, **extra}


def crit(metric, kind):
    return [c for c in metric.criteria if c.kind == kind]


@pytest.mark.parametrize("rid", [f"R{i:02d}" for i in range(1, 11) if i != 6])
def test_sample_requests_parse_completely(parser, rid):
    parsed = parser.parse(json.loads((REQUESTS / f"{rid}.json").read_text()))
    assert parsed.unresolved == []
    assert parsed.statistics is not None


def test_patient_level_report_is_flagged(parser):
    parsed = parser.parse(json.loads((REQUESTS / "R06.json").read_text()))
    assert parsed.statistics is None
    assert parsed.patient_level_output


def test_cohort_and_record_types_for_spe(parser):
    parsed = parser.parse(json.loads((REQUESTS / "R05.json").read_text()))
    tables = {r.table: r for r in parsed.cohort.records}
    assert set(tables) == {"conditions", "medications", "observations"}
    assert tables["observations"].loinc == ["4548-4"]
    kinds = {c.kind for c in parsed.cohort.criteria}
    assert kinds == {"age", "condition", "observation"}


def test_plain_english_names_without_codes(parser):
    parsed = parser.parse(req(
        ["Report per hospital: adults with type 2 diabetes, how many of them take an SGLT2 inhibitor and that share in %."],
        question="How many adults with type 2 diabetes take an SGLT2 inhibitor, per hospital?"))
    spec = parsed.statistics
    assert [d.kind for d in spec.dimensions] == ["data_holder"]
    base, treated, pct = spec.metrics
    assert crit(base, "condition")[0].icd10_prefixes == ["E11"]
    med = crit(treated, "medication")[0]
    assert med.atc_prefixes == ["A10BK"] and med.active and not med.negate
    assert pct.numerator == treated.name and pct.denominator == base.name


def test_inpatient_stays_by_primary_diagnosis_and_hospital(parser):
    parsed = parser.parse(req(
        ["Inpatient stay = encounter with type 'inpatient'.",
         "Count stays (not patients) whose start_date is in 2024.",
         "Report per hospital: inpatient stays with a main diagnosis (primary_icd10) starting with S72."],
        question="How many inpatient stays for a femur fracture started in 2024, by hospital?"))
    (metric,) = parsed.statistics.metrics
    assert metric.unit == "encounter"
    (stay,) = crit(metric, "encounter")
    assert stay.filter.types == ["inpatient"]
    assert stay.filter.primary_icd10_prefixes == ["S72"]
    assert (stay.filter.date_from.isoformat(), stay.filter.date_to.isoformat()) == ("2024-01-01", "2024-12-31")
    assert not crit(metric, "condition"), "a main diagnosis must not be read as a conditions.csv row"
    assert [d.kind for d in parsed.statistics.dimensions] == ["data_holder"]


def test_main_diagnosis_by_name(parser):
    parsed = parser.parse(req(["Report per region: inpatient stays with a main diagnosis of pneumonia in 2025."]))
    (metric,) = parsed.statistics.metrics
    (stay,) = crit(metric, "encounter")
    assert stay.filter.primary_icd10_prefixes[-1] == "J18"
    assert stay.filter.date_from.year == 2025


def test_patients_with_a_stay(parser):
    parsed = parser.parse(req(["Report per sex: patients with heart failure who had an emergency visit in 2025."]))
    (metric,) = parsed.statistics.metrics
    assert metric.unit == "patient"
    (visit,) = crit(metric, "has_encounter")
    assert visit.filter.types == ["emergency"] and visit.filter.date_from.year == 2025
    assert crit(metric, "condition")[0].icd10_prefixes == ["I50"]


def test_or_merges_codes(parser):
    parsed = parser.parse(req(
        ["Cardiovascular disease = any condition with an ICD-10 code starting with I48 or I50.",
         "Report per region: patients with cardiovascular disease, patients with asthma or COPD."]))
    a, b = parsed.statistics.metrics
    assert crit(a, "condition")[0].icd10_prefixes == ["I48", "I50"]
    assert crit(b, "condition")[0].icd10_prefixes == ["J45", "J44"]


def test_threshold_on_latest_measurement(parser):
    parsed = parser.parse(req(
        ["Report per age band: adults with type 2 diabetes, how many of them have a latest HbA1c above 8% and that share in %."],
        question="by 10-year age band"))
    _, high, _ = parsed.statistics.metrics
    (obs,) = crit(high, "observation")
    assert obs.loinc == "4548-4" and obs.comparator == ">" and str(obs.threshold) == "8"
    assert parsed.statistics.dimensions[0].bands[0].label == "0-9"


def test_negation_and_region_names(parser):
    parsed = parser.parse(req(["Report per sex: adults in Vienna without hypertension."]))
    (metric,) = parsed.statistics.metrics
    assert crit(metric, "region")[0].values == ["Wien"]
    assert crit(metric, "condition")[0].negate


def test_question_restriction_is_applied_and_recorded(parser):
    parsed = parser.parse(json.loads((REQUESTS / "R08.json").read_text()))
    for metric in parsed.statistics.metrics:
        assert crit(metric, "condition")[0].icd10_prefixes == ["C50"]
    assert any("breast cancer" in a for a in parsed.assumptions)


def test_mean_age_and_length_of_stay(parser):
    parsed = parser.parse(req(["Report per hospital: inpatient stays and mean length of stay.",
                               "Count stays (not patients) whose start_date is in 2025."]))
    stays, los = parsed.statistics.metrics
    assert los.kind == "mean" and los.value.source == "length_of_stay" and los.unit == "encounter"
    parsed = parser.parse(req(["Report per region: patients with COPD and their mean age."]))
    assert parsed.statistics.metrics[1].value.source == "age"


def test_unsupported_things_are_reported_not_guessed(parser):
    parsed = parser.parse(req(["Report per postcode: patients with asthma."]))
    assert parsed.statistics is None and "postcode" in " ".join(parsed.unresolved)
    parsed = parser.parse(req(["Report per region: median BMI of adults."]))
    assert parsed.statistics is None
    parsed = parser.parse(req(["Patients are weighted by inverse probability.", "Report per region: adults."]))
    assert any("weighted" in u for u in parsed.unresolved)


def test_drug_activity_default_is_recorded(parser):
    parsed = parser.parse(req(["Report per region: adults prescribed a statin."]))
    med = crit(parsed.statistics.metrics[0], "medication")[0]
    assert not med.active
    assert any("statin" in a and "ever prescribed" in a for a in parsed.assumptions)


def test_request_to_include_opted_out_is_flagged(parser):
    parsed = parser.parse(req(["Report per region: patients with COPD, including opted-out patients."]))
    assert any("Art. 71" in u for u in parsed.unresolved)


def test_blood_pressure_is_a_measurement_and_high_blood_pressure_a_condition(parser):
    parsed = parser.parse(req(["Report per region: patients with high blood pressure and their mean blood pressure."]))
    count, mean = parsed.statistics.metrics
    assert crit(count, "condition")[0].icd10_prefixes[0] == "I10"
    assert mean.value.loinc == "8480-6"


def test_comma_and_separates_numbers(parser):
    parsed = parser.parse(req(["Report per region: adults with asthma, the share of them with an active "
                               "inhaler in %, and their mean BMI."]))
    kinds = [m.kind for m in parsed.statistics.metrics if m.visible]
    assert kinds == ["count", "percentage", "mean"]


def test_condition_only_in_question_is_flagged_not_guessed(parser):
    parsed = parser.parse(req(["Report per region: adults."], question="How many adults with asthma live in each region?"))
    (metric,) = parsed.statistics.metrics
    assert not crit(metric, "condition"), "a diagnosis named only in the question must not be added silently"
    assert any("asthma" in u and "not applied" in u for u in parsed.unresolved)


def test_defined_concept_from_question_is_still_applied(parser):
    parsed = parser.parse(json.loads((REQUESTS / "R08.json").read_text()))  # breast cancer is defined
    assert parsed.unresolved == []
    assert crit(parsed.statistics.metrics[0], "condition")[0].icd10_prefixes == ["C50"]


def test_population_named_once_applies_to_every_mean(parser):
    parsed = parser.parse(req(["Report per region: mean BMI and mean HbA1c of adults with diabetes."]))
    bmi, hba1c = parsed.statistics.metrics
    assert bmi.value.loinc == "39156-5" and hba1c.value.loinc == "4548-4"
    for metric in (bmi, hba1c):
        assert crit(metric, "age")[0].min == 18 and crit(metric, "condition")
    assert parsed.unresolved == []


def test_mean_without_any_population_is_flagged(parser):
    parsed = parser.parse(req(["Report per region: mean BMI."]))
    assert parsed.statistics is None and any("not stated" in u for u in parsed.unresolved)


def test_phrase_asking_for_two_numbers_is_flagged(parser):
    parsed = parser.parse(req(["Report per region: adults with asthma and their mean BMI or average HbA1c."]))
    assert any("more than one number" in u for u in parsed.unresolved)
