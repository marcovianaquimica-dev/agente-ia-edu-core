import pytest
from pydantic import ValidationError

from agente_ia_edu.essay_engine_contract.v6 import CONTRACT_VERSION, InputReliability


def test_contract_version_is_v6():
    assert CONTRACT_VERSION == "essay_engine_output_v6"


def test_input_reliability_accepts_the_three_statuses():
    for status in ("RELIABLE", "USABLE_WITH_WARNING", "UNRELIABLE_NEEDS_REVIEW"):
        InputReliability(status=status, rationale="motivo qualquer")


def test_input_reliability_rejects_unknown_status():
    with pytest.raises(ValidationError):
        InputReliability(status="ALGO_INVALIDO", rationale="motivo")


def test_input_reliability_requires_non_blank_rationale():
    with pytest.raises(ValidationError):
        InputReliability(status="RELIABLE", rationale="")
