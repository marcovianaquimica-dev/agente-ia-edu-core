import pytest
from pydantic import ValidationError
from uuid import uuid4

from agente_ia_edu.essay_engine_contract.v6 import (
    CONTRACT_VERSION,
    InputReliability,
    Identification,
    EssayEngineOutput,
    Scores,
    CompetencyScore,
    CompetencyRationale,
    Annotation,
    Feedback,
    InterventionBreakdown,
)


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


def test_essay_engine_output_accepts_v6_contract_version():
    """Regression test: ensure EssayEngineOutput can be constructed with v6 contract_version.

    This catches the bug where Identification.contract_version was still Literal["essay_engine_output_v5"]
    even after v6.py was created, causing validation failures for any caller setting the v6 version.
    """
    essay_id = uuid4()
    essay_version_id = uuid4()

    identification = Identification(
        essay_id=essay_id,
        essay_version_id=essay_version_id,
        rubric_version="v1",
        model_version="gpt-4",
        prompt_version="v1",
        engine_version="v1",
        contract_version="essay_engine_output_v6",
        anchor_mode="TEXT_OFFSET",
    )

    input_reliability = InputReliability(
        status="RELIABLE",
        rationale="Essay text is clear and readable",
    )

    # Minimal valid EssayEngineOutput with v6 contract_version
    output = EssayEngineOutput(
        identification=identification,
        input_reliability=input_reliability,
        rationales=(
            CompetencyRationale(
                competency_code="C1",
                summary="Summary",
                strengths="Strengths text",
                growth_area="Growth area text",
            ),
        ),
        annotations=(),
        feedback=Feedback(next_essay_strategy="Strategy text"),
        intervention=InterventionBreakdown(respeita_direitos_humanos=True),
        intro_message="Intro",
        closing_message="Closing",
    )

    # If this passes, the contract_version field correctly accepts v6
    assert output.identification.contract_version == "essay_engine_output_v6"
    assert output.input_reliability.status == "RELIABLE"
