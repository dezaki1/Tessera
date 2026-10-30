"""Decision rules for a request, applied in a fixed order:

1. Prohibited purpose (Art. 54)                                   -> REJECT, Art. 54
2. Health data request (statistics)                               -> APPROVE_STATISTICAL, Art. 53, 69
3. Access application explaining why statistics are not enough   -> APPROVE_SPE, Art. 53, 66, 68, 73
4. Any other access application                                   -> APPROVE_STATISTICAL, Art. 53, 66, 69

Each rule returns the text it matched, so applicants and staff can see why.
Prohibited-purpose rules need a trigger *and* a context (e.g. "nicotine"
together with developing or positioning a product), so research about a harm
is not mistaken for promoting it. Weaker signals are only flagged for staff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

I = re.IGNORECASE

ARTICLE_TITLES = {
    "53": "Purposes for which health data can be processed for secondary use",
    "54": "Prohibited secondary use",
    "66": "Data minimisation and purpose limitation (anonymised data by default; pseudonymised only if justified)",
    "68": "Data permit",
    "69": "Health data request (anonymised statistical answer)",
    "71": "Right to opt out",
    "73": "Secure processing environment",
}


@dataclass(frozen=True)
class ProhibitedRule:
    category: str
    explanation: str
    patterns: tuple[str, ...]  # any of these alone is enough
    trigger: str | None = None  # or: trigger ...
    context: str | None = None  # ... together with context
    exceptions: str | None = None  # text that cancels the match (e.g. "health promotion")
    # Activity rules: every regex of a group must occur in the same sentence, in any order.
    activities: tuple[tuple[str, ...], ...] = ()
    activity_exceptions: str | None = None  # cancels an activity match in that sentence


PROHIBITED = [
    ProhibitedRule(
        "insurance or credit",
        "Using health data to change people's insurance premiums or contributions, or to exclude them from "
        "insurance or credit or change their loan conditions, is prohibited (Art. 54(b)).",
        patterns=(
            # insurer/insurance near any premium-setting or underwriting signal (extended window)
            r"\b(insurance|insurer'?s?|life[- ]insurance)\b[^.]{0,150}"
            r"\b(premiums?|pricing|underwrit\w*|tariffs?|risk[- ]based|actual risk|policy pricing|"
            r"premium tiers?|premium levels?|contributions?)\b",
            # price/pricing near insurance/life-insurance in either order within a sentence
            r"\bpric\w+\b[^.]{0,80}\b(insurance|life[- ]insurance|insurer)\b",
            # underwriting / actuarial / policyholders near pricing decisions
            r"\b(underwrit\w*|actuarial|policyholders?|claimants?)\b[^.]{0,100}"
            r"\b(model|tables?|premiums?|pricing|tiers?|pay\b|adjust|price)\b",
            # standalone premium tiers / policy pricing signals
            r"\bpremium (tiers?|levels?|adjust\w*|pricing)\b",
            r"\bpolicy pricing\b",
            # exclude … from (our) insurance / coverage / products
            r"\bexclud\w+\b[^.]{0,60}\bfrom\b[^.]{0,40}\b((?:our |their |the )?(?:insurance|coverage|benefits?|products?))\b",
            # credit, loan and financial eligibility signals
            r"\b(credit ?(score|scoring|worthiness)|creditworthiness)\b",
            r"\bloan\b[^.]{0,30}\b(approval|conditions?|decisions?|terms?|rates?|eligibility)\b",
            r"\breview (?:their |the )?loan\b",
            # "price accordingly" in the context of patients / claimants / diagnoses
            r"\bprice accordingly\b",
            # customers/members whose charges are raised, without the word insurance
            r"\b(customers?|clients?|members?|subscribers?)\b[^.]{0,120}\b(raise|increase|charge|adjust|differentiat)\w*"
            r"\b[^.]{0,40}\b(contributions?|rates?|fees?|prices?|tariffs?)\b",
        ),
    ),
    ProhibitedRule(
        "employment decisions",
        "Using health data for decisions about job offers, employees or job applicants is prohibited "
        "(Art. 54(a) and (b)).",
        patterns=(
            r"\b(employee list|staff list|our employees|our staff)\b",
            # job applicants / candidates (including "job candidates to shortlist")
            r"\b(job applicants?|job candidates?|candidates? (?:for (?:a )?jobs?|to shortlist))\b",
            # contract, hiring, dismissal verbs
            r"\b(renew(?:ing|al of)? (?:their |the |employment )?contracts?|contract renewals?|hiring decisions?|"
            r"before (?:we )?(?:hire|promote|dismiss)|dismiss(?:al)?s? of)\b",
            # "terminate/termination of employment" (verb and noun)
            r"\bterminat\w+ (?:of )?(?:their |the )?employment\b",
            # dismiss / retain workers/employees/staff in a decision context (either order)
            r"\b(dismiss\w*|retain\w*)\b[^.]{0,60}\b(workers?|employees?|staff)\b",
            r"\b(workers?|employees?|staff)\b[^.]{0,60}\b(dismiss\w*|retain\w*)\b",
            # redundancy selections / decisions about the workforce
            r"\bredundan\w+\b[^.]{0,60}\b(select\w*|decisions?|list\b|shortlist\w*)\b",
            # career decisions about staff (not "health promotion")
            r"\b(employees?|staff|workers?|personnel)\b[^.]{0,80}(?<!health )\b(promot(?:e|ion|ions)|demot\w*|"
            r"bonus\w*|appraisals?|performance reviews?|disciplin\w*)\b",
        ),
        exceptions=r"\boccupational (health|disease|safety)\b",
    ),
    ProhibitedRule(
        "advertising or marketing",
        "Using health data for advertising or marketing is prohibited (Art. 54(c)).",
        patterns=(r"\badvertis\w*\b", r"\bmarketing (campaign|activit\w+|strateg\w+|purposes?)\b",
                  # a seller who wants to target people ("we sell supplements and want to target gyms")
                  r"\b(we (?:sell|distribute|market)|our (?:products?|brand|supplements?|shop|store|stores))\b[^.]{0,120}"
                  r"\b(target\w*|reach)\b(?!\s+(?:population|group|cohort|patients for|enrolment|recruitment))",
                  r"\b(sales|promotional) (campaign|targeting|strategy)\b", r"\btarget (?:our )?(?:advertising|ads|customers)\b"),
        trigger=r"\bmarket(?:ing)?\b|\bpromot\w*\b|\bcampaign\b|\bbrand\w*\b",
        context=r"\b(our (?:client|product|brand)|products?|launch\w*|prescribers?|physicians?|sales|customers?|consumers?)\b",
        exceptions=r"\bmarketing authori[sz]ation\b|\bhealth promotion\b|\bpromot\w* (?:public )?health\b",
    ),
    ProhibitedRule(
        "harmful products",
        "Using health data to develop products that may harm people or public health, such as tobacco and "
        "nicotine products, alcoholic beverages, weapons or addictive products, is prohibited (Art. 54(d)).",
        patterns=(),
        trigger=r"\b(tobacco|nicotine|cigarettes?|e-?cigarettes?|vap(?:e|es|ing)|pouch(?:es)?|snus|alcohol\w*|beer|spirits|"
                r"liquor|gambling|betting|weapons?|firearms?|illicit drugs?|addictive)\b",
        context=r"\b(develop\w*|flavours?|flavors?|launch\w*|position\w*|products?|apps?|platform\w*|sell\w*|brand\w*|"
                r"market\w*|consumers?|healthier alternative|customers?|engaging|design\w*)\b",
        exceptions=r"\b(cessation|quit\w*|stop smoking|nicotine replacement|harms? of|harm\w* (?:caused|done)|"
                   r"prevent\w* (?:smoking|use)|addiction treatment|treatment guidelines?|"
                   r"tobacco control|control polic\w+|inform\w* (?:policy|prevention|guidelines?)|"
                   r"epidemiolog\w+|prevalence|public health|health polic\w+)\b",
    ),
    ProhibitedRule(
        "decisions against individuals",
        "Taking decisions that are detrimental to people based on their health data is prohibited (Art. 54(a)).",
        patterns=(
            # deny/refuse/blacklist a person group (health reason need not be explicit — the
            # action against the group is itself the signal; study-exclusion uses "from the
            # analysis / from the study / from the cohort", not "from our services/coverage")
            r"\b(deny|refuse|blacklist)\w*\b[^.]{0,60}"
            r"\b(patients?|people|persons|customers?|applicants?|individuals?)\b",
            # exclude … from our/their coverage/services/products/programme/benefits
            r"\bexclud\w+\b[^.]{0,60}\bfrom\b[^.]{0,40}"
            r"\b(?:our |their |the )(?:programme|services?|coverage|benefits?|products?|plan)\b",
            # "decide which … to exclude/blacklist/deny"
            r"\bdecide\b[^.]{0,60}\b(exclude|blacklist|deny|refuse)\b",
        ),
        exceptions=r"\b(from the (?:analysis|study|cohort|sample|dataset|model)|"
                   r"excluded (?:from |due to |because of )?(?:missing|incomplete|invalid|age|criteria|the analysis))\b",
    ),
]

# What the applicant would *do* with the data, in plain business language, in any word order.
_SELLING = (r"\b(?:we sell|our (?:new |own )?(?:products?|brand|shake|supplements?|drinks?|drops|spray|app|"
            r"margarine|mattress\w*|devices?|monitors?|inhalers?|strips|shop|stores?|pharmacy chain|gym chain|client)|"
            r"sales|sells?|selling)\b")
ACTIVITIES = {
    "advertising or marketing": (
        (
            (r"\bads\b|\b(?:online|digital|targeted|social media|tv|print|banner|search)\s+ads?\b|\badvert\w*",),
            (r"\b(?:billboards?|tv spots?|commercials|influencers?|direct mail|mail(?:ing)?s?\b|coupons?|"
             r"lead lists?|sales reps?|social media push)\b",),
            (r"\bmarketing\b",),
            (r"\bmarket (?:our|the|their|its|a|this)\b",),
            (r"\b(?:boost|increase|grow)\w* (?:our |their )?sales\b|\bsell (?:more|best)\b",),
            (r"\bpromot(?:e|es|ing|ion)\b(?!\s+(?:of\s+)?(?:public\s+)?health)", _SELLING),
            (r"\b(?:campaigns?|newsletters?|target\w*|reach|place|position\w*|push)\b", _SELLING),
        ),
        r"\bmarketing authori[sz]ation\b|\bpost-marketing\b|\bhealth[- ]promotion\b|\bsocial marketing\b|"
        r"\b(?:vaccination|prevention|awareness|screening|public health)\s+(?:programme\s+|program\s+)?campaigns?\b|"
        r"\bfree (?:national )?(?:\w+ )?(?:prevention|screening|vaccination) (?:programme|program)\b|\btarget population\b|\bclinical (?:trial|study|evaluation)\b",
    ),
    "insurance or credit": (
        (
            (r"\b(?:insur\w*|health (?:plans?|cover)|private cover|dental plans?|policy ?holders?|underwrit\w*|"
             r"actuar\w*|sickness fund|health fund)\b",
             r"\b(?:premiums?|tariffs?|pric(?:e|es|ing)|surcharges?|deductibles?|(?:monthly )?fees?|loadings?|"
             r"eligibility|renewal|terms|contracts?|cover\b|coverage|exclud\w+|refus\w+|charg\w+|"
             r"what (?:\w+ ){0,3}pay|raise|increase|recalibrat\w*)\b"),
            (r"\b(?:banks?|lenders?|leasing|mortgages?|loans?|credit)\b",
             r"\b(?:approv\w+|grant\w*|decid\w+|factor\w*|terms|rates?|eligib\w*|check\w*|refus\w+|scor\w+)\b"),
            (r"\b(?:residents|tenants|members|customers|clients)\b",
             r"\b(?:shorter|higher|stricter)\b", r"\b(?:contracts?|fees?|rents?|renewals?|prices?|rates?)\b"),
            (r"\b(?:members|customers|clients|subscribers)\b",
             r"\b(?:surcharges?|higher (?:fees|prices|rates|premiums|deductibles)|charg\w+ (?:more|extra|higher))\b"),
        ),
        r"\b(?:research department|publish\w*|study on (?:the )?outcomes|disease management programme|"
        r"forecast\w*|budget|evaluat\w+ whether|coverage gaps?|consumer protection)\b",
    ),
    "employment decisions": (
        (
            (r"\b(?:HR|human resources)\b",),
            (r"\bhir(?:e|es|ing)\b",),
            (r"\b(?:payroll|let go|lay(?:ing)? off|layoffs?|retir(?:e|ing) (?:members|staff|employees|workers)|"
             r"from active duty|(?:coaches|employees|staff|workers) (?:stay|remain)\b)",),
            (r"\b(?:recruit\w*|staffing|job applicants?|applicants|candidates)\b",
             r"\b(?:screen\w*|rank\w*|shortlist\w*|select\w*|reject\w*|harder|avoid\w*|filter\w*)\b"),
            (r"\b(?:employees?|staff|workers?|team leads?|personnel)\b",
             r"\b(?:promot(?:e|es|ing|ion)|dismiss\w*|fir(?:e|ing)|bonus\w*|appraisals?|sick leave|redundan\w+|"
             r"contracts?)\b"),
            (r"\bworkforce\b", r"\b(?:branch\w*|locat\w*|open\w*|hire|hiring)\b"),
        ),
        r"\b(?:occupational (?:health|disease|safety)|burnout|working conditions|well-?being|health promotion|"
        r"(?:trial|study) participants|patients for (?:the|a|our) (?:trial|study)|hiring (?:of )?(?:nurses|doctors|"
        r"physicians|health(?:care)? (?:staff|workers))|(?:trial|study) sites|select\w* (?:\w+ )?sites|"
        r"recruit\w* (?:patients|participants|volunteers)|drug candidates|candidate (?:drugs|compounds|molecules)|"
        r"hir\w* (?:a |an )?(?:statistician|data scientist|analyst|researcher|epidemiologist)|"
        r"(?:need|needed|would need) to hire|staffing (?:study|levels|needs)|caseloads?)\b",
    ),
    "harmful products": (
        (
            (r"\b(?:energy drinks?|caffeine|casinos?|slot[- ]machines?|loot ?box\w*|betting|gambl\w*|vap(?:e|es|ing)|"
             r"e-?cig\w*|nicotine|tobacco|alcohol\w*|beer|spirits|liquor|vodka|gin|whisk(?:e)?y|rum|schnapps|"
             r"weapons?|firearms?|pistols?|rifles?|guns?|ammunition|psychoactive|recreational (?:drugs?|compounds?)|"
             r"narcotics?)\b",
             r"\b(?:design\w*|develop\w*|produc\w+|sell\w*|reach|market\w*|launch\w*|flavou?r\w*|find our|"
             r"customers?|players?|brand|creat\w+|build\w*|formulat\w+|position\w*|aimed at|user base|tasting|"
             r"tun(?:e|es|ed|ing)|releases?|maximi[sz]\w*|engag\w*|play ?time|in-app|spend|segments?|sponsor\w*)\b"),
        ),
        r"\b(?:cessation|quit\w*|stop smoking|nicotine replacement|harms? of|addiction treatment|prevent\w*|"
        r"tobacco control|public health|epidemiolog\w+)\b",
    ),
    "decisions against individuals": (
        (
            (r"\b(?:refus\w+|deny\w*|avoid\w*|reject\w*|declin\w+|exclud\w+|turn(?:ed)? (?:them |people )?away|"
             r"ban(?:ned|s)?|stricter|priority)\b",
             r"\b(?:rent\w*|flats?|apartments?|tenan\w+|households?|memberships?|members|customers?|applicants?|"
             r"service|admissions?|families|pool|gym|classes|clubs?|renewals?)\b"),
        ),
        r"\b(?:avoid\w* (?:falls|admissions|readmissions|complications|strokes|harm)|avoidable (?:hospital )?"
        r"(?:admissions|readmissions|hospitali[sz]ations)|hospital admissions|exclusion criteria|"
        r"from the (?:analysis|study|cohort))\b",
    ),
}

REIDENTIFICATION = re.compile(
    r"\b(match\w*|link\w*|combine\w*)\b[^.]{0,60}\b(with|to|against)\b[^.]{0,30}\b(our|their|own|employee|customer|"
    r"client|member)s?\b[^.]{0,20}\b(lists?|database|records|data|files?)\b|\bre-?identif\w*\b|\bidentify (?:the )?"
    r"(?:individual|specific|named) (?:patients|persons|people)\b", I)

SPE_JUSTIFICATION = [
    # "... which aggregated tables cannot provide", "summary statistics would not capture ..."
    r"\b(aggregated?|summary|anonymi[sz]ed|statistical)\s+(?:tables?|data|statistics|counts?|figures|numbers)\s+"
    r"(?:cannot|can ?not|can't|do not|don't|would not|wouldn't|will not|won't)\s+(?:provide|capture|show|answer|"
    r"support|represent|reflect|give|contain|be used)\b",
    r"\b(?:not possible|impossible)\s+with\s+(?:aggregated?|summary|anonymi[sz]ed)\s+(?:data|statistics|tables?)\b",
    r"\beach patient'?s\s+(?:sequence|history|histories|trajectory|timeline|time series|records?)\b",
    r"\b(?:individual|patient|person)[- ]level\s+(?:sequences?|histories|trajectories|timelines|time series)\b",
    r"\b(aggregates?|aggregated data|statistics|summary data|summary statistics|anonymi[sz]ed (?:data|statistics|counts?))\b"
    r"[^.]{0,40}\b(are|is|would be|will be)\s+(?:not (?:enough|sufficient|adequate)|insufficient)\b",
    r"\b(aggregates?|aggregated (?:data|tables)|summary (?:data|statistics|tables)|statistics)\s+(?:will|would|do|does|"
    r"can)\s*(?:not|n't)\s+(?:give|show|capture|provide|reveal)\b",
    r"\b(not enough|insufficient|cannot be answered|can ?not be done)\b[^.]{0,40}\b(aggregates?|statistics|aggregated)\b",
    r"\b(needs?|requires?|requiring|need)\b[^.]{0,30}\b(individual|patient|record|person)[- ]level\b",
    r"\b(individual|patient|record|person)[- ]level (?:data|histories|records|trajectories|information) (?:is|are) "
    r"(?:needed|necessary|required|essential)\b",
]

ALLOWED_PURPOSES = [
    ("public health", r"\b(public health|prevention|preventive|surveillance|epidemiolog\w*|health threats?)\b"),
    ("policy-making", r"\b(polic(?:y|ies)|ministry|guidelines?|quality (?:programme|program|improvement)|regulat\w+|"
                      r"health authorit\w+|planning)\b"),
    ("official statistics", r"\b(official statistics|statistics office|statistical office|health report)\b"),
    ("education or teaching", r"\b(teaching|education\w*|training (?:of )?(?:doctors|nurses|students|GPs)|curricul\w+|"
                              r"teaching module|courses?|lectures?|(?:medical|nursing) (?:school|students)|"
                              r"train(?:ing)? (?:medical |nursing )?students)\b"),
    ("scientific research", r"\b(research\w*|study|studies|studying|we study|randomi[sz]ed (?:controlled )?trials?|power calculation|investigat\w+|analys[ie]s|risk factors?|"
                            r"scientific\w*|evaluat\w+|assess\w*|burden of|health economic\w*|cost-effectiveness|"
                            r"real-world (?:evidence|safety|data)|clinical (?:study|trial|evaluation)\w*|"
                            r"(?:update|develop)\w* (?:clinical )?guidelines)\b"),
    ("developing products or services", r"\b(medical devices?|in vitro diagnostic|digital health (?:app|application)s?|"
                                        r"develop\w* (?:a |an |our )?(?:software|app|device|medicine|drug|therapy)|"
                                        r"product development)\b"),
    ("training and testing algorithms", r"\b(train\w*|validat\w+|test\w*)\b[^.]{0,40}\b(models?|algorithms?|AI|"
                                       r"prediction|classifiers?)\b|\bmachine learning\b|\bprediction (?:model|software)\b"),
    ("improving care", r"\b(improv\w+ (?:the )?(?:care|treatment|delivery)|treatment gap|intervene earlier|patient safety|"
                       r"better care|quality of (?:health ?)?care|care quality|optimi[sz]\w* (?:the )?(?:treatment|care)|"
                       r"treatment optimi[sz]ation|guideline[- ]adheren\w*|adheren\w* to (?:clinical )?guidelines|"
                       r"follows? (?:the )?guidelines|benchmark\w*|reduc\w+ (?:waiting times|readmissions|admissions)|"
                       r"optimi[sz]\w*(?: \w+)? (?:treatment|care|therapy|pathways?)|audit\w*|quality (?:team|assurance|"
                       r"improvement)|(?:how|whether) (?:well )?(?:\w+ )?care is organi[sz]ed|learn from the best)\b"),
]


@dataclass
class Finding:
    rule: str
    outcome: str
    matched: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Decision:
    decision: str
    articles: list[str]
    reason: str
    trace: list[Finding]
    purposes: list[str]
    staff_flags: list[str]
    reidentification: bool = False  # the applicant states an intent to re-identify or link people
    hold_for_review: bool = False  # approved in principle, but nothing is released before staff review

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "articles": self.articles,
            "reason": self.reason,
            "purposes": self.purposes,
            "staff_flags": self.staff_flags,
            "reidentification": self.reidentification,
            "hold_for_review": self.hold_for_review,
            "trace": [f.__dict__ for f in self.trace],
        }


def _snippets(pattern: str, text: str) -> list[str]:
    return [m.group(0) for m in re.finditer(pattern, text, I)]


def _with_activities(rules: list[ProhibitedRule]) -> list[ProhibitedRule]:
    from dataclasses import replace
    return [replace(r, activities=ACTIVITIES[r.category][0], activity_exceptions=ACTIVITIES[r.category][1])
            if r.category in ACTIVITIES else r for r in rules]


# Sentences that describe a public campaign for a free health programme, or research that protects
# consumers from a prohibited practice, are not that practice, even when they name it.
PATTERN_EXCEPTIONS = {
    "advertising or marketing": r"\bhealth[- ]promotion\b|\bsocial marketing\b|\bfree (?:national )?(?:\w+ )?"
                                r"(?:prevention|screening|vaccination) (?:programme|program)\b|"
                                r"\badvertising (?:bans?|restrictions?|regulations?|rules|limits)\b",
    "insurance or credit": r"\bconsumer protection\b|\bcoverage gaps?\b",
}


def _sentence_of(snippet: str, text: str) -> str:
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if snippet in sentence:
            return sentence
    return text


def check_prohibited(text: str) -> list[tuple[ProhibitedRule, list[str]]]:
    hits = []
    for rule in _with_activities(PROHIBITED):
        if rule.exceptions and re.search(rule.exceptions, text, I):
            # An exception cancels only the trigger/context pairing, not explicit patterns.
            matched = [s for p in rule.patterns for s in _snippets(p, text)
                       if not re.search(rule.exceptions, s, I)]
        else:
            matched = [s for p in rule.patterns for s in _snippets(p, text)]
            if rule.trigger and rule.context:
                for sentence in re.split(r"(?<=[.!?])\s+", text):
                    t = re.search(rule.trigger, sentence, I)
                    c = re.search(rule.context, sentence, I)
                    if t and c:
                        matched.append(f"{t.group(0)} … {c.group(0)}")
        for sentence in re.split(r"(?<=[.!?;])\s+", text):
            if rule.activity_exceptions and re.search(rule.activity_exceptions, sentence, I):
                continue
            for group in rule.activities:
                found = [re.search(rx, sentence, I) for rx in group]
                if all(found):
                    matched.append(" … ".join(f.group(0) for f in found))
        cancel = PATTERN_EXCEPTIONS.get(rule.category)
        if cancel:
            matched = [m for m in matched if not re.search(cancel, _sentence_of(m.split(" … ")[0], text), I)]
        if matched:
            hits.append((rule, list(dict.fromkeys(matched))))
    return hits


# Art. 53(1) point for each purpose; (a)-(c) are reserved for public sector bodies (Art. 53(2)).
PURPOSE_POINTS = {
    "public health": "a", "policy-making": "b", "official statistics": "c", "education or teaching": "d",
    "scientific research": "e", "training and testing algorithms": "e(ii)", "improving care": "f",
    "developing products or services": "e(i)",
}
PUBLIC_ONLY = {"public health", "policy-making", "official statistics"}
COMMERCIAL = re.compile(
    r"\b(?:GmbH|AG|KG|OG|Ltd|LLC|Inc|SA|SE|plc|Corp|B\.?V|S\.?r\.?l)\b|\b(?:company|companies|insurer|insurance|"
    r"versicherung|agency|bank|consultancy|start-?up|brand|chain|retailer|manufacturer|marketing|sales)\b", I)
PUBLIC_BODY = re.compile(
    r"\b(ministry|ministerium|government|authority|authorities|agency|statistics office|statistical office|"
    r"national statistics|public (?:health )?(?:body|institute|agency|authority|administration)|municipal\w*|"
    r"federal|social (?:security|insurance)|health insurance fund|european commission|ECDC|EMA|"
    r"public (?:health |accident |pension )?insur\w+|Landesamt|state health office|Gesundheitsamt|Gesundheitskasse|Sozialversicherung|[ÖO]GK|AUVA|SVS|BVAEB|"
    r"Land (?:Steiermark|Tirol|Salzburg|Kärnten|Vorarlberg|Burgenland|Niederösterreich|Oberösterreich)|"
    r"Stadt Wien)\b", I)

# Words that suggest a use the rules may not have recognised. With no citable Art. 53(1) purpose,
# any of these holds the request for staff review, whoever the applicant is.
WARNING_WORDS = re.compile(
    r"\b(?:target\w*|customers?|clients?|members?|membership|applicants?|premiums?|sell\w*|sales|market\w*|"
    r"campaign\w*|brand\w*|launch\w*|hir(?:e|ing)|employ\w*|staff|workforce|payroll|loans?|interest rates?|"
    r"fees?|surcharges?|supplement\w*|contracts?|tenan\w+|renewals?|priority|stricter|ban\w*|"
    r"petition|retir\w+|nicotine|tobacco|alcohol\w*|beer|vodka|newsletters?|players?|games?|pistols?|"
    r"weapons?|guns?|psychoactive|recreational|compounds?|price\w*|pricing|insur\w+|let go)\b", I)


def allowed_purposes(text: str) -> list[str]:
    return [name for name, pattern in ALLOWED_PURPOSES if re.search(pattern, text, I)]


def _cite(purposes: list[str]) -> str:
    return _join([f"{p} (point {PURPOSE_POINTS[p]})" for p in purposes])


def _quote(rule_hits: list[str], text: str) -> str:
    """The applicant's own sentence that triggered a prohibition, to show them why."""
    for hit in rule_hits:
        anchor = hit.split(" … ")[0].split(": ", 1)[-1]
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if anchor.lower() in sentence.lower():
                return sentence.strip()
    return ""


