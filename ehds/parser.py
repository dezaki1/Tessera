"""Rule-based parser: a request's plain-English definitions -> QuerySpec / CohortSpec.

How a request is read:

1. Each definition sentence is classified: a concept ("X = ..."), age bands,
   the medication-activity rule, a measurement rule ("Use the latest ..."),
   a counting rule ("Count stays (not patients) ..."), or the Report line.
2. Concepts are turned into criteria. Codes written in the sentence win;
   otherwise names are looked up in the terminology.
3. The Report line gives the groups ("per region and age band") and the list
   of numbers. Each number is a count, a percentage or a mean; phrases like
   "of them", "that share" or "their" refer back to earlier numbers.
4. Anything the question restricts but the Report line does not mention
   (e.g. "women with breast cancer" when the Report line only says "women")
   is applied to every number, and recorded as an assumption.

Nothing here touches patient data. Whatever cannot be interpreted is listed in
`unresolved` so staff (or the optional LLM step) can deal with it; the parser
never guesses silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .spec import (
    AgeBand,
    AgeCriterion,
    CohortSpec,
    ConditionCriterion,
    Criterion,
    DataHolderCriterion,
    Dimension,
    EncounterCriterion,
    EncounterFilter,
    HasEncounterCriterion,
    MedicationActiveRule,
    MedicationCriterion,
    Metric,
    ObservationCriterion,
    QuerySpec,
    RecordSelection,
    RegionCriterion,
    SexCriterion,
    ValueSource,
)
from .terminology import Term, Terminology, name_pattern

I = re.IGNORECASE

NEGATION_RE = re.compile(
    r"\b(no|not|without|never|lacking|free of|absence of|don't|do not|doesn't|does not|"
    r"haven't|have not|hasn't|has not|didn't|did not|none)\b", I)
ACTIVE_RE = re.compile(r"\b(active|current|currently|taking|takes|take|still|ongoing|on)\b", I)
BACKREF_RE = re.compile(
    r"\b(of them|of whom|of these|of those|among them|among these|among those|their|them|"
    r"that share|this share|that proportion|this proportion|that percentage|those)\b", I)
# "%" right after a number is a unit ("HbA1c above 8%"), not a request for a share.
PERCENT_RE = re.compile(r"(?<![\d.])(?<![\d.]\s)%|\b(percent|percentage|share|proportion|prevalence|fraction|rate)\b", I)
MEAN_RE = re.compile(r"\b(mean|average|avg)\b", I)
UNSUPPORTED_STAT_RE = re.compile(r"\b(median|minimum|maximum|min|max|standard deviation|variance|quartile|percentile|sum of)\b", I)
LINK_RE = re.compile(r"\b(with|who|having|had|has|have|whose|that)\b", I)
FORMAT_RE = re.compile(
    r",?\s*(?:with|to|rounded to|in|at)?\s*(?:one|1)\s+decimal(?:\s+places?)?|,?\s*rounded\b", I)
REFERENCE_DATE_RE = re.compile(r"\b(?:on|at|as of)?\s*31\s+December\s+2025\b", I)
OTHER_DATE_RE = re.compile(
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{4}\b", I)
PATIENT_LEVEL_RE = re.compile(
    r"\b(for each patient|per patient|each individual|patient[ _-]?ids?|birth ?dates?|"
    r"dates? of birth|patient names?|address(?:es)?|social security)\b", I)

DIMENSION_WORDS = [
    (re.compile(r"\b(?:age[\s-]*bands?|bands?|age[\s-]*groups?|age[\s-]*classes?|ages?)\b", I), "age_band"),
    (re.compile(r"\b(?:regions?|federal states?|states?|provinces?|bundesl(?:a|ä)nder|bundesland)\b", I), "region"),
    (re.compile(r"\b(?:hospitals?|data[\s_-]*holders?|clinics?|sites?|klinikum|providers?)\b", I), "data_holder"),
    (re.compile(r"\b(?:sex|sexes|gender|genders)\b", I), "sex"),
    (re.compile(r"\b(?:visit|encounter|stay)[\s-]*types?|types? of (?:visit|encounter|stay)s?\b", I), "encounter_type"),
    (re.compile(r"\b(?:calendar\s+)?years?\b", I), "encounter_year"),
]

PATIENT_HEADS = r"patients?|people|persons?|individuals?|adults?|women|woman|men|man|children|child|cases?"

RECORD_WORDS = [
    (re.compile(r"\b(conditions?|diagnos[ie]s|problem list|comorbidit(?:y|ies))\b", I), "conditions"),
    (re.compile(r"\b(medications?|prescriptions?|drugs?|treatments?|medicines?)\b", I), "medications"),
    (re.compile(r"\b(observations?|measurements?|lab(?:oratory)? (?:values?|results?)|vital signs?)\b", I), "observations"),
    (re.compile(r"\b(encounters?|visits?|stays?|admissions?|hospitali[sz]ations?)\b", I), "encounters"),
    (re.compile(r"\b(demographics?)\b", I), "patients"),
]
DEMOGRAPHIC_COLUMNS = [
    (re.compile(r"\b(sex|gender)\b", I), "sex"),
    (re.compile(r"\b(birth ?dates?|dates? of birth|birth years?|year of birth)\b", I), "birth_date"),
    (re.compile(r"\bage\b", I), "age"),
    (re.compile(r"\bregions?\b", I), "region"),
    (re.compile(r"\b(hospitals?|data[\s_]*holders?)\b", I), "data_holder"),
]


# --- data structures --------------------------------------------------------------


@dataclass
class Concept:
    name: str
    definition: str
    kind: str  # condition | medication | measurement | attribute | visit | composite | ratio
    criteria: list = field(default_factory=list)  # patient-level
    med: MedicationCriterion | None = None
    loinc: str | None = None
    visit: EncounterFilter | None = None
    ratio: tuple[str, str] | None = None
    keys: frozenset = frozenset()
    patterns: list[re.Pattern] = field(default_factory=list)


@dataclass
class Atom:
    start: int
    end: int
    text: str
    kind: str  # condition | medication | measurement | attribute | visit | composite | ratio | year | primary
    key: str
    criteria: list = field(default_factory=list)
    med: MedicationCriterion | None = None
    loinc: str | None = None
    visit: EncounterFilter | None = None
    years: tuple[date | None, date | None] | None = None
    primary: list[str] | None = None
    ratio: tuple[str, str] | None = None
    concept: Concept | None = None
    priority: int = 0


@dataclass
class Meaning:
    text: str
    criteria: list = field(default_factory=list)
    unit: str = "patient"
    measurement: str | None = None
    value_source: str | None = None
    ratio: tuple[str, str] | None = None
    back_ref: bool = False
    visit_head: EncounterFilter | None = None
    keys: set = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedRequest:
    statistics: QuerySpec | None = None
    cohort: CohortSpec | None = None
    patient_level_output: bool = False
    assumptions: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    concepts: dict[str, str] = field(default_factory=dict)
    method: str = "rules"

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "statistics": self.statistics.model_dump(mode="json") if self.statistics else None,
            "cohort": self.cohort.model_dump(mode="json") if self.cohort else None,
            "patient_level_output": self.patient_level_output,
            "assumptions": list(self.assumptions),
            "unresolved": list(self.unresolved),
            "concepts": dict(self.concepts),
        }


class _Unresolved(Exception):
    pass


# --- small helpers ------------------------------------------------------------------


def _dedupe(criteria: list) -> list:
    seen, out = set(), []
    for c in criteria:
        key = c.model_dump_json()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _criteria_key(criteria: list) -> frozenset:
    return frozenset(c.model_dump_json() for c in criteria)


def _slug(text: str, limit: int = 60) -> str:
    s = text.lower().replace("%", " pct ").replace("+", " plus ")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s:
        return "value"
    if not s[0].isalpha():
        s = "n_" + s
    if len(s) > limit:
        s = s[:limit].rsplit("_", 1)[0]
    return s


def _clean_phrase(text: str) -> str:
    text = FORMAT_RE.sub("", text)
    text = REFERENCE_DATE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" .,;:")


def _strip_count_words(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^(?:the\s+)?(?:total\s+)?(?:number|count|size)\s+of\s+", "", t, flags=I)
    t = re.sub(r"^how many(?:\s+(?:of them|of these|of whom|of those))?"
               r"(?:\s+(?:have|has|had|are|were|take|took|receive|received|with|get|got))?\s+", "", t, flags=I)
    t = re.sub(r"^(?:the|all|all the)\s+", "", t, flags=I)
    return t.strip()


def _years(y1: int, y2: int | None = None) -> tuple[date, date]:
    return date(y1, 1, 1), date(y2 or y1, 12, 31)


def _split_top_level(text: str, separators: str) -> list[tuple[str, str]]:
    """Split on separators outside parentheses; returns (separator_before, piece)."""
    pieces, depth, current, sep_before = [], 0, [], ""
    i = 0
    sep_re = re.compile(separators, I)
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0:
            m = sep_re.match(text, i)
            if m and m.end() > i:
                pieces.append((sep_before, "".join(current)))
                sep_before, current = m.group(0), []
                i = m.end()
                continue
        current.append(ch)
        i += 1
    pieces.append((sep_before, "".join(current)))
    return [(s, p.strip()) for s, p in pieces if p.strip()]


# --- the parser ----------------------------------------------------------------------


class RuleParser:
    def __init__(self, terminology: Terminology):
        self.terminology = terminology

    # ---- public entry point ----
    def parse(self, request: dict) -> ParsedRequest:
        ctx = _Context(self.terminology, request)
        return ctx.run()


class _Context:
    def __init__(self, terminology: Terminology, request: dict):
        self.terminology = terminology
        self.request = request
        self.question = str(request.get("question") or "")
        self.definitions = [str(d) for d in (request.get("definitions") or [])]
        self.concepts: list[Concept] = []
        self.bands: list[AgeBand] | None = None
        self.active_rule: MedicationActiveRule = "start_and_end"
        self.active_rule_stated = False
        self.required_measurements: list[str] = []
        self.count_visits = False
        self.visit_years: tuple[date | None, date | None] | None = None
        self.report: str | None = None
        self.result = ParsedRequest()

    # ---------------------------------------------------------------- driver
    def run(self) -> ParsedRequest:
        r = self.result
        for sentence in self._sentences():
            try:
                self._classify(sentence)
            except _Unresolved as exc:
                r.unresolved.append(f"{sentence} ({exc})")
        if OTHER_DATE_RE.search(" ".join(self.definitions)) and any(
            m.group(0).lower() != "31 december 2025" for m in OTHER_DATE_RE.finditer(" ".join(self.definitions))
        ):
            r.unresolved.append("A reference date other than 31 December 2025 is mentioned; only 31 December 2025 is supported.")
        everything = " ".join([self.question, *self.definitions])
        if re.search(r"\b(?:includ\w*|with|also|even)\b[^.]{0,40}\bopt(?:ed)?[- ]?outs?\b", everything, I):
            r.unresolved.append("The request asks to include patients who opted out. They are always excluded "
                                "(Art. 71); the numbers leave them out.")
        r.concepts = {c.name: f"{c.kind}: {c.definition}" for c in self.concepts}
        try:
            r.statistics = self._statistics()
        except _Unresolved as exc:
            r.unresolved.append(str(exc))
        if str(self.request.get("requested_as", "")).lower().find("access application") >= 0 or \
                "record" in str(self.request.get("requested_format", "")).lower():
            try:
                r.cohort = self._cohort()
            except _Unresolved as exc:
                r.unresolved.append(f"Cohort for a secure processing environment: {exc}")
        return r

    def _sentences(self) -> list[str]:
        out = []
        for d in self.definitions:
            d = d.strip()
            # "ACE inhibitor = ...; ARB = ..." holds two definitions.
            parts = [p.strip() for p in d.split(";")] if d.count("=") > 1 else [d]
            out.extend(p for p in parts if p)
        # Definitions that only name codes go first, so that combined definitions
        # ("Cohort = adults with type 2 diabetes ...") can refer to them.
        simple = re.compile(r"=\s*.*\b(ICD[- ]?10|ATC|LOINC|sex|type)\b|=\s*aged?\b", I)
        return sorted(out, key=lambda x: 0 if simple.search(x) else 1)

    # ------------------------------------------------------- sentence types
    def _classify(self, sentence: str) -> None:
        s = sentence.strip().rstrip(".")
        if re.match(r"^(report|show|give|provide|list|return|present)\b", s, I):
            if self.report is not None:
                self.report += "; " + s
            else:
                self.report = s
            return
        if re.match(r"^(?:the\s+)?(?:\d+[\s-]*year\s+)?age[\s-]*(?:bands?|groups?|classes?)\b", s, I):
            self.bands = self._parse_bands(s)
            return
        if re.search(r"\bactive\b", s, I) and re.search(r"\b(medications?|prescriptions?|drugs?)\b", s, I) \
                and "=" not in s:
            if re.search(r"start[_ ]date", s, I):
                self.active_rule = "start_and_end"
            elif re.search(r"end[_ ]date\s+is\s+empty|no end[_ ]date|without an? end[_ ]date", s, I):
                self.active_rule = "end_empty"
            else:
                raise _Unresolved("medication activity rule not understood")
            self.active_rule_stated = True
            return
        if re.match(r"^(?:use|take|using)\b", s, I):
            self._parse_measurement_rule(s)
            return
        if re.match(r"^count\b", s, I):
            self._parse_count_rule(s)
            return
        if re.fullmatch(r"(?:round(?:ed)?|show|report)?\s*(?:to|with)?\s*(?:one|1)\s+decimal(?:\s+places?)?", s, I):
            return
        if re.match(r"^(?:the\s+)?(?:length of (?:hospital\s+)?stay|duration of (?:the\s+)?stay|age)\b"
                    r"\s*(?:=|:|\bmeans\b|\bis\b)", s, I):
            # Length of stay (end_date - start_date, in days) and age (2025 - birth year) are built in.
            self.result.assumptions.append(f"'{s}' matches the built-in definition.")
            return
        if "=" in s:
            name, body = s.split("=", 1)
            self._define(name.strip(), body.strip(), sentence)
            return
        # "Heart failure: ICD-10 I50", "Asthma means ...", "Statin is defined as ..."
        m = re.match(r"^(?P<name>[^:]{1,60}?)\s*(?::|\bmeans\b|\bis defined as\b|\bare defined as\b|"
                     r"\brefers to\b|\bdefined as\b)\s*(?P<body>.+)$", s, I)
        if m and not re.match(r"^(?:report|show|note|please)\b", m.group("name"), I):
            self._define(m.group("name").strip(), m.group("body").strip(), sentence)
            return
        raise _Unresolved("sentence type not recognised")

    def _parse_bands(self, s: str) -> list[AgeBand]:
        _, _, listing = s.partition(":")
        if not listing.strip():
            m = re.search(r"(\d+)[\s-]*year", s)
            if not m:
                raise _Unresolved("age bands listed without values")
            width = int(m.group(1))
            self.result.assumptions.append(f"{width}-year age bands from 0 to 90+ were used.")
            return [AgeBand(label=f"{lo}-{lo + width - 1}", min=lo, max=lo + width - 1)
                    for lo in range(0, 90, width)] + [AgeBand(label="90+", min=90)]
        listing = REFERENCE_DATE_RE.sub("", listing)
        bands = []
        # "and" separates bands only before a number: "60-79 and 80+", not "80 and over".
        for _, token in _split_top_level(listing.strip().rstrip("."), r",|;|\s+and\s+(?=\d)"):
            t = token.strip()
            if m := re.fullmatch(r"(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})(?:\s*years?)?", t, I):
                bands.append(AgeBand(label=f"{m.group(1)}-{m.group(2)}", min=int(m.group(1)), max=int(m.group(2))))
            elif m := re.fullmatch(r"(\d{1,3})\s*(?:\+|years?\s+and\s+(?:over|older)|and\s+(?:over|older)|or\s+(?:over|older))", t, I):
                bands.append(AgeBand(label=t.replace(" ", "") if "+" in t else t, min=int(m.group(1))))
            elif m := re.fullmatch(r"(?:under|below|<|younger than)\s*(\d{1,3})(?:\s*years?)?", t, I):
                bands.append(AgeBand(label=t, min=0, max=int(m.group(1)) - 1))
            else:
                raise _Unresolved(f"age band {t!r} not understood")
        return bands

    def _parse_measurement_rule(self, s: str) -> None:
        atoms = [a for a in self._atoms(s) if a.kind == "measurement"]
        if not atoms:
            raise _Unresolved("no measurement named")
        loinc = next((a.loinc for a in atoms if a.key.startswith("code:")), atoms[0].loinc)
        if not re.search(r"\b(latest|most recent|last|newest)\b", s, I):
            raise _Unresolved("only the latest value per patient is supported")
        name_atom = next((a for a in atoms if not a.key.startswith("code:")), None)
        if name_atom is not None:
            self._add_concept(Concept(
                name=name_atom.text, definition=s, kind="measurement", loinc=loinc,
                keys=frozenset({f"measurement:{loinc}"})))
        self.result.assumptions.append(f"The latest value per patient is used for LOINC {loinc}.")
        if re.search(r"\b(left out|excluded|exclude|leave out|omitted|not counted|are dropped)\b", s, I):
            self.required_measurements.append(loinc)

    def _parse_count_rule(self, s: str) -> None:
        atoms = self._atoms(s)
        if any(a.kind == "visit" for a in atoms) or re.search(r"\b(stays|visits|encounters|admissions)\b", s, I):
            self.count_visits = True
        years = [a.years for a in atoms if a.kind == "year"]
        if years:
            self.visit_years = years[0]
        if not self.count_visits and not years:
            raise _Unresolved("counting rule not understood")

    # --------------------------------------------------------------- concepts
    def _add_concept(self, concept: Concept) -> None:
        names = [concept.name]
        if m := re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", concept.name):
            names = [m.group(1), m.group(2)]
        concept.patterns = [name_pattern(n) for n in names if n.strip()]
        self.concepts = [c for c in self.concepts if c.name.lower() != concept.name.lower()]
        self.concepts.append(concept)

    def _define(self, name: str, body: str, sentence: str) -> None:
        body = _clean_phrase(body)
        if not name:
            raise _Unresolved("definition without a name")
        concept = self._concept_from_body(name, body, sentence)
        self._add_concept(concept)

    def _concept_from_body(self, name: str, body: str, sentence: str) -> Concept:
        # Ratios: "A / B × 100"
        if re.search(r"\s/\s|\bdivided by\b", body, I):
            num, den = re.split(r"\s/\s|\bdivided by\b", body, maxsplit=1, flags=I)
            den = re.sub(r"\s*(?:[×x*]\s*100|times 100|in %|as a percentage).*$", "", den, flags=I)
            return Concept(name, sentence, "ratio", ratio=(num.strip(), den.strip()), keys=frozenset({f"ratio:{name.lower()}"}))

        atoms = self._atoms(body)
        kinds = {a.kind for a in atoms}
        type_m = re.search(r"\btype\s*(?:=|is|of)?\s*['\"‘’]?(inpatient|outpatient|emergency)['\"‘’]?", body, I)
        sex_m = re.search(r"\bsex\s*(?:=|is)?\s*['\"‘’]?(female|male)['\"‘’]?", body, I)

        if "visit" in kinds or type_m or re.search(r"\b(encounters?|visits?)\b", body, I) and "primary" in kinds:
            meaning = self._meaning(body, role="count", force_head_visit=True)
            visit = meaning.visit_head or EncounterFilter()
            if type_m:
                visit = visit.model_copy(update={"types": [type_m.group(1).lower()]})
            patient_level = [c for c in meaning.criteria if c.kind not in ("encounter", "has_encounter")]
            return Concept(name, sentence, "visit", criteria=patient_level, visit=visit,
                           keys=frozenset(meaning.keys | {f"concept:{name.lower()}"}))
        if sex_m and kinds <= {"attribute"}:
            crit = SexCriterion(values=[sex_m.group(1).lower()])
            return Concept(name, sentence, "attribute", criteria=[crit], keys=frozenset({f"sex:{sex_m.group(1).lower()}"}))

        code_atoms = [a for a in atoms if a.key.startswith("code:")]
        if code_atoms and all(a.kind == code_atoms[0].kind for a in code_atoms) and len(atoms) == len(code_atoms):
            a = code_atoms[0]
            codes = [c for x in code_atoms for c in (x.criteria[0].icd10_prefixes if x.kind == "condition"
                                                     else x.med.atc_prefixes if x.kind == "medication" else [])]
            if a.kind == "condition":
                crit = ConditionCriterion(icd10_prefixes=codes)
                return Concept(name, sentence, "condition", criteria=[crit], keys=frozenset({f"condition:{','.join(codes)}"}))
            if a.kind == "medication":
                med = MedicationCriterion(atc_prefixes=codes)
                return Concept(name, sentence, "medication", med=med, keys=frozenset({f"medication:{','.join(codes)}"}))
            if a.kind == "measurement":
                return Concept(name, sentence, "measurement", loinc=a.loinc, keys=frozenset({f"measurement:{a.loinc}"}))
        if not atoms:
            # Bare codes without a keyword: "any condition coded E11".
            bare = re.findall(r"\b([A-Z]\d{2}(?:\.\w{1,4})?|[A-Z]\d{2}[A-Z]{1,2}\d{0,2})\b", body)
            if bare and re.search(r"\b(conditions?|diagnos[ie]s|disease)\b", body, I):
                crit = ConditionCriterion(icd10_prefixes=bare)
                return Concept(name, sentence, "condition", criteria=[crit], keys=frozenset({f"condition:{','.join(crit.icd10_prefixes)}"}))
            if bare and re.search(r"\b(drugs?|medications?|prescriptions?)\b", body, I):
                med = MedicationCriterion(atc_prefixes=bare)
                return Concept(name, sentence, "medication", med=med, keys=frozenset({f"medication:{','.join(med.atc_prefixes)}"}))
            raise _Unresolved(f"definition of {name!r} not understood")
        if len(atoms) == 1 and atoms[0].kind == "medication":
            return Concept(name, sentence, "medication", med=atoms[0].med, keys=frozenset({atoms[0].key}))
        if len(atoms) == 1 and atoms[0].kind == "measurement":
            return Concept(name, sentence, "measurement", loinc=atoms[0].loinc, keys=frozenset({atoms[0].key}))
        meaning = self._meaning(body, role="count")
        if meaning.ratio:
            return Concept(name, sentence, "ratio", ratio=meaning.ratio, keys=frozenset(meaning.keys))
        kind = "attribute" if kinds <= {"attribute"} else "condition" if kinds == {"condition"} else "composite"
        return Concept(name, sentence, kind, criteria=meaning.criteria,
                       keys=frozenset(meaning.keys | {f"concept:{name.lower()}"}))

    # ----------------------------------------------------------------- atoms
    def _atoms(self, text: str) -> list[Atom]:
        candidates: list[Atom] = []
        for concept in self.concepts:
            for pattern in concept.patterns:
                for m in pattern.finditer(text):
                    candidates.append(self._concept_atom(concept, m.start(), m.end(), m.group(0)))
        candidates += self._code_atoms(text)
        candidates += self._age_atoms(text)
        candidates += self._year_atoms(text)
        for start, end, matched, term in self.terminology.find(text):
            candidates.append(self._term_atom(term, start, end, matched))

        candidates.sort(key=lambda a: (-(a.end - a.start), -a.priority, a.start))
        chosen: list[Atom] = []
        for a in candidates:
            if all(a.end <= b.start or a.start >= b.end for b in chosen):
                chosen.append(a)
        return sorted(chosen, key=lambda a: a.start)

    def _concept_atom(self, c: Concept, start: int, end: int, text: str) -> Atom:
        key = f"concept:{c.name.lower()}"
        base = dict(start=start, end=end, text=text, concept=c, priority=3)
        if c.kind == "condition":
            return Atom(kind="condition", key=next(iter(c.keys), key), criteria=list(c.criteria), **base)
        if c.kind == "medication":
            return Atom(kind="medication", key=next(iter(c.keys), key), med=c.med, **base)
        if c.kind == "measurement":
            return Atom(kind="measurement", key=f"measurement:{c.loinc}", loinc=c.loinc, **base)
        if c.kind == "visit":
            return Atom(kind="visit", key=key, visit=c.visit, criteria=list(c.criteria), **base)
        if c.kind == "ratio":
            return Atom(kind="ratio", key=key, ratio=c.ratio, **base)
        return Atom(kind="attribute" if c.kind == "attribute" else "composite", key=key,
                    criteria=list(c.criteria), **base)

    def _term_atom(self, term: Term, start: int, end: int, text: str) -> Atom:
        base = dict(start=start, end=end, text=text, priority=1)
        if term.kind == "condition":
            return Atom(kind="condition", key=f"condition:{','.join(term.codes)}",
                        criteria=[ConditionCriterion(icd10_prefixes=list(term.codes))], **base)
        if term.kind == "medication":
            return Atom(kind="medication", key=f"medication:{','.join(term.codes)}",
                        med=MedicationCriterion(atc_prefixes=list(term.codes)), **base)
        if term.kind == "measurement":
            return Atom(kind="measurement", key=f"measurement:{term.codes[0]}", loinc=term.codes[0], **base)
        if term.kind == "age":
            return Atom(kind="attribute", key=f"age:{term.age_min}-{term.age_max}",
                        criteria=[AgeCriterion(min=term.age_min, max=term.age_max)], **base)
        if term.kind == "sex":
            return Atom(kind="attribute", key=f"sex:{term.values[0]}",
                        criteria=[SexCriterion(values=list(term.values))], **base)
        if term.kind == "region":
            return Atom(kind="attribute", key=f"region:{term.values[0]}",
                        criteria=[RegionCriterion(values=list(term.values))], **base)
        if term.kind == "hospital":
            return Atom(kind="attribute", key=f"hospital:{term.values[0]}",
                        criteria=[DataHolderCriterion(values=list(term.values))], **base)
        return Atom(kind="visit", key=f"visit:{','.join(term.values)}",
                    visit=EncounterFilter(types=list(term.values) or None), **base)

    _CODE_KEYWORDS = [
        ("primary", re.compile(r"\bprimary_icd-?10\b|\b(?:main|primary|principal)\s+diagnos[ie]s\b(?:\s*\(\s*primary_icd-?10\s*\))?", I)),
        ("condition", re.compile(r"(?<!primary_)\bICD[- ]?10\b(?:[- ]?codes?)?", I)),
        ("medication", re.compile(r"\bATC\b(?:[- ]?codes?)?", I)),
        ("measurement", re.compile(r"\bLOINC\b(?:[- ]?codes?)?", I)),
    ]
    _CODE_TOKENS = {
        "primary": re.compile(r"[A-Z]\d{2}(?:\.\w{1,4})?", I),
        "condition": re.compile(r"[A-Z]\d{2}(?:\.\w{1,4})?", I),
        "medication": re.compile(r"[A-Z]\d{2}(?:[A-Z]{1,2}(?:\d{2})?)?", I),
        "measurement": re.compile(r"\d{1,7}-\d", I),
    }
    _FILLER = re.compile(
        r"\s+|,|\(|\)|=|:|/|\b(?:codes?|starting|starts|start|beginning|begins|with|of|in|or|and|prefix(?:es)?|"
        r"is|are|the|an?|any|category|categories|value|values|range|equal|to|coded|as)\b", I)

    def _code_atoms(self, text: str) -> list[Atom]:
        atoms = []
        for kind, kw in self._CODE_KEYWORDS:
            for m in kw.finditer(text):
                if kind == "condition" and text[max(0, m.start() - 8):m.start()].lower().endswith("primary_"):
                    continue
                codes, end = self._scan_codes(text, m.end(), self._CODE_TOKENS[kind], ranges=kind in ("condition", "primary"))
                if kind == "primary":
                    atoms.append(Atom(m.start(), end if codes else m.end(), text[m.start():end if codes else m.end()],
                                      "primary", key=f"primary:{','.join(codes)}", primary=codes, priority=2))
                    continue
                if not codes:
                    continue
                span = dict(start=m.start(), end=end, text=text[m.start():end], priority=2)
                if kind == "condition":
                    atoms.append(Atom(kind="condition", key=f"code:condition:{','.join(codes)}",
                                      criteria=[ConditionCriterion(icd10_prefixes=codes)], **span))
                elif kind == "medication":
                    atoms.append(Atom(kind="medication", key=f"code:medication:{','.join(codes)}",
                                      med=MedicationCriterion(atc_prefixes=codes), **span))
                else:
                    atoms.append(Atom(kind="measurement", key=f"code:measurement:{codes[0]}", loinc=codes[0], **span))
        return atoms

    def _scan_codes(self, text: str, pos: int, token: re.Pattern, ranges: bool) -> tuple[list[str], int]:
        codes: list[str] = []
        end = pos
        i = pos
        while i < len(text):
            m = token.match(text, i)
            if m and (m.end() == len(text) or not text[m.end()].isalnum()):
                code = m.group(0).upper()
                # A range such as E10-E14
                if ranges and codes and re.fullmatch(r"\s*(?:-|–|to)\s*", text[end:m.start()] or "") \
                        and codes[-1][0] == code[0] and len(codes[-1]) == 3 and len(code) == 3:
                    lo, hi = int(codes[-1][1:]), int(code[1:])
                    codes.extend(f"{code[0]}{n:02d}" for n in range(lo + 1, hi + 1))
                else:
                    codes.append(code)
                i = end = m.end()
                continue
            f = self._FILLER.match(text, i)
            if f and f.end() > i:
                i = f.end()
                continue
            if text[i] in "-–" and codes:
                i += 1
                continue
            break
        return codes, end

    _AGE_PATTERNS = [
        (re.compile(r"\b(?:aged?|age)\s+(\d{1,3})\s*(?:years?(?:\s+old)?\s*)?(?:or|and)\s+(?:older|over|above|more)\b", I), "min"),
        (re.compile(r"\b(\d{1,3})\s*years?(?:\s+old)?\s+(?:or|and)\s+(?:older|over|above)\b", I), "min"),
        (re.compile(r"\b(\d{1,3})\s+(?:or|and)\s+older\b", I), "min"),
        (re.compile(r"\b(?:aged?|age)\s+(?:at least)\s+(\d{1,3})\b", I), "min"),
        (re.compile(r"\b(?:aged?|age)\s+(\d{1,3})\s*\+", I), "min"),
        (re.compile(r"\b(?:aged?|age)\s+(?:over|above|older than|more than)\s+(\d{1,3})\b", I), "gt"),
        (re.compile(r"\bolder than\s+(\d{1,3})\b", I), "gt"),
        (re.compile(r"\b(?:aged?|age)\s+(?:under|below|younger than|less than)\s+(\d{1,3})\b", I), "lt"),
        (re.compile(r"\byounger than\s+(\d{1,3})\b", I), "lt"),
        (re.compile(r"\b(?:under|below)\s+(?:the\s+age\s+of\s+)?(\d{1,3})\s*years?\b", I), "lt"),
        (re.compile(r"\b(?:aged?|age)\s+(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})\b", I), "range"),
        (re.compile(r"\b(?:aged?|age)\s+between\s+(\d{1,3})\s+and\s+(\d{1,3})\b", I), "range"),
    ]

    def _age_atoms(self, text: str) -> list[Atom]:
        atoms = []
        for pattern, how in self._AGE_PATTERNS:
            for m in pattern.finditer(text):
                if how == "min":
                    crit = AgeCriterion(min=int(m.group(1)))
                elif how == "gt":
                    crit = AgeCriterion(min=int(m.group(1)) + 1)
                elif how == "lt":
                    crit = AgeCriterion(max=int(m.group(1)) - 1)
                else:
                    crit = AgeCriterion(min=int(m.group(1)), max=int(m.group(2)))
                atoms.append(Atom(m.start(), m.end(), m.group(0), "attribute",
                                  key=f"age:{crit.min}-{crit.max}", criteria=[crit], priority=2))
        return atoms

    _YEAR_PATTERNS = [
        (re.compile(r"\b(?:between|from)\s+((?:19|20)\d{2})\s+(?:and|to|until|through|till)\s+((?:19|20)\d{2})\b", I), "range"),
        (re.compile(r"\b(?:in|during)\s+((?:19|20)\d{2})\s*(?:-|–|to)\s*((?:19|20)\d{2})\b", I), "range"),
        (re.compile(r"\bsince\s+((?:19|20)\d{2})\b", I), "since"),
        (re.compile(r"\b(?:in|during|within)\s+(?:the\s+(?:calendar\s+)?year\s+)?((?:19|20)\d{2})\b(?![-–])", I), "year"),
    ]

    def _year_atoms(self, text: str) -> list[Atom]:
        atoms = []
        for pattern, how in self._YEAR_PATTERNS:
            for m in pattern.finditer(text):
                if how == "range":
                    years = _years(int(m.group(1)), int(m.group(2)))
                elif how == "since":
                    years = (date(int(m.group(1)), 1, 1), None)
                else:
                    years = _years(int(m.group(1)))
                atoms.append(Atom(m.start(), m.end(), m.group(0), "year", key=f"year:{years}", years=years, priority=2))
        return atoms

    # --------------------------------------------------------------- meaning
    def _meaning(self, text: str, role: str, force_head_visit: bool = False) -> "Meaning":
        atoms = self._atoms(text)
        meaning = Meaning(text=text)
        meaning.back_ref = bool(BACKREF_RE.search(text))
        visit_filters: list[tuple[bool, EncounterFilter, bool]] = []  # (is_head, filter, negate)
        head_decided = False
        primary_codes: list[str] = []
        pending_primary = False
        years: tuple | None = None
        prev_end = 0
        criteria: list = []
        last_kind = None

        for a in atoms:
            window = text[prev_end:a.start]
            neg = bool(NEGATION_RE.search(window))
            # "diabetes or hypertension", "ACE inhibitor or ARB": one criterion with both code lists.
            if (a.kind == last_kind and a.kind in ("condition", "medication") and criteria
                    and re.fullmatch(r"\s*(?:,\s*)?(?:or|and/or)\s+(?:an?\s+|the\s+)?", window, I)):
                last = criteria[-1]
                field_name = "icd10_prefixes" if a.kind == "condition" else "atc_prefixes"
                extra = a.criteria[0].icd10_prefixes if a.kind == "condition" else a.med.atc_prefixes
                merged = list(dict.fromkeys(getattr(last, field_name) + list(extra)))
                criteria[-1] = last.model_copy(update={field_name: merged})
                meaning.keys.add(a.key)
                prev_end = a.end
                continue
            meaning.keys.add(a.key)
            if a.concept is not None:
                meaning.keys |= set(a.concept.keys)
            if a.kind == "primary":
                if a.primary:
                    primary_codes += a.primary
                    meaning.keys.add(f"condition:{','.join(a.primary)}")
                else:
                    pending_primary = True
            elif a.kind == "condition":
                if pending_primary:
                    primary_codes += a.criteria[0].icd10_prefixes
                    pending_primary = False
                else:
                    criteria += [c.model_copy(update={"negate": neg}) for c in a.criteria]
            elif a.kind == "medication":
                cue = ACTIVE_RE.search(window)
                active = bool(cue) or self._default_active(a.text)
                criteria.append(a.med.model_copy(update={"active": active, "negate": neg}))
            elif a.kind == "measurement":
                if role == "mean" and meaning.measurement is None:
                    meaning.measurement = a.loinc
                    meaning.value_source = "observation"
                else:
                    comparator, threshold = self._threshold(text[a.end:])
                    which = "any" if re.search(r"\b(any|ever|at least one)\b", window, I) and comparator else "latest"
                    criteria.append(ObservationCriterion(loinc=a.loinc, comparator=comparator, threshold=threshold,
                                                         which=which, negate=neg))
            elif a.kind in ("attribute", "composite"):
                if neg and len(a.criteria) > 1:
                    meaning.warnings.append(f"negation of the combined definition {a.text!r} is not supported")
                criteria += [c.model_copy(update={"negate": neg}) if neg else c for c in a.criteria]
            elif a.kind == "visit":
                is_head = False
                if not head_decided:
                    prefix = text[:a.start]
                    is_head = force_head_visit or (role != "qualifier" and not LINK_RE.search(prefix)
                                                   and not re.search(rf"\b({PATIENT_HEADS})\b", prefix, I))
                    head_decided = True
                visit_filters.append((is_head, a.visit or EncounterFilter(), neg))
                criteria += list(a.criteria)
            elif a.kind == "year":
                years = a.years
            elif a.kind == "ratio":
                meaning.ratio = a.ratio
            prev_end = a.end
            last_kind = a.kind if a.kind in ("condition", "medication") and not pending_primary else None

        if pending_primary:
            meaning.warnings.append("a main diagnosis is mentioned without a code or known name")

        head = next((f for is_head, f, _ in visit_filters if is_head), None)
        if head is None and (self.count_visits and role != "qualifier") and (visit_filters or primary_codes or years):
            if visit_filters:
                _, first, _ = visit_filters[0]
                visit_filters[0] = (True, first, False)
                head = first
            else:
                head = EncounterFilter()
        if head is not None:
            meaning.unit = "encounter"
            update = {}
            if primary_codes:
                update["primary_icd10_prefixes"] = primary_codes
                primary_codes = []
            yrs = years or (self.visit_years if self.count_visits else None)
            if yrs:
                update["date_from"], update["date_to"] = yrs
                years = None
            head = head.model_copy(update=update)
            criteria.insert(0, EncounterCriterion(filter=head))
            meaning.visit_head = head
        else:
            meaning.visit_head = None
        for is_head, f, neg in visit_filters:
            if is_head:
                continue
            update = {}
            if primary_codes:
                update["primary_icd10_prefixes"], primary_codes = primary_codes, []
            if years:
                (update["date_from"], update["date_to"]), years = years, None
            criteria.append(HasEncounterCriterion(filter=f.model_copy(update=update), negate=neg))
        if primary_codes:
            criteria.append(HasEncounterCriterion(filter=EncounterFilter(types=["inpatient"], primary_icd10_prefixes=primary_codes)))
            self.result.assumptions.append(
                f"'{text}': main diagnosis {', '.join(primary_codes)} is read as an inpatient stay with that primary_icd10.")
        if years:
            meaning.warnings.append(f"the period in {text!r} could not be attached to visits and was not applied")

        if role == "mean" and meaning.value_source is None:
            if re.search(r"\blength of (?:hospital )?stay|duration of (?:the )?stay|days in hospital\b", text, I):
                meaning.value_source = "length_of_stay"
                if meaning.unit != "encounter":
                    meaning.unit = "encounter"
                    criteria.insert(0, EncounterCriterion(filter=EncounterFilter(types=["inpatient"])))
            elif re.search(r"\bage\b", text, I):
                meaning.value_source = "age"
        meaning.criteria = _dedupe(criteria)
        return meaning

    def _default_active(self, drug_text: str) -> bool:
        active = self.active_rule_stated or bool(re.search(rf"\bactive\b[^.]*{re.escape(drug_text)}", self.question, I))
        how = "active on 31 December 2025" if active else "ever prescribed"
        note = f"'{drug_text}' without 'active' is read as {how}."
        if note not in self.result.assumptions:
            self.result.assumptions.append(note)
        return active

    _THRESHOLD = re.compile(
        r"^\s*(?:values?\s+|levels?\s+|results?\s+)?(?:of\s+|is\s+|was\s+)?"
        r"(?P<op>above|over|greater than|more than|higher than|>=|≥|>|at least|below|under|less than|lower than|<=|≤|<|at most|equal to|=|of)?\s*"
        r"(?P<num>\d+(?:[.,]\d+)?)\s*(?:%|kg/m2|kg/m²|mmhg|mm\[hg\]|ml/min\S*)?\s*"
        r"(?P<tail>or (?:more|higher|above|over|greater|less|lower|below|under|fewer))?", I)
    _OPS = {"above": ">", "over": ">", "greater than": ">", "more than": ">", "higher than": ">", ">": ">",
            ">=": ">=", "≥": ">=", "at least": ">=", "below": "<", "under": "<", "less than": "<",
            "lower than": "<", "<": "<", "<=": "<=", "≤": "<=", "at most": "<=", "equal to": "=", "=": "="}

    def _threshold(self, after: str):
        m = self._THRESHOLD.match(after)
        if not m or not (m.group("op") or m.group("tail")):
            return None, None
        op = (m.group("op") or "").lower()
        tail = (m.group("tail") or "").lower()
        if tail:
            comparator = ">=" if any(w in tail for w in ("more", "higher", "above", "over", "greater")) else "<="
        elif op in self._OPS:
            comparator = self._OPS[op]
        else:
            return None, None
        from decimal import Decimal
        return comparator, Decimal(m.group("num").replace(",", "."))

    # ------------------------------------------------------------ statistics
    def _report_from_question(self) -> str | None:
        """Without a Report line, read the numbers and groups from a simple question."""
        q = re.sub(r"\s+", " ", self.question.strip().rstrip("?. "))
        groups = r"(?:,\s*)?\s+(?P<dims>(?:by|per|in each|for each|broken down by|split by)\s+.+)$"
        patterns = [
            (rf"^how many (?P<what>.+?){groups}", "{what}"),
            (rf"^what is the (?:mean|average) (?P<value>.+?) (?:of|among|in|for) (?P<what>.+?){groups}",
             "{what} and their mean {value}"),
            (rf"^what (?:share|proportion|percentage|fraction) of (?P<what>.+?) (?P<verb>have|has|are|is|take|takes|receive|receives|get|use|uses) "
             rf"(?P<cond>.+?){groups}", "{what}, how many of them {verb} {cond} and that share in %"),
        ]
        for pattern, template in patterns:
            m = re.match(pattern, q, I)
            if m:
                fields = {k: v for k, v in m.groupdict().items() if v}
                what = re.sub(r"\s+(?:have|has|are|had|take|took)\s+", " with ", fields.get("what", ""), flags=I)
                fields["what"] = what
                report = f"Report {fields['dims']}: " + template.format(**fields)
                self.result.assumptions.append(f"No Report line; numbers read from the question as: '{report}'.")
                return report
        return None

    def _report_parts(self) -> tuple[str, str]:
        if self.report is None:
            self.report = self._report_from_question()
        if self.report is None:
            raise _Unresolved("No 'Report ...' line: the numbers to produce are not stated.")
        s = re.sub(r"^(report|show|give|provide|list|return|present)\b\s*", "", self.report, flags=I)
        header, sep, body = s.partition(":")
        if not sep:
            header, body = "", s
        return header.strip(), body.strip()

    def _dimensions(self, header: str) -> list[Dimension]:
        text = header
        m = re.search(r"\b(?:per|by|for each|in each|for every|broken down by|split by|stratified by)\s+(.+)$", header, I)
        if not m:
            q = re.search(r"\b(?:per|by|in each|for each|broken down by)\s+((?:the\s+)?(?:patient'?s\s+)?(?:\d+[\s-]*year\s+)?[a-z ,'\-]+?)(?:\?|$|\s+and\s+what|\s+in\s+)",
                          self.question, I)
            if not q:
                return []
            text = q.group(1)
            self.result.assumptions.append(f"Groups taken from the question: '{text.strip()}'.")
        else:
            text = m.group(1)
        dims: list[Dimension] = []
        for _, part in _split_top_level(text, r",|\s+and\s+|\s*&\s*|\s+x\s+|\s+×\s+"):
            part = re.sub(r"^(?:the\s+)?(?:patient'?s\s+)?", "", part.strip(), flags=I)
            for pattern, kind in DIMENSION_WORDS:
                if pattern.search(part):
                    dims.append(self._dimension(kind, part))
                    break
            else:
                raise _Unresolved(f"grouping '{part}' is not supported (supported: region, hospital, sex, age band, visit type, year)")
        return dims

    def _dimension(self, kind: str, label: str) -> Dimension:
        if kind == "age_band":
            if not self.bands:
                m = re.search(r"(\d+)[\s-]*year", label + " " + self.question)
                width = int(m.group(1)) if m else 10
                self.bands = [AgeBand(label=f"{lo}-{lo + width - 1}", min=lo, max=lo + width - 1)
                              for lo in range(0, 90, width)] + [AgeBand(label="90+", min=90)]
                self.result.assumptions.append(f"No age bands were listed; {width}-year bands from 0 to 90+ were used.")
            return Dimension(kind="age_band", label="age_band", bands=self.bands)
        return Dimension(kind=kind, label=kind)

    def _metric_phrases(self, body: str) -> list[str]:
        body = _clean_phrase(body)
        starters = (r"(?:the|a|an|mean|average|median|%|percent|percentage|share|proportion|number|count|how many|"
                    r"their|that|this|those|of them|total|" + PATIENT_HEADS + r"|stays?|visits?|encounters?|admissions?)\b|%")
        concept_heads = [c.name for c in self.concepts if c.kind in ("attribute", "composite", "ratio", "visit")]
        starter_re = re.compile(r"^\s*(?:" + starters + "".join(
            "|" + p.pattern.replace("(?<![\\w-])", "") for c in self.concepts
            if c.name in concept_heads for p in c.patterns) + ")", I)
        phrases: list[str] = []
        for sep, piece in _split_top_level(body, r",\s*(?:and\s+)?|\s+and\s+|;\s*"):
            if phrases and sep and not starter_re.match(piece):
                phrases[-1] = f"{phrases[-1]}{sep}{piece}" if sep.strip() else f"{phrases[-1]} {piece}"
            else:
                phrases.append(piece)
        out = []
        for p in phrases:
            p = _clean_phrase(p)
            if not p or re.fullmatch(r"(?:in\s+)?%|(?:one|1) decimals?", p, I):
                continue
            out.append(p)
        return out

    def _statistics(self) -> QuerySpec | None:
        header, body = self._report_parts()
        if PATIENT_LEVEL_RE.search(header) or PATIENT_LEVEL_RE.search(body):
            self.result.patient_level_output = True
            raise _Unresolved("The Report line asks for patient-level rows; statistics cannot contain them.")
        dims = self._dimensions(header)
        phrases = self._metric_phrases(body)
        if not phrases:
            raise _Unresolved("The Report line names no numbers.")

        metrics: list[Metric] = []
        hidden: list[Metric] = []
        names: set[str] = set()
        meanings: list[Meaning] = []
        base: Metric | None = None
        prev_count: Metric | None = None

        def unique(name: str) -> str:
            n, k = name, 2
            while n in names:
                n, k = f"{name}_{k}", k + 1
            names.add(n)
            return n

        def find_or_add_count(criteria: list, unit: str, label: str) -> Metric:
            key = _criteria_key(criteria)
            for m in metrics + hidden:
                if m.kind == "count" and m.unit == unit and _criteria_key(m.criteria) == key:
                    return m
            m = Metric(name=unique("n_" + _slug(_strip_count_words(label))), label=label, kind="count",
                       unit=unit, criteria=criteria, visible=False)
            hidden.append(m)
            return m

        for index, phrase in enumerate(phrases):
            if UNSUPPORTED_STAT_RE.search(phrase):
                raise _Unresolved(f"'{phrase}': only counts, percentages and means are supported")
            if MEAN_RE.search(phrase):
                kind = "mean"
            elif PERCENT_RE.search(phrase) or any(a.kind == "ratio" for a in self._atoms(phrase)):
                kind = "percentage"
            else:
                kind = "count"
            # A phrase should ask for exactly one number. Two kinds of cue (or two means, or two
            # shares) means the split went wrong; flag it instead of silently dropping a number.
            mean_cues = len(MEAN_RE.findall(phrase))
            share_cues = len(re.findall(r"\b(?:percent|percentage|share|proportion|prevalence|fraction|rate)\b", phrase, I))
            if (mean_cues and share_cues) or mean_cues > 1 or share_cues > 1:
                self.result.unresolved.append(
                    f"'{phrase}' seems to ask for more than one number; only one was produced. Staff should check.")
            meaning = self._meaning(phrase, role=kind)
            meanings.append(meaning)
            for w in meaning.warnings:
                self.result.unresolved.append(f"'{phrase}': {w}")
            label = phrase[0].upper() + phrase[1:]

            if kind == "count":
                criteria = meaning.criteria
                if meaning.back_ref and prev_count is not None:
                    criteria = _dedupe(list(prev_count.criteria) + criteria)
                if re.search(r"\bsize of (?:the )?(?:study )?(?:cohort|population)\b", phrase, I):
                    name = "cohort_size"
                else:
                    name = _slug(_strip_count_words(phrase)) or "count"
                m = Metric(name=unique(name), label=label, kind="count",
                           unit=meaning.unit, criteria=criteria)
                metrics.append(m)
                base = base or m
                prev_count = m
            elif kind == "percentage":
                if meaning.ratio:
                    num_m = self._meaning(meaning.ratio[0], role="count")
                    den_m = self._meaning(meaning.ratio[1], role="count")
                    meaning.keys |= num_m.keys | den_m.keys
                    num = find_or_add_count(num_m.criteria, num_m.unit, meaning.ratio[0])
                    den = find_or_add_count(den_m.criteria, den_m.unit, meaning.ratio[1])
                    stem = _slug(re.sub(r"^(?:the|a|an)\s+", "", phrase, flags=I))
                elif meaning.criteria:
                    if base is None:
                        raise _Unresolved(f"'{phrase}': a percentage needs a group to divide by")
                    reference = prev_count if meaning.back_ref and prev_count else base
                    num = find_or_add_count(_dedupe(list(reference.criteria) + meaning.criteria),
                                            reference.unit, f"{reference.label} {phrase.lstrip('% ')}")
                    den = base
                    stem = _slug(re.sub(r"^(?:%|percent(?:age)?|share|proportion)?\s*(?:of\s+\w+\s+)?(?:with|who|having|on|taking)?\s*(?:an?|the)?\s*",
                                        "", phrase, flags=I)) or num.name
                else:
                    if base is None or prev_count is None or prev_count is base:
                        raise _Unresolved(f"'{phrase}': cannot tell which numbers the percentage divides")
                    num, den = prev_count, base
                    stem = num.name
                    self.result.assumptions.append(f"'{phrase}' = {prev_count.label} / {base.label} × 100.")
                stem = re.sub(r"_?(?:pct|in_pct|percent|percentage)$", "", stem)
                metrics.append(Metric(name=unique(f"{stem}_pct"), label=label, kind="percentage",
                                      unit=num.unit, numerator=num.name, denominator=den.name))
            else:  # mean
                if meaning.value_source is None:
                    raise _Unresolved(f"'{phrase}': cannot tell what to average")
                reference = prev_count if (meaning.back_ref and prev_count) else base
                criteria = list(reference.criteria) if reference else []
                if reference is None and not meaning.criteria:
                    # "mean BMI and mean HbA1c of adults with diabetes": the population is named once,
                    # in a later phrase, and applies to both.
                    later = next((self._meaning(nxt, role="mean") for nxt in phrases[index + 1:]
                                  if self._meaning(nxt, role="mean").criteria), None)
                    if later is None:
                        raise _Unresolved(f"'{phrase}': the patients to average over are not stated")
                    criteria = list(later.criteria)
                    self.result.assumptions.append(
                        f"'{phrase}' uses the patients named later in the Report line ({later.text}).")
                criteria = _dedupe(criteria + meaning.criteria)
                if meaning.value_source == "observation":
                    value = ValueSource(source="observation", loinc=meaning.measurement)
                    stem = "mean_" + _slug(re.sub(r"^.*?\b(?:mean|average)\s+", "", phrase, flags=I))
                else:
                    value = ValueSource(source=meaning.value_source)
                    stem = "mean_" + ("age" if meaning.value_source == "age" else "length_of_stay_days")
                unit = "encounter" if meaning.value_source == "length_of_stay" else (reference.unit if reference else meaning.unit)
                metrics.append(Metric(name=unique(stem), label=label, kind="mean", unit=unit,
                                      criteria=criteria, value=value))

        # Restrictions stated in the question (or definitions) but not in the Report line.
        covered = set().union(*(m.keys for m in meanings)) if meanings else set()
        restrictions = self._question_restrictions(covered, self._codes_used(metrics + hidden))
        for loinc in self.required_measurements:
            restrictions.append(ObservationCriterion(loinc=loinc))
        all_metrics = []
        for m in metrics + hidden:
            if m.kind in ("count", "mean") and restrictions:
                extra = [c for c in restrictions if not (m.unit == "patient" and c.kind == "encounter")]
                m = m.model_copy(update={"criteria": _dedupe(extra + list(m.criteria))})
            all_metrics.append(m)
        return QuerySpec(dimensions=dims, metrics=all_metrics, medication_active_rule=self.active_rule)

    @staticmethod
    def _codes_used(metrics: list) -> set[str]:
        """Every ICD-10 / ATC prefix the numbers already use, including main diagnoses of stays."""
        codes: set[str] = set()
        for m in metrics:
            for c in m.criteria:
                codes |= set(getattr(c, "icd10_prefixes", None) or [])
                codes |= set(getattr(c, "atc_prefixes", None) or [])
                f = getattr(c, "filter", None)
                if f is not None and f.primary_icd10_prefixes:
                    codes |= set(f.primary_icd10_prefixes)
        return codes

    def _question_restrictions(self, covered: set[str], codes_used: set[str]) -> list:
        """Qualifiers that appear only in the question.

        Applied automatically: age, sex, region and hospital, and concepts the request defines
        (e.g. a defined "Breast cancer" the Report line does not repeat). A diagnosis or drug
        named only in the question is not added on a guess; it is listed for staff instead.
        """
        restrictions = []
        question_atoms = self._atoms(self.question)
        prev_end = 0
        for a in question_atoms:
            window = self.question[prev_end:a.start]
            prev_end = a.end
            keys = {a.key} | (set(a.concept.keys) if a.concept else set())
            if keys & covered or a.kind in ("measurement", "visit", "ratio", "year", "primary"):
                continue
            atom_codes = set()
            if a.kind == "condition":
                atom_codes = {p for c in a.criteria for p in getattr(c, "icd10_prefixes", [])}
            elif a.kind == "medication":
                atom_codes = set(a.med.atc_prefixes)
            if atom_codes and any(x.startswith(y) or y.startswith(x) for x in atom_codes for y in codes_used):
                continue  # already used by the numbers (e.g. as the main diagnosis of a stay)
            if a.concept is None and a.kind in ("condition", "medication", "composite"):
                self.result.unresolved.append(
                    f"'{a.text}' in the question is not part of the Report line or the definitions, so it was "
                    "not applied. Staff should check whether the numbers should be restricted to it.")
                continue
            neg = bool(NEGATION_RE.search(window))
            if a.kind == "medication":
                crits = [a.med.model_copy(update={"active": bool(ACTIVE_RE.search(window)) or self._default_active(a.text),
                                                  "negate": neg})]
            else:
                crits = [c.model_copy(update={"negate": neg}) if neg else c for c in a.criteria]
            restrictions += crits
            self.result.assumptions.append(
                f"'{a.text}' from the question is applied to every number (the Report line does not repeat it).")
        return restrictions

    # ---------------------------------------------------------------- cohort
    def _cohort(self) -> CohortSpec:
        criteria = None
        for c in self.concepts:
            if re.fullmatch(r"(?:the\s+)?(?:study\s+)?(?:cohort|population|study population)", c.name, I):
                criteria = list(c.criteria)
                break
        q = self.question
        split = re.search(rf"\b(?:for|of|on|about)\s+(?:all\s+)?(?:the\s+)?(?=(?:{PATIENT_HEADS})\b|(?:\w+\s+){{0,3}}(?:{PATIENT_HEADS})\b)", q, I)
        records_text = q[:split.start()] if split else q
        population_text = q[split.end():] if split else ""
        population_text = re.split(r"\.\s|\.$", population_text)[0]
        if criteria is None:
            if not population_text:
                raise _Unresolved("the patients to include are not stated")
            criteria = self._meaning(population_text, role="qualifier").criteria
            self.result.assumptions.append(f"SPE cohort taken from the question: '{population_text.strip()}'.")
        if any(c.kind == "encounter" for c in criteria):
            criteria = [HasEncounterCriterion(filter=c.filter, negate=c.negate) if c.kind == "encounter" else c
                        for c in criteria]
        restrictions = []
        for loinc in self.required_measurements:
            restrictions.append(ObservationCriterion(loinc=loinc))
        criteria = _dedupe(criteria + restrictions)

        records: dict[str, RecordSelection] = {}
        measurements = [a.loinc for a in self._atoms(records_text) if a.kind == "measurement"]
        for pattern, table in RECORD_WORDS:
            if pattern.search(records_text):
                records[table] = RecordSelection(table=table)
        if measurements:
            if "observations" not in records:
                records["observations"] = RecordSelection(table="observations", loinc=sorted(set(measurements)))
        columns = [col for pattern, col in DEMOGRAPHIC_COLUMNS if pattern.search(records_text)]
        if columns or "patients" in records:
            records["patients"] = RecordSelection(
                table="patients", columns=columns or ["sex", "birth_date", "region", "data_holder"])
        if not records:
            if re.search(r"\brecords?\b", records_text, I):
                for table in ("conditions", "medications", "observations", "encounters"):
                    records[table] = RecordSelection(table=table)
                self.result.assumptions.append("'Records' without a type: all record types are included; staff should check data minimisation.")
            else:
                raise _Unresolved("the record types to provide are not stated")
        order = ["patients", "conditions", "medications", "observations", "encounters"]
        return CohortSpec(criteria=criteria, records=[records[t] for t in order if t in records],
                          medication_active_rule=self.active_rule)
