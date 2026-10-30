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


PROHIBITED = [
    ProhibitedRule(
        "insurance or credit",
        "Using health data to set insurance premiums or contributions, or to exclude people from "
        "insurance or credit, is prohibited (Art. 54).",
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
        ),
    ),
    ProhibitedRule(
        "employment decisions",
        "Using health data for decisions about employees or job applicants is prohibited (Art. 54).",
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
        ),
        exceptions=r"\boccupational (health|disease|safety)\b",
    ),
    ProhibitedRule(
        "advertising or marketing",
        "Advertising and marketing are prohibited purposes (Art. 54).",
        patterns=(r"\badvertis\w*\b", r"\bmarketing (campaign|activit\w+|strateg\w+|purposes?)\b",
                  r"\b(sales|promotional) (campaign|targeting|strategy)\b", r"\btarget (?:our )?(?:advertising|ads|customers)\b"),
        trigger=r"\bmarket(?:ing)?\b|\bpromot\w*\b|\bcampaign\b|\bbrand\w*\b",
        context=r"\b(our (?:client|product|brand)|products?|launch\w*|prescribers?|physicians?|sales|customers?|consumers?)\b",
        exceptions=r"\bmarketing authori[sz]ation\b|\bhealth promotion\b|\bpromot\w* (?:public )?health\b",
    ),
    ProhibitedRule(
        "harmful products",
        "Developing or marketing products that harm health, such as tobacco and nicotine products, "
        "alcohol, addictive products or weapons, is prohibited (Art. 54).",
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
        "Taking decisions that are detrimental to the people in the data is prohibited (Art. 54).",
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

REIDENTIFICATION = re.compile(
    r"\b(match\w*|link\w*|combine\w*)\b[^.]{0,60}\b(with|to|against)\b[^.]{0,30}\b(our|their|own|employee|customer|"
    r"client|member)s?\b[^.]{0,20}\b(lists?|database|records|data|files?)\b|\bre-?identif\w*\b|\bidentify (?:the )?"
    r"(?:individual|specific|named) (?:patients|persons|people)\b", I)

SPE_JUSTIFICATION = [
    r"\b(aggregates?|aggregated data|statistics|summary data|summary statistics|anonymi[sz]ed (?:data|statistics|counts?))\b"
    r"[^.]{0,40}\b(are|is|would be|will be)\s+(?:not (?:enough|sufficient|adequate)|insufficient)\b",
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
                              r"teaching module)\b"),
    ("scientific research", r"\b(research\w*|study|studies|we study|investigat\w+|analys[ie]s|risk factors?)\b"),
    ("training and testing algorithms", r"\b(train\w*|validat\w+|test\w*)\b[^.]{0,40}\b(models?|algorithms?|AI|"
                                       r"prediction|classifiers?)\b|\bmachine learning\b|\bprediction (?:model|software)\b"),
    ("improving care", r"\b(improv\w+ (?:the )?(?:care|treatment|delivery)|treatment gap|intervene earlier|patient safety|"
                       r"better care)\b"),
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

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "articles": self.articles,
            "reason": self.reason,
            "purposes": self.purposes,
            "staff_flags": self.staff_flags,
            "trace": [f.__dict__ for f in self.trace],
        }


def _snippets(pattern: str, text: str) -> list[str]:
    return [m.group(0) for m in re.finditer(pattern, text, I)]


def check_prohibited(text: str) -> list[tuple[ProhibitedRule, list[str]]]:
    hits = []
    for rule in PROHIBITED:
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
        if matched:
            hits.append((rule, list(dict.fromkeys(matched))))
    return hits


def allowed_purposes(text: str) -> list[str]:
    return [name for name, pattern in ALLOWED_PURPOSES if re.search(pattern, text, I)]


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
        if reident:
            reason += " Matching the records with the applicant's own lists would also re-identify people."
        return Decision("REJECT", ["54"], reason, trace, purposes, flags)
    trace.append(Finding("1. Prohibited purpose (Art. 54)", "no match"))
    weak = [w for w in ("insurance", "employer", "marketing", "advertis", "nicotine", "tobacco", "alcohol")
            if re.search(rf"\b{w}", f"{text} {organisation}", I)]
    if weak:
        flags.append(f"Possible prohibited-purpose wording ({', '.join(weak)}) did not meet a rule; please check.")

    purpose_text = _join(purposes) if purposes else "the stated purpose"
    if not purposes:
        flags.append("No Art. 53 purpose was recognised in the purpose text; please confirm it is an allowed purpose.")
    allowed = f"{purpose_text[0].upper()}{purpose_text[1:]} {'is an' if len(purposes) <= 1 else 'are'} allowed purpose{'s' if len(purposes) > 1 else ''} (Art. 53)."

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
