"""Optional LLM fallback for definitions the rule parser cannot interpret.

Off by default. Set EHDS_LLM to choose:

* ``off``   (default) rules only.
* ``cache`` rules, then answers already stored in ``llm_cache/``; never calls the network.
* ``on``    rules, then the cache, then Claude for anything still not interpreted.
            The answer is stored in the cache, so later runs give the same result.

Only the request text (purpose, question, definitions) and the data dictionary
(column names, category values, code lists) are sent. No patient-level data,
counts or query results ever leave the app. The model must answer in the
QuerySpec / CohortSpec JSON schema; the reply is validated exactly like rule
output, and the engine still checks every code and value against the data.

Current Claude models do not accept a temperature setting, so consistency
comes from the structured output schema plus the cache: a request text is
interpreted once and the stored interpretation is reused on every later run.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from .parser import ParsedRequest, RuleParser
from .spec import CohortSpec, QuerySpec
from .store import Vocabulary

MODEL = os.environ.get("EHDS_LLM_MODEL", "claude-opus-5-5")
PROMPT_VERSION = "1"
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "llm_cache"


class LLMInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statistics: QuerySpec | None = None
    cohort: CohortSpec | None = None
    assumptions: list[str] = []
    cannot_interpret: list[str] = []


SYSTEM = """You translate requests to a European Health Data Space access body into a structured query spec.
Use only the JSON schema you are given. Rules:
- The reference date is 31 December 2025; age = 2025 - birth year.
- A diagnosis is a row in conditions.csv (criterion kind "condition"). Use the main diagnosis of a stay
  (encounter filter primary_icd10_prefixes) only when the request names a main/primary diagnosis or primary_icd10.
- Codes are prefixes: ICD-10 for conditions, ATC for medications, LOINC for measurements.
- Every number is a metric of kind count, percentage (numerator and denominator are names of count metrics,
  which may be hidden with visible=false) or mean (latest value per patient). Repeat the population criteria
  on every metric that needs them.
- Count stays (unit "encounter") only when the request says to count stays/visits rather than patients.
- Put anything you cannot express in cannot_interpret instead of guessing, and list every assumption you make.
- Only fill cohort for applications asking for pseudonymised records.
"""


def _data_dictionary(v: Vocabulary) -> str:
    return json.dumps({
        "regions": v.regions, "hospitals (data_holder)": v.data_holders, "sex": v.sexes,
        "visit types": v.encounter_types, "visit years": v.encounter_years,
        "ICD-10 codes in conditions.csv": v.icd10, "ICD-10 codes used as main diagnosis": v.primary_icd10,
        "ATC codes": v.atc, "LOINC codes": {k: n for k, (n, _u) in v.loinc.items()},
    }, ensure_ascii=False)


def request_text(request: dict) -> dict:
    """The only part of a request that may be sent to the LLM."""
    return {k: request.get(k) for k in ("requested_as", "requested_format", "purpose", "question", "definitions")}


def cache_key(request: dict) -> str:
    payload = json.dumps({"model": MODEL, "prompt": PROMPT_VERSION, "request": request_text(request)},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class LLMClient:
    def __init__(self, vocabulary: Vocabulary, cache_dir: Path | str = DEFAULT_CACHE, mode: str | None = None):
        self.vocabulary = vocabulary
        self.cache_dir = Path(cache_dir)
        self.mode = (mode or os.environ.get("EHDS_LLM", "off")).lower()

    def interpret(self, request: dict, problems: list[str]) -> tuple[LLMInterpretation | None, str]:
        if self.mode not in ("cache", "on"):
            return None, "off"
        path = self.cache_dir / f"{cache_key(request)}.json"
        if path.exists():
            return LLMInterpretation.model_validate_json(path.read_text(encoding="utf-8")), "llm-cache"
        if self.mode != "on":
            return None, "cache-miss"
        try:
            result = self._call(request, problems)
        except Exception as exc:  # network, auth or validation problem: fall back to rules
            return None, f"llm-error: {type(exc).__name__}"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result, "llm"

    def _call(self, request: dict, problems: list[str]) -> LLMInterpretation:
        import anthropic

        client = anthropic.Anthropic()
        content = (
            "Data dictionary (no patient data):\n" + _data_dictionary(self.vocabulary)
            + "\n\nRequest:\n" + json.dumps(request_text(request), ensure_ascii=False, indent=1)
            + "\n\nThe rule-based parser could not interpret: " + json.dumps(problems, ensure_ascii=False)
        )
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": content}],
            output_format=LLMInterpretation,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise ValueError("no usable interpretation")
        return response.parsed_output


class HybridParser:
    """Rules first; the LLM (or its cached answer) only for what the rules left unresolved."""

    def __init__(self, rules: RuleParser, llm: LLMClient):
        self.rules = rules
        self.llm = llm

    def parse(self, request: dict) -> ParsedRequest:
        parsed = self.rules.parse(request)
        needs_help = parsed.unresolved and not parsed.patient_level_output
        if not needs_help:
            return parsed
        interpretation, how = self.llm.interpret(request, parsed.unresolved)
        if interpretation is None:
            if how not in ("off",):
                parsed.assumptions.append(f"LLM fallback not used ({how}).")
            return parsed
        try:
            stats = QuerySpec.model_validate(interpretation.statistics.model_dump()) if interpretation.statistics else None
        except ValidationError:
            stats = None
        if stats is None and parsed.statistics is None and interpretation.cohort is None:
            return parsed
        return ParsedRequest(
            statistics=stats or parsed.statistics,
            cohort=interpretation.cohort or parsed.cohort,
            patient_level_output=parsed.patient_level_output,
            assumptions=parsed.assumptions + [f"Interpreted with {MODEL} ({how}); staff should confirm."]
                        + interpretation.assumptions,
            unresolved=list(interpretation.cannot_interpret),
            concepts=parsed.concepts,
            method=how,
        )
