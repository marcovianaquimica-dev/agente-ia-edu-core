"""Engine output contract, artifact version v1 (spec v1.0 §18).

Layer 1 of validation: shape. Everything checkable without the rubric and without
the essay text lives here - field types, the six-value score scale, the total
being the sum of five competencies, and anchor consistency. The rubric-aware and
text-aware checks are layers 2 and 3, in services/essay_engine_validation.py.

Scores are OPTIONAL on purpose. In an institution configured as FORMATIVO the
engine produces analysis and no grade at all, and that output is valid. What is
not valid is a partial score: three competencies out of five means the engine
failed, not that it was being careful.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "essay_engine_output_v1"
COMPETENCY_CODES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")
OFFICIAL_LEVEL_POINTS: tuple[int, ...] = (0, 40, 80, 120, 160, 200)

CompetencyCode = Literal["C1", "C2", "C3", "C4", "C5"]
AnchorMode = Literal["TEXT_OFFSET", "IMAGE_REGION"]

_Strict = ConfigDict(extra="forbid")


class TextOffsetAnchor(BaseModel):
    """Verifiable anchor: the quote can be checked against the canonical text."""

    model_config = _Strict

    type: Literal["TEXT_OFFSET"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _end_follows_start(self) -> "TextOffsetAnchor":
        if self.end <= self.start:
            raise ValueError("TEXT_OFFSET anchor requires end > start")
        return self


class ImageRegionAnchor(BaseModel):
    """Weaker anchor: only the page bounds are verifiable. ``read_text`` is what
    the model says it read in that region and cannot be checked against anything."""

    model_config = _Strict

    type: Literal["IMAGE_REGION"]
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    read_text: str = Field(min_length=1)


Anchor = Annotated[
    Union[TextOffsetAnchor, ImageRegionAnchor], Field(discriminator="type")
]


class Identification(BaseModel):
    model_config = _Strict

    essay_id: UUID
    essay_version_id: UUID
    rubric_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    contract_version: Literal["essay_engine_output_v1"]
    anchor_mode: AnchorMode


class CompetencyScore(BaseModel):
    model_config = _Strict

    points: int
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _points_on_the_official_scale(self) -> "CompetencyScore":
        if self.points not in OFFICIAL_LEVEL_POINTS:
            raise ValueError(
                f"points must be one of {list(OFFICIAL_LEVEL_POINTS)}; got {self.points}"
            )
        return self


class Scores(BaseModel):
    model_config = _Strict

    per_competency: dict[str, CompetencyScore]
    total: int

    @model_validator(mode="after")
    def _all_five_and_total_is_the_sum(self) -> "Scores":
        if tuple(sorted(self.per_competency)) != COMPETENCY_CODES:
            raise ValueError(
                f"scores must cover exactly {list(COMPETENCY_CODES)}; "
                f"got {sorted(self.per_competency)}"
            )
        expected = sum(score.points for score in self.per_competency.values())
        if self.total != expected:
            raise ValueError(
                f"total must be the sum of the five competencies ({expected}); "
                f"got {self.total}"
            )
        return self


class CompetencyRationale(BaseModel):
    model_config = _Strict

    competency_code: CompetencyCode
    summary: str
    strengths: str = Field(min_length=1)
    growth_area: str = Field(min_length=1)
    signal_keys: tuple[str, ...] = ()


class Annotation(BaseModel):
    model_config = _Strict

    letter: str = Field(pattern=r"^[A-Z]{1,2}$")
    competency_code: CompetencyCode
    kind: Literal["ACERTO", "ATENCAO", "MELHORIA"]
    evidence_kind: Literal["LOCALIZED", "GLOBAL"]
    anchor: Anchor | None = None
    short_comment: str = Field(min_length=1)
    long_comment: str = Field(min_length=1)
    pedagogical_suggestion: str | None = None
    signal_keys: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _anchor_required_when_localized(self) -> "Annotation":
        if self.evidence_kind == "LOCALIZED" and self.anchor is None:
            raise ValueError(
                f"annotation {self.letter!r} has evidence_kind=LOCALIZED but no anchor"
            )
        return self


class Rewrite(BaseModel):
    model_config = _Strict

    letter: str = Field(pattern=r"^[A-Z]{1,2}$")
    competency_code: CompetencyCode
    original: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    pedagogical_goal: str = Field(min_length=1)


class Feedback(BaseModel):
    model_config = _Strict

    strengths: tuple[str, ...] = ()
    improvements: tuple[str, ...] = ()
    next_essay_strategy: str = Field(min_length=1)


class InterventionBreakdown(BaseModel):
    """C5 decomposed into verifiable elements (spec §4)."""

    model_config = _Strict

    agente: str | None = None
    acao: str | None = None
    meio_modo: str | None = None
    finalidade: str | None = None
    detalhamento: str | None = None
    respeita_direitos_humanos: bool


class Alert(BaseModel):
    model_config = _Strict

    code: Literal[
        "FUGA_AO_TEMA",
        "TIPO_TEXTUAL",
        "TEXTO_INSUFICIENTE",
        "OCR_DUVIDOSO",
        "POSSIVEL_DUPLICIDADE",
    ]
    detail: str | None = None


class MechanicalOccurrence(BaseModel):
    """A confirmed mechanical error, quoted from the essay (spec §2 — the C1
    checklist's dynamic rows). Never invented to pad the list: an empty
    ``mechanical_review`` tuple on ``EssayEngineOutput`` is valid and expected
    for essays with no confirmed errors of these kinds."""

    model_config = _Strict

    category: Literal[
        "ORTOGRAFIA",
        "ACENTUACAO",
        "CRASE",
        "PORQUES",
        "CONCORDANCIA",
        "REGENCIA",
        "PONTUACAO",
    ]
    excerpt: str = Field(min_length=1)
    suggested_form: str = Field(min_length=1)
    rule_explanation: str = Field(min_length=1)


class EssayEngineOutput(BaseModel):
    model_config = _Strict

    identification: Identification
    scores: Scores | None = None
    rationales: tuple[CompetencyRationale, ...]
    annotations: tuple[Annotation, ...]
    rewrites: tuple[Rewrite, ...] = ()
    feedback: Feedback
    intervention: InterventionBreakdown
    alerts: tuple[Alert, ...] = ()
    intro_message: str = Field(min_length=1)
    closing_message: str = Field(min_length=1)
    mechanical_review: tuple[MechanicalOccurrence, ...] = ()

    @model_validator(mode="after")
    def _anchors_agree_with_declared_mode(self) -> "EssayEngineOutput":
        declared = self.identification.anchor_mode
        for annotation in self.annotations:
            if annotation.anchor is not None and annotation.anchor.type != declared:
                raise ValueError(
                    f"annotation {annotation.letter!r} anchors on "
                    f"{annotation.anchor.type} but the output declares {declared}"
                )
        return self


__all__ = [
    "Alert",
    "Annotation",
    "Anchor",
    "CONTRACT_VERSION",
    "COMPETENCY_CODES",
    "CompetencyRationale",
    "CompetencyScore",
    "EssayEngineOutput",
    "Feedback",
    "Identification",
    "ImageRegionAnchor",
    "InterventionBreakdown",
    "MechanicalOccurrence",
    "OFFICIAL_LEVEL_POINTS",
    "Rewrite",
    "Scores",
    "TextOffsetAnchor",
]
