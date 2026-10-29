import json

from ehds.llm import HybridParser, LLMClient, LLMInterpretation, cache_key, request_text
from ehds.parser import RuleParser
from ehds.spec import ConditionCriterion, Dimension, Metric, QuerySpec
from ehds.terminology import build_terminology

UNCLEAR = {"id": "X", "requested_as": "health data request", "purpose": "research",
           "question": "How many patients have asthma, per postcode area?",
           "definitions": ["Report per postcode area: patients with asthma."], "applicant": {"name": "secret"}}


def test_only_request_text_is_sent():
    assert set(request_text(UNCLEAR)) == {"requested_as", "requested_format", "purpose", "question", "definitions"}


def test_off_mode_never_uses_llm(store, tmp_path):
    hp = HybridParser(RuleParser(build_terminology(store.vocabulary)), LLMClient(store.vocabulary, tmp_path, "off"))
    parsed = hp.parse(UNCLEAR)
    assert parsed.method == "rules" and parsed.statistics is None


def test_cached_answer_is_used_without_network(store, tmp_path):
    cached = LLMInterpretation(statistics=QuerySpec(dimensions=[Dimension(kind="region")], metrics=[
        Metric(name="asthma", label="Patients with asthma", kind="count",
               criteria=[ConditionCriterion(icd10_prefixes=["J45"])])]),
        assumptions=["postcode is not in the data; grouped by region instead"])
    (tmp_path / f"{cache_key(UNCLEAR)}.json").write_text(cached.model_dump_json())
    hp = HybridParser(RuleParser(build_terminology(store.vocabulary)), LLMClient(store.vocabulary, tmp_path, "cache"))
    parsed = hp.parse(UNCLEAR)
    assert parsed.method == "llm-cache"
    assert parsed.statistics.metrics[0].criteria[0].icd10_prefixes == ["J45"]
    assert any("staff should confirm" in a for a in parsed.assumptions)


def test_cache_miss_falls_back_to_rules(store, tmp_path):
    hp = HybridParser(RuleParser(build_terminology(store.vocabulary)), LLMClient(store.vocabulary, tmp_path, "cache"))
    parsed = hp.parse(UNCLEAR)
    assert parsed.method == "rules" and any("cache-miss" in a for a in parsed.assumptions)
