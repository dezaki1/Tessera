"""Adversarial robustness tests for the purpose-check rules in ehds/decision.py.

40+ prohibited-purpose texts and 40+ allowed-purpose texts, covering all five
Art. 54 categories with varied wording, indirect phrasing, and some non-native
English.  Hard cases are annotated.
"""
import pytest

from ehds.decision import decide


def r(purpose):
    return {
        "purpose": purpose,
        "question": "How many patients had a hospital stay in 2024?",
        "requested_as": "health data request",
    }


# ---------------------------------------------------------------------------
# Prohibited purposes (must REJECT with articles == ["54"])
# ---------------------------------------------------------------------------

PROHIBITED = [
    # --- Art. 54(1/2): insurance / credit ---
    "We want to optimise our underwriting model using patients' diagnoses to set more accurate risk-based premiums.",
    "Our insurer needs to identify which conditions raise the risk profile of customers so we can adjust their premiums accordingly.",
    "Develop a credit-scoring algorithm that incorporates chronic illness diagnoses for loan approval decisions.",
    "The bank wishes to assess creditworthiness of applicants by factoring in their diabetes or hypertension records.",
    "We need to assess loan conditions for applicants; health data will improve our risk model.",
    "We want to price our life-insurance products using real-world morbidity patterns.",
    "Identify customers with expensive chronic diseases so we can adjust their policy pricing.",
    "We want to find which patients with pre-existing conditions should be excluded from our insurance products.",

    # --- Art. 54(2): employment decisions ---
    "Help our client decide which job candidates to shortlist by flagging those with high sickness-absence patterns.",
    "Our HR department wants to review the health data of our staff before renewing their contracts.",
    "Screen job applicants for mental health conditions before we make hiring decisions.",
    "We want to know how often employees call in sick before we promote them or dismiss them.",
    "Flag which of our employees have chronic conditions that could affect their work before contract renewals.",
    "We need data on employees with long-term sick leave so our manager can decide on termination of employment.",
    "Select which workers to retain or dismiss based on their attendance and medical history.",

    # --- Art. 54(3): advertising / marketing ---
    "Find regions where our energy drink sells best so we can focus our marketing campaign there.",
    "We want to segment customers for our new loyalty programme and tailor targeted advertising.",
    "Identify high-prescription areas so our sales team can run a promotional campaign for our product.",
    "Use health data to target our advertising to physicians who treat a large volume of hypertensive patients.",
    "Build a marketing strategy for our new supplement range using morbidity data.",
    "Design a sales campaign targeting consumers with diabetes who may benefit from our product line.",
    "Identify which ZIP codes have high rates of obesity so we can target them with advertising.",
    "We want to do a promotional campaign to grow the market for our brand.",

    # --- Art. 54(4): harmful products ---
    "Optimise our nicotine pouch products by understanding what flavours appeal to young adults.",
    "We are developing a new tobacco product line and want to know where current smokers live.",
    "We want to identify the healthier alternative among our e-cigarette variants and launch it to consumers.",
    "Design a more engaging betting app by understanding which user groups visit emergency care after gambling.",
    "Our spirits brand wants to segment regions by alcohol-related hospital admissions to position our products.",
    "Develop new beer flavours for consumers with metabolic conditions — we want to know their diet.",
    "We want to launch a vaping product range and need to know the demographics of current e-cigarette users.",
    "Our weapons manufacturer wants to identify PTSD rates in veterans to develop a targeted wellness product.",
    "Identify regions with high illicit drug use so we can launch our harm-reduction product line there.",
    "We are developing a new gambling platform and want data on addiction-prone demographics.",

    # --- Art. 54(1): detrimental decisions against individuals / groups ---
    "We want to exclude patients with comorbidities from coverage based on their diagnosis records.",
    "Deny access to our services to individuals who have a history of psychiatric hospitalisation.",
    "Refuse coverage to people with chronic conditions identified from their health records.",
    "Identify and blacklist customers based on their mental health history.",
    "We want to decide which individuals to exclude from the programme because of prior conditions.",

    # --- Subtle / indirect wording ---
    "Refine our actuarial tables so that policyholders with expensive conditions pay their fair share.",
    "We need to know the prevalence of hypertension so we can tailor premium tiers for our health plan.",
    "Build a propensity model to identify patients likely to become high-cost claimants and price accordingly.",
    "Understand the disease burden of the workforce to help our client decide on redundancy selections.",
    "We want to know which customers' diagnoses signal high future costs so we can review their loan terms.",
]


@pytest.mark.parametrize("purpose", PROHIBITED)
def test_prohibited_purpose_is_rejected(purpose):
    d = decide(r(purpose))
    assert d.decision == "REJECT", (
        f"Expected REJECT but got {d.decision!r}.\nPurpose: {purpose!r}"
    )
    assert d.articles == ["54"], f"Expected articles ['54'] but got {d.articles!r}.\nPurpose: {purpose!r}"


# ---------------------------------------------------------------------------
# Allowed purposes (must NOT REJECT — any APPROVE_* is acceptable)
# ---------------------------------------------------------------------------

