import json

from agente_ia_edu.essay_prompts import get_essay_prompt
from agente_ia_edu.essay_prompts.v16 import VERSION, RESPONSE_SCHEMA, build_prompt


def test_version_is_v16():
    assert VERSION == "essay_correction_v16"


def test_response_schema_has_input_reliability():
    assert "input_reliability" in RESPONSE_SCHEMA
    assert "status" in RESPONSE_SCHEMA["input_reliability"]


def test_build_prompt_mentions_input_reliability_rules():
    prompt = build_prompt(
        anchor_mode="TEXT_OFFSET", essay_statement="tema qualquer",
        rubric={}, include_scores=True, text="redacao qualquer",
    )
    assert "INPUT_RELIABILITY_RULES" in prompt
    assert "UNRELIABLE_NEEDS_REVIEW" in prompt
    assert "TEXTO_INSUFICIENTE" in prompt  # distingue explicitamente os dois conceitos


def test_registry_resolves_v16():
    artifact = get_essay_prompt("essay_correction_v16")
    assert artifact.version == "essay_correction_v16"
    assert json.dumps(artifact.response_schema)  # serializavel


def test_system_policy_requires_portuguese_for_input_reliability_rationale():
    """SYSTEM_POLICY's PT-BR enumeration must name the new free-text field.

    v15's own precedent (see module docstring) was to update this same
    enumeration by one clause every time a new free-text field group was
    added, so the model has an explicit instruction covering its language -
    otherwise input_reliability.rationale could drift into English or the
    essay's own foreign language, which LINGUA_ESTRANGEIRA/
    INPUT_RELIABILITY_RULES both already treat as a real scenario.
    """
    prompt = build_prompt(
        anchor_mode="TEXT_OFFSET", essay_statement="tema qualquer",
        rubric={}, include_scores=True, text="redacao qualquer",
    )
    marker = "Escreva todo texto livre da resposta ("
    start = prompt.index(marker) + len(marker)
    end = prompt.index(")", start)
    pt_br_field_list = prompt[start:end]
    assert "input_reliability.rationale" in pt_br_field_list