def spe_justification(text: str) -> list[str]:
    return [s for p in SPE_JUSTIFICATION for s in _snippets(p, text)]


def request_type(request: dict) -> str:
    requested_as = str(request.get("requested_as", "")).lower()
    if "access application" in requested_as:
        return "access_application"
    if "data request" in requested_as:
        return "data_request"
    fmt = str(request.get("requested_format", "")).lower()
    return "access_application" if "record" in fmt or "pseudonym" in fmt else "data_request"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def decide(request: dict) -> Decision:
    decision = _decide(request)
    text = f"{request.get('purpose', '')} {request.get('question', '')}"
    # Chapter IV prohibits re-identifying the people in the data (recital 54); the pipeline will not
    # grant a secure processing environment to an application that announces it.
    decision.reidentification = bool(_snippets(REIDENTIFICATION.pattern, text))
    decision.hold_for_review = decision.decision != "REJECT" and any(
        f.startswith("Held for review:") for f in decision.staff_flags)
    return decision


def _decide(request: dict) -> Decision:
    purpose = str(request.get("purpose", ""))
    question = str(request.get("question", ""))
    organisation = str((request.get("applicant") or {}).get("organisation", ""))
    text = f"{purpose} {question}"
    trace: list[Finding] = []
    flags: list[str] = []
    purposes = allowed_purposes(text)

    reident = _snippets(REIDENTIFICATION.pattern, text)
    if reident:
        flags.append(f"Re-identification risk: the applicant plans to link the data with their own records ({reident[0]!r}).")

    # 1. Prohibited purposes
    hits = check_prohibited(text)
    if hits:
        trace.append(Finding("1. Prohibited purpose (Art. 54)", "matched",
                             [f"{rule.category}: {m}" for rule, ms in hits for m in ms]))
        reason = " ".join(dict.fromkeys(rule.explanation for rule, _ in hits))
        quoted = _quote([m for _, ms in hits for m in ms], text)
        if quoted:
            reason += f' Your application says: "{quoted}"'
        reason += (" If this is not what you intend, you can submit a new application with the purpose "
                   "described more precisely.")
        if reident:
            reason += " Matching the records with the applicant's own lists would also re-identify people."
        return Decision("REJECT", ["54"], reason, trace, purposes, flags)
    trace.append(Finding("1. Prohibited purpose (Art. 54)", "no match"))
    weak = [w for w in ("insurance", "employer", "marketing", "advertis", "nicotine", "tobacco", "alcohol")
            if re.search(rf"\b{w}", f"{text} {organisation}", I)]
    if weak:
        flags.append(f"Possible prohibited-purpose wording ({', '.join(weak)}) did not meet a rule; please check.")

    # A third party working on behalf of a public body counts as that body (Art. 53(2)).
    public_body = bool(PUBLIC_BODY.search(organisation)) or bool(re.search(
        r"\b(?:commissioned by|on behalf of|mandated by|contracted by)\s+(?:the\s+)?(?:\w+\s+){0,2}"
        r"(?:ministry|authority|agency|government|statistics office|social insurance)\b", text, I))
    citable = purposes if public_body else [p for p in purposes if p not in PUBLIC_ONLY]
    if purposes and not citable:
        flags.append(f"Art. 53(2): the purpose ({_join(purposes)}) is reserved for public sector bodies, and "
                     f"'{organisation or 'the applicant'}' does not look like one. Please check before release.")
    elif len(citable) < len(purposes):
        dropped = [p for p in purposes if p not in citable]
        flags.append(f"Art. 53(2): {_join(dropped)} {'is' if len(dropped) == 1 else 'are'} reserved for public "
                     "sector bodies; only the other purposes were cited.")
    # Only the name counts, not a description in brackets ("Mieterverein (tenants' association)").
    commercial = bool(COMMERCIAL.search(re.sub(r"\([^)]*\)", "", organisation))) and not public_body
    warning = WARNING_WORDS.search(purpose)
    if not citable and commercial:
        flags.append(f"Held for review: '{organisation}' looks like a commercial applicant and no Art. 53(1) purpose "
                     "open to it was recognised. Nothing is released until staff confirm the purpose.")
    elif not citable and warning:
        flags.append(f"Held for review: no Art. 53(1) purpose open to the applicant was recognised and the text "
                     f"mentions '{warning.group(0)}'. Nothing is released until staff confirm the purpose.")
    if citable:
        allowed = f"The purpose falls under Art. 53(1): {_cite(citable)}."
    else:
        if not purposes:
            flags.append("No Art. 53 purpose was recognised in the purpose text; please confirm it is an allowed purpose.")
        allowed = ("The stated purpose did not match a specific point of Art. 53(1) automatically; staff have been "
                   "asked to confirm it.")

    # 2. Health data request -> statistics
    kind = request_type(request)
    if kind == "data_request":
        trace.append(Finding("2. Health data request", "matched", [str(request.get("requested_as", ""))]))
        return Decision("APPROVE_STATISTICAL", ["53", "69"],
                        f"{allowed} The question is answered with anonymised statistics (Art. 69).",
                        trace, purposes, flags)
    trace.append(Finding("2. Health data request", "no match", note="requested as a data access application"))

    # 3. Access application that explains why statistics are not enough
    justification = spe_justification(text)
    if justification:
        trace.append(Finding("3. Pseudonymised data justified", "matched", justification))
        return Decision(
            "APPROVE_SPE", ["53", "66", "68", "73"],
            f"{allowed} The applicant explains why statistics are not enough, so pseudonymised data is "
            "justified (Art. 66). Access is granted only under a data permit (Art. 68), inside a secure "
            "processing environment from which data cannot be downloaded (Art. 73).",
            trace, purposes, flags)
    trace.append(Finding("3. Pseudonymised data justified", "no match",
                         note="the application does not explain why statistics would not be enough"))
    if "training and testing algorithms" in purposes:
        flags.append("The applicant mentions model training but does not say why statistics are not enough.")

    # 4. Any other access application -> statistics instead
    trace.append(Finding("4. Other access application", "matched"))
    return Decision(
        "APPROVE_STATISTICAL", ["53", "66", "69"],
        f"{allowed} The application does not explain why statistics would not be enough, so pseudonymised "
        "records are not justified (Art. 66). The question is answered with anonymised statistics instead (Art. 69).",
        trace, purposes, flags)
