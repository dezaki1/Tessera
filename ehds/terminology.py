"""Plain-English names for diagnoses, drugs, measurements, places and visit types.

Requests may name things in words ("SGLT2 inhibitor", "HbA1c", "Vienna")
instead of codes. This module maps those words to ICD-10, ATC and LOINC
prefixes and to the category values used in the data. It contains public
coding knowledge only; the per-code descriptions and drug names from the data
files are added as extra names when the store is loaded.

A code written in the request always wins over a name looked up here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .store import Vocabulary

TermKind = Literal[
    "condition", "medication", "measurement", "age", "sex", "region", "hospital", "visit"
]


@dataclass(frozen=True)
class Term:
    kind: TermKind
    codes: tuple[str, ...] = ()  # ICD-10 / ATC prefixes, or a single LOINC code
    values: tuple[str, ...] = ()  # sex / region / hospital / visit types
    age_min: int | None = None
    age_max: int | None = None
    label: str = ""  # canonical name shown to users


# name -> term. Names are matched as whole words, case-insensitively, with an
# optional plural; names in UPPER CASE of up to five letters are matched
# case-sensitively so that "AF" does not match "af" inside other words.
_CONDITIONS: dict[str, tuple[str, ...]] = {
    "type 2 diabetes|type 2 diabetes mellitus|type II diabetes|diabetes type 2|T2D|T2DM|non-insulin-dependent diabetes": ("E11",),
    "type 1 diabetes|type 1 diabetes mellitus|type I diabetes|diabetes type 1|T1D|T1DM": ("E10",),
    "diabetes|diabetes mellitus|diabetic": ("E10", "E11", "E12", "E13", "E14"),
    "obesity|obese": ("E66",),
    "depression|depressive episode|depressive disorder|major depression": ("F32", "F33"),
    "hypertension|high blood pressure|arterial hypertension|hypertensive": ("I10", "I11", "I12", "I13", "I15"),
    "essential hypertension|primary hypertension": ("I10",),
    "atrial fibrillation|atrial fibrillation and flutter|AF|AFib": ("I48",),
    "heart failure|cardiac failure|congestive heart failure|HF": ("I50",),
    "stroke": ("I63", "I64"),
    "ischaemic stroke|ischemic stroke|cerebral infarction": ("I63",),
    "COPD|chronic obstructive pulmonary disease|chronic obstructive lung disease|smokers' lung disease|smoker's lung disease": ("J44",),
    "asthma": ("J45",),
    "chronic kidney disease|CKD|chronic renal failure|chronic renal disease": ("N18",),
    "breast cancer|breast carcinoma|malignant neoplasm of breast|cancer of the breast": ("C50",),
    "pneumonia": ("J12", "J13", "J14", "J15", "J16", "J17", "J18"),
    "appendicitis|acute appendicitis": ("K35",),
    "urinary tract infection|UTI": ("N39.0",),
    "femur fracture|femoral fracture|fracture of femur|fractured femur|hip fracture|broken hip": ("S72",),
}

_MEDICATIONS: dict[str, tuple[str, ...]] = {
    "SGLT2 inhibitor|SGLT-2 inhibitor|SGLT2i|gliflozin": ("A10BK",),
    "GLP-1 receptor agonist|GLP-1 agonist|GLP-1 RA|GLP-1 analogue|GLP1 receptor agonist": ("A10BJ",),
    "biguanide": ("A10BA",),
    "insulin": ("A10A",),
    "long-acting insulin|basal insulin": ("A10AE",),
    "antidiabetic|antidiabetic drug|diabetes medication|glucose-lowering drug|blood glucose lowering drug": ("A10",),
    "oral antidiabetic|non-insulin glucose-lowering drug": ("A10B",),
    "vitamin K antagonist|VKA|coumarin": ("B01AA",),
    "factor Xa inhibitor|direct factor Xa inhibitor": ("B01AF",),
    "DOAC|NOAC|direct oral anticoagulant|novel oral anticoagulant": ("B01AE", "B01AF"),
    "oral anticoagulant|OAC|oral anticoagulation": ("B01AA", "B01AE", "B01AF"),
    "anticoagulant|anticoagulation": ("B01AA", "B01AB", "B01AE", "B01AF", "B01AX"),
    "diuretic": ("C03",),
    "loop diuretic|high-ceiling diuretic": ("C03C",),
    "beta blocker|beta-blocker|beta-adrenergic blocker": ("C07",),
    "selective beta blocker|cardioselective beta blocker": ("C07AB",),
    "calcium channel blocker|calcium antagonist|CCB": ("C08",),
    "dihydropyridine|dihydropyridine calcium channel blocker": ("C08CA",),
    "ACE inhibitor|ACE-inhibitor|ACEi|ACE-I|angiotensin-converting enzyme inhibitor|angiotensin converting enzyme inhibitor": ("C09AA",),
    "ARB|angiotensin receptor blocker|angiotensin II receptor blocker|angiotensin II receptor antagonist|angiotensin receptor antagonist|sartan": ("C09CA",),
    "RAS inhibitor|RAAS inhibitor|renin-angiotensin system inhibitor": ("C09",),
    "statin|HMG-CoA reductase inhibitor": ("C10AA",),
    "lipid-lowering drug|lipid lowering drug|lipid-modifying agent": ("C10",),
    "SSRI|selective serotonin reuptake inhibitor": ("N06AB",),
    "antidepressant": ("N06A",),
    "inhaled corticosteroid and long-acting beta agonist|ICS/LABA|ICS-LABA": ("R03AK",),
    "long-acting muscarinic antagonist|LAMA|inhaled anticholinergic": ("R03BB",),
    "inhaler|drug for obstructive airway disease": ("R03",),
}

_MEASUREMENTS: dict[str, str] = {
    "HbA1c|Hb A1c|HbA1C|A1c|glycated haemoglobin|glycated hemoglobin|glycosylated haemoglobin|glycosylated hemoglobin|haemoglobin A1c|hemoglobin A1c": "4548-4",
    "BMI|body mass index": "39156-5",
    "systolic blood pressure|systolic BP|SBP|systolic pressure": "8480-6",
    "eGFR|GFR|estimated glomerular filtration rate|glomerular filtration rate|kidney function|renal function": "62238-1",
}

_AGE = {
    "adult|grown-up": (18, None),
    "child|children|minor|paediatric patient|pediatric patient|kid": (None, 17),
}

_SEX = {
    "woman|women|female|girl|lady": "female",
    "man|men|male|boy|gentleman": "male",
}

_REGIONS = {
    "Burgenland": "Burgenland",
    "Kärnten|Kaernten|Karnten|Carinthia": "Kärnten",
    "Niederösterreich|Niederoesterreich|Lower Austria": "Niederösterreich",
    "Oberösterreich|Oberoesterreich|Upper Austria": "Oberösterreich",
    "Salzburg": "Salzburg",
    "Steiermark|Styria": "Steiermark",
    "Tirol|Tyrol": "Tirol",
    "Vorarlberg": "Vorarlberg",
    "Wien|Vienna": "Wien",
}

_HOSPITALS = {
    "Klinikum Nord|Klinikum North": "Klinikum Nord",
    "Klinikum Süd|Klinikum Sued|Klinikum Sud|Klinikum South": "Klinikum Süd",
    "Klinikum West": "Klinikum West",
}

_VISITS = {
    "inpatient stay|inpatient admission|hospital stay|hospitalisation|hospitalization|admission|inpatient episode|stay|inpatient": ("inpatient",),
    "emergency visit|emergency department visit|emergency room visit|ED visit|ER visit|emergency": ("emergency",),
    "outpatient visit|outpatient appointment|outpatient": ("outpatient",),
    "visit|encounter": (),  # any type
}

_IRREGULAR_PLURALS = {"woman": "women", "man": "men", "child": "children", "person": "people",
                      "diagnosis": "diagnoses", "gentleman": "gentlemen", "lady": "ladies"}


def name_pattern(name: str) -> re.Pattern:
    """Whole-word regex for a name, allowing a plural and hyphen/space variation."""
    words = re.split(r"[\s]+", name.strip())
    last = words[-1]
    lower = last.lower()
    if lower in _IRREGULAR_PLURALS:
        tail = f"(?:{re.escape(last)}|{re.escape(_IRREGULAR_PLURALS[lower])})"
    elif lower in _IRREGULAR_PLURALS.values():
        tail = re.escape(last)
    elif re.search(r"(s|x|z|ch|sh)$", lower):
        tail = re.escape(last) + "(?:es)?"
    elif re.search(r"[^aeiou]y$", lower):
        tail = re.escape(last[:-1]) + "(?:y|ies)"
    else:
        tail = re.escape(last) + "(?:s|'s)?"
    parts = [re.escape(w).replace(r"\-", r"[\s-]?") for w in words[:-1]] + [tail.replace(r"\-", r"[\s-]?")]
    body = r"[\s-]+".join(parts)
    case_sensitive = name.isupper() and len(name.replace("-", "")) <= 5
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", flags)


@dataclass
class Terminology:
    entries: list[tuple[str, re.Pattern, Term]] = field(default_factory=list)

    def add(self, names: str, term: Term) -> None:
        for name in names.split("|"):
            self.entries.append((name, name_pattern(name), term))

    def find(self, text: str) -> list[tuple[int, int, str, Term]]:
        """All matches (start, end, matched text, term), longest first at each position."""
        hits = []
        for _, pattern, term in self.entries:
            for m in pattern.finditer(text):
                hits.append((m.start(), m.end(), m.group(0), term))
        return hits

    def lookup(self, name: str) -> Term | None:
        """Exact name lookup (whole string)."""
        for _, pattern, term in self.entries:
            m = pattern.fullmatch(name.strip())
            if m:
                return term
        return None


def build_terminology(vocabulary: Vocabulary) -> Terminology:
    t = Terminology()
    for names, codes in _CONDITIONS.items():
        t.add(names, Term("condition", codes=codes, label=names.split("|")[0]))
    for names, codes in _MEDICATIONS.items():
        t.add(names, Term("medication", codes=codes, label=names.split("|")[0]))
    for names, code in _MEASUREMENTS.items():
        t.add(names, Term("measurement", codes=(code,), label=names.split("|")[0]))
    for names, (lo, hi) in _AGE.items():
        t.add(names, Term("age", age_min=lo, age_max=hi, label=names.split("|")[0]))
    for names, value in _SEX.items():
        t.add(names, Term("sex", values=(value,), label=value))
    for names, value in _REGIONS.items():
        if value in vocabulary.regions:
            t.add(names, Term("region", values=(value,), label=value))
    for names, value in _HOSPITALS.items():
        if value in vocabulary.data_holders:
            t.add(names, Term("hospital", values=(value,), label=value))
    for names, types in _VISITS.items():
        t.add(names, Term("visit", values=types, label=names.split("|")[0]))

    # Names that appear in the data itself: exact code descriptions and drug names.
    for code, description in vocabulary.icd10.items():
        t.add(_clean(description), Term("condition", codes=(code,), label=description))
    for code, name in vocabulary.atc.items():
        for part in {name, *name.split("/")}:
            t.add(_clean(part), Term("medication", codes=(code,), label=name))
    for code, (name, _unit) in vocabulary.loinc.items():
        t.add(_clean(name), Term("measurement", codes=(code,), label=name))
    return t


def _clean(name: str) -> str:
    return re.sub(r"[|]", " ", name).strip()


def known_code_prefixes(prefixes: list[str], codes: list[str] | tuple[str, ...]) -> bool:
    return any(code.startswith(p) for p in prefixes for code in codes)