ALLOWED = [
    # --- Insurance / actuarial research — legitimate (guards for loosened insurance patterns) ---
    "Insurance fund study of the cost of diabetes care for public health planning.",
    "The national health insurance fund wants to estimate the annual cost of cardiovascular treatments for policy purposes.",
    "A social insurance body wishes to produce official statistics on medication costs for the ministry.",
    # public insurance planning — has 'insurance' but no premium-setting signal
    "A statutory health insurance body needs data on hospital readmissions to plan reimbursement policy.",
    # 'premium' in clinical sense (premium-priced medicine) — no insurance subject
    "We assess whether premium-priced biologics deliver better outcomes than standard therapy.",
    # actuarial science research — no pricing/adjustment target
    "Academic study of actuarial methods for mortality forecasting, for publication.",

    # --- Tobacco / alcohol / nicotine — research, not product promotion ---
    "Smoking cessation trial: we assess whether varenicline improves quit rates in COPD patients.",
    "Research on the harms of alcohol to liver function, to inform clinical prevention guidelines.",
    "We study the effectiveness of nicotine replacement therapy in pregnant women.",
    "An academic group investigates long-term respiratory damage from tobacco smoking to guide health policy.",
    "We are studying the epidemiology of alcohol-related hospitalisations to inform the national prevention strategy.",
    "Research on the harms of illicit drug use for developing addiction treatment guidelines.",
    # smoking-cessation app — 'app' is now in context list; exception must fire
    "We are developing a smoking cessation app to help people quit, using population health data.",
    # alcohol reduction — 'app'/'platform' in context but exception fires on 'cessation'/'harms of'
    "An alcohol reduction programme: we study the harms of alcohol to design better support services.",
    # gambling addiction treatment — 'platform' in context but exception fires on 'addiction treatment'
    "A non-profit develops a gambling addiction treatment platform to help users recover.",

    # --- Marketing authorisation ---
    "Pharma company preparing a marketing authorisation dossier for a new anticoagulant.",
    "We need real-world evidence for the marketing authorisation of a biosimilar insulin.",
    "Clinical evidence for a marketing authorisation application for a rare-disease medicine.",

    # --- Occupational health ---
    "Occupational health study of nurses exposed to shift work and cardiovascular risk.",
    "Research on occupational disease in construction workers to improve workplace safety.",
    "Occupational health surveillance of chemical plant employees for regulatory compliance.",
    # 'employees' appears — occupational-health exception must fire
    "Occupational health survey of hospital employees to assess burnout and improve working conditions.",
    # 'redundancy' in data-quality context — no employment-decision signal
    "We assess data redundancy in hospital records to improve data quality for research purposes.",
    # 'dismiss' in a clinical context — no employment subjects
    "Clinicians often dismiss initial symptoms; we study diagnostic delays in cancer care.",

    # --- Health promotion / public campaigns ---
    "Health promotion campaign by a ministry to reduce childhood obesity rates.",
    "A public health authority wants to design a health promotion programme targeting high-risk regions.",
    "National campaign to promote health: we need statistics on preventable hospitalisations.",

    # --- AI / algorithm training for health ---
    "AI start-up training a sepsis early-warning model on ICU trajectories.",
    "We are training a machine-learning algorithm to predict readmission risk for heart-failure patients.",
    "Develop and validate an AI classifier to support radiologists in detecting pulmonary nodules.",
    "Training a prediction model for diabetic nephropathy progression — model benefits patient care.",

    # --- Scientific research broadly ---
    "Epidemiological study of hypertension risk factors in the Austrian adult population.",
    "Academic cohort study on long-term outcomes of bariatric surgery.",
    "We are investigating the association between socioeconomic status and type 2 diabetes incidence.",
    "A university research group is analysing risk factors for stroke in patients over 65.",
    "Multi-centre study on treatment gaps in mental health care across regions.",

    # --- Policy / planning ---
    "The Ministry of Health needs statistics on hospital capacity utilisation for national health planning.",
    "Regional health authorities need data for planning future primary care services.",
    "Health technology assessment of a new diabetes drug for reimbursement decisions.",

    # --- Education ---
    "Training of medical students using anonymised case patterns.",
    "Curriculum development for a post-graduate nursing programme using aggregate morbidity data.",

    # --- Improving care ---
    "We want to improve the care of chronic kidney disease patients by identifying treatment gaps.",
    "Patient safety initiative to reduce post-surgical infections in orthopaedic departments.",
    "Improving treatment delivery for patients with heart failure across Austrian hospitals.",
    # study exclusion criteria — not service exclusion; exception must fire
    "We exclude patients younger than 18 from the analysis; the study covers adult diabetes only.",
    "Patients with missing data were excluded from the study cohort.",
    # 'loan' in non-financial context
    "This research is conducted under a data loan agreement with the national registry.",

    # --- Hard / alarming-sounding but clearly allowed ---
    "Our pharmaceutical company is conducting post-marketing surveillance for a cancer therapy.",
    "A non-profit is studying gambling addiction to design more effective addiction treatment programmes.",
    "We study the prevalence of depression and anxiety among healthcare workers to improve occupational health services.",
    "Research on electronic cigarette use patterns to inform tobacco control policies.",
    "Studying the health effects of alcohol consumption to support a national prevention programme.",
    "We assess whether people living near industrial sites have higher rates of respiratory disease (public health).",
    "Evaluate the burden of nicotine dependence in the population to guide cessation support services.",

    # --- Non-native English phrasings ---
    "We make study about how many peoples have diabetes in different regions for the statistics office.",
    "Our company want to know the rate of hospitalisations for heart disease in purpose of improve care.",
    "Research for understand better the risk factors which lead to cancer, for scientific publication.",
]


@pytest.mark.parametrize("purpose", ALLOWED)
def test_allowed_purpose_is_not_rejected(purpose):
    d = decide(r(purpose))
    assert d.decision != "REJECT", (
        f"Expected APPROVE_* but got REJECT.\nPurpose: {purpose!r}\nReason: {d.reason!r}"
    )
