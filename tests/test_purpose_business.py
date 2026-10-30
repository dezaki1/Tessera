"""Purpose check on plain business language (independent verifier, round 2).

60 texts written in everyday commercial wording, deliberately not phrased like the
rules in ehds/decision.py, plus the 7 wordings the verifier found approved.
"""

import pytest

from ehds.decision import decide

VERIFIER_FOUND = [
    "We want to know which federal states have the most people with obesity so we can concentrate the online "
    "ads for our new weight-loss shake there.",
    "For marketing our weight-loss shake we need obesity numbers per region.",
    "We will use regional diabetes rates to recalibrate the regional tariffs of our private health cover from "
    "next year.",
    "To set the premiums of our health insurance by region.",
    "The figures help decide which regions our insurance company should stop offering contracts in.",
    "We want to adjust what customers pay for their supplementary health insurance.",
    "Our HR department wants to know which regions to avoid when hiring staff.",
]

PROHIBITED = [
    # advertising / marketing / promotion
    "Our agency plans a social media push for a sleep supplement and wants to know where insomnia is common.",
    "We sell orthopaedic mattresses and want to place billboards where back pain is most frequent.",
    "To target our newsletter about a new allergy spray at the regions with the most asthma.",
    "Our pharmacy chain wants to promote its diabetes testing strips where diabetes is most common.",
    "We are building a lead list of regions for our sales reps who sell blood pressure monitors.",
    "The numbers will guide where we buy TV spots for our cholesterol-lowering margarine.",
    "Our brand wants to sponsor events in the regions with the most heart patients to boost sales.",
    "We need the data to decide in which regions to run discount coupons for our vitamin D drops.",
    "To design an influencer campaign for our fitness app among people with obesity.",
    "We plan direct mail to doctors in regions where our new inhaler would sell best.",
    # insurance / credit / pricing
    "Our health plan will charge members in high-diabetes regions a surcharge.",
    "The bank wants to factor chronic illness rates into mortgage approvals per region.",
    "We price our travel insurance and need to know where heart failure is frequent.",
    "Our company insurance scheme will tighten eligibility in regions with more depression.",
    "We want to set higher deductibles for customers living in regions with many COPD patients.",
    "To decide which applicants for our income protection policy get a medical loading.",
    "Our underwriters need regional cancer rates to refuse cover more often where risk is high.",
    "We will raise the monthly fee of our dental plan in regions with more diabetics.",
    "The leasing company wants to check which regions have more sick people before granting car loans.",
    "We review the renewal terms for policyholders in regions with high stroke rates.",
    # employment
    "Our recruitment team wants to know where depression is common so we can screen applicants harder there.",
    "We decide where to open new branches based on how healthy the local workforce is.",
    "The staffing agency will rank candidates by the chronic disease rate of their home region.",
    "Our managers want to know which employees' regions have high sick leave so they can plan dismissals.",
    "Before promoting team leads, HR wants to check the diabetes prevalence in their region.",
    # harmful products / detrimental decisions
    "We design a new energy drink with extra caffeine for young adults and want to find our market.",
    "Our online casino wants to reach regions where depression is frequent.",
    "We produce flavoured vapes and want to know where young people with asthma live.",
    "The landlord association wants to avoid renting flats to people with chronic illness.",
    "Our gym chain wants to refuse memberships to people with heart conditions in high-risk regions.",
]

ALLOWED = [
    "Our start-up builds software that reminds patients with heart failure to take their medication; we "
    "evaluate it scientifically.",
    "A pharmaceutical company studies the real-world safety of its approved inhaler for the medicines agency.",
    "We are a health economics consultancy commissioned by the ministry to estimate the cost of diabetes care.",
    "A medical device company validates an AI tool that detects atrial fibrillation in ECGs.",
    "The hospital group wants to improve the quality of care for stroke patients across its sites.",
    "We optimise treatment pathways for COPD so that patients are admitted less often.",
    "The regional health authority checks guideline adherence in hypertension treatment.",
    "Our university spin-off develops a prediction model for kidney failure to support nephrologists.",
    "We study how obesity rates differ between regions for a scientific publication.",
    "The nursing school uses the numbers in a course on chronic disease management.",
    "Our insurer's research department publishes a study on the long-term outcomes of diabetes care.",
    "A statutory sickness fund evaluates whether its disease management programme improves HbA1c.",
    "We check whether the prescription of anticoagulants follows guidelines, to reduce strokes.",
    "Researchers compare emergency visits between hospitals to reduce waiting times.",
    "A biotech company tests a new algorithm for early breast cancer detection on historical data.",
    "We assess the burden of asthma in children to plan school health programmes.",
    "We investigate why some regions have more hip fractures, to improve fall prevention.",
    "Our consultancy evaluates the treatment gap for depression on behalf of the health ministry.",
    "The university hospital benchmarks the length of stay for heart failure to optimise care.",
    "A digital health company tests whether its blood pressure app improves control in a clinical study.",
    "We train medical students with anonymised statistics on diabetes complications.",
    "Public health researchers estimate how many people with CKD need dialysis in five years.",
    "The quality team audits whether diabetes patients receive annual HbA1c checks.",
    "We evaluate whether SGLT2 inhibitor uptake differs by region, to update clinical guidelines.",
    "Our company develops a medical device for COPD and needs disease prevalence for its clinical "
    "evaluation report.",
    "We study regional differences in antidepressant treatment to improve mental health services.",
    "The hospital pharmacy wants to optimise anticoagulant treatment for atrial fibrillation.",
    "Health services researchers study readmissions after pneumonia to improve discharge planning.",
]


# Borderline: holding these for staff review is the right outcome, rejecting them is not.
REVIEW_ACCEPTABLE = [
    # planning for patients by a private body: a public-health purpose, reserved for public bodies (Art. 53(2))
    "A patient organisation wants to know how many people live with COPD to plan support groups; results "
    "are published openly.",
    # the text names the ministry, but the applicant is a GmbH: the purpose does not fit the applicant
    "The ministry plans vaccination campaigns and needs the number of older people with COPD.",
]


def r(purpose, organisation="Example Company GmbH"):
    return {"purpose": purpose, "question": "How many patients per region?",
            "requested_as": "health data request", "applicant": {"organisation": organisation}}


def held_or_rejected(d) -> bool:
    return d.decision == "REJECT" or d.hold_for_review


@pytest.mark.parametrize("purpose", VERIFIER_FOUND + PROHIBITED)
def test_business_prohibited_is_never_released(purpose):
    d = decide(r(purpose))
    assert held_or_rejected(d), (d.decision, d.reason)


@pytest.mark.parametrize("purpose", VERIFIER_FOUND)
def test_verifier_wordings_are_rejected_outright(purpose):
    d = decide(r(purpose))
    assert d.decision == "REJECT" and d.articles == ["54"]


@pytest.mark.parametrize("purpose", ALLOWED)
def test_business_allowed_is_released(purpose):
    d = decide(r(purpose, organisation="Example Research GmbH"))
    assert d.decision != "REJECT" and not d.hold_for_review, (d.decision, d.reason, d.staff_flags)


@pytest.mark.parametrize("purpose", REVIEW_ACCEPTABLE)
def test_borderline_purposes_are_never_rejected(purpose):
    assert decide(r(purpose, organisation="Example Research GmbH")).decision != "REJECT"


def test_counts():
    assert len(PROHIBITED) == 30 and len(ALLOWED) + len(REVIEW_ACCEPTABLE) == 30
