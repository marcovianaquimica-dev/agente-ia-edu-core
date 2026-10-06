"""Engine output contract, artifact version v6 (structured C2/C3 feedback with input reliability).

Layer 1 of validation: shape. Everything checkable without the rubric and without
the essay text lives here - field types, the six-value score scale, the total
being the sum of five competencies, and anchor consistency. The rubric-aware and
text-aware checks are layers 2 and 3, in services/essay_engine_validation.py.

Scores are OPTIONAL on purpose. In an institution configured as FORMATIVO the
engine produces analysis and no grade at all, and that output is valid. What is
not valid is a partial score: three competencies out of five means the engine
failed, not that it was being careful.

Shape change from v4
---------------------
Competências II and III stop being free-running prose in ``rationales`` and
become eight named fields at the top of the output:

* ``c2_tipologia_textual`` / ``c2_tema`` / ``c2_repertorio_sociocultural`` /
  ``c2_orientacao_melhoria`` - C2 read as "Tipologia, tema e repertório";
* ``c3_projeto_argumentativo`` / ``c3_fatos_informacoes_opinioes`` /
  ``c3_autoria`` / ``c3_orientacao_melhoria`` - C3 read as "Projeto
  argumentativo e autoria".

The teacher who asked for this wanted the student to see WHICH aspect of C2/C3
the feedback is about, instead of one undifferentiated paragraph per
competency. A free-text field cannot carry that distinction reliably (the
model writes whatever order it feels like), so the distinction moved into the
schema, where the renderer can label each aspect.

Two validators enforce the new shape, both conditioned on ``scores`` being
present (SCORING_MODE=AVALIATIVO):

* all eight fields must be present and non-blank - a partially-structured C2
  would render half a table, worse than the free-text fallback it replaced;
* ``rationales`` must not carry C2 or C3 - the same content written twice (once
  generic, once structured) is what this version exists to stop. C1, C4 and C5
  keep their CompetencyRationale exactly as v4 had it, summary/strengths/
  growth_area unchanged.

In FORMATIVO (``scores is None``) neither validator runs: that mode already
produces a deliberately partial output, and failing it over feedback shape
would reject corrections that carry real pedagogical content.

Nothing else changed. Alert codes, anchors, annotations, rewrites,
mechanical_review's seven categories and the intervention breakdown are v4's,
byte for byte. The crase/pontuação improvement that shipped alongside this
version is PROMPT text only (essay_prompts/v15.py's MECHANICAL_REVIEW_RULES) -
``MechanicalOccurrence.rule_explanation`` already existed and already carried
a per-occurrence explanation, so no schema change was needed or made.

A correction persisted under ``essay_engine_output_v1`` .. ``..._v4`` must stay
readable by the schema it was born with, so this is a NEW module, never an edit
to v4.py - the same rule ``classification_prompts`` and ``essay_prompts``
follow.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "essay_engine_output_v6"
COMPETENCY_CODES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")
OFFICIAL_LEVEL_POINTS: tuple[int, ...] = (0, 40, 80, 120, 160, 200)

#: The competencies whose feedback moved out of ``rationales`` into the
#: structured fields below. Kept as data so the service, the renderer and the
#: tests all name the same set.
STRUCTURED_COMPETENCY_CODES: tuple[str, ...] = ("C2", "C3")

#: The eight structured feedback fields, in the order they are rendered.
STRUCTURED_FEEDBACK_FIELDS: tuple[str, ...] = (
    "c2_tipologia_textual",
    "c2_tema",
    "c2_repertorio_sociocultural",
    "c2_orientacao_melhoria",
    "c3_projeto_argumentativo",
    "c3_fatos_informacoes_opinioes",
    "c3_autoria",
    "c3_orientacao_melhoria",
)

CompetencyCode = Literal["C1", "C2", "C3", "C4", "C5"]
AnchorMode = Literal["TEXT_OFFSET", "IMAGE_REGION"]

_Strict = ConfigDict(extra="forbid")


class TextOffsetAnchor(BaseModel):
    """Verifiable anchor: the quote can be checked against the canonical text.

    ``start``/``end`` are NOT required to be internally consistent here
    (``end`` may be <= ``start``) - confirmed live 2026-09-29: the model
    occasionally reports self-contradictory offsets for an otherwise
    correctly-chosen, verbatim quote. Rejecting the whole correction at this
    shape layer over one annotation's arithmetic mistake would throw away a
    good correction; instead, essay_engine_validation.py's anchoring layer
    (``_resolve_text_offset``) treats the quote - which the model copies
    reliably - as authoritative and re-derives the real offsets from it,
    the same recovery already relied on for offsets that drifted by a few
    characters (see that function's docstring). Only when the quote itself
    cannot be found unambiguously does that layer reject the annotation.
    """

    model_config = _Strict

    type: Literal["TEXT_OFFSET"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1)


class ImageRegionAnchor(BaseModel):
    """Weaker anchor: only page/line bounds are verifiable. ``read_text`` is
    what the model says it read on that line and cannot be checked against
    anything. Line-based, not pixel-based - see v2's module docstring."""

    model_config = _Strict

    type: Literal["IMAGE_REGION"]
    page: int = Field(ge=1)
    line: int = Field(ge=1)
    total_lines: int = Field(ge=1)
    read_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _line_within_total(self) -> "ImageRegionAnchor":
        if self.line > self.total_lines:
            raise ValueError(
                f"line {self.line} exceeds total_lines {self.total_lines}"
            )
        return self


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
    contract_version: Literal["essay_engine_output_v6"]
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
        # total is redundant with per_competency (each score independently
        # constrained to OFFICIAL_LEVEL_POINTS above) and the model
        # sometimes gets its own arithmetic wrong reporting it - confirmed
        # live 2026-09-29 (reported 800, the five competencies actually summed
        # to 760). per_competency is what carries the AI's real judgment, so
        # total is always recomputed from it instead of rejecting an
        # otherwise-good correction over the model's redundant arithmetic.
        self.total = sum(score.points for score in self.per_competency.values())
        return self


class CompetencyRationale(BaseModel):
    """Free-text rationale, now only for C1, C4 and C5 - see the module
    docstring. Shape is v4's, unchanged."""

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
        "TANGENCIAMENTO_AO_TEMA",
        "TIPO_TEXTUAL",
        "TIPO_TEXTUAL_PREDOMINANTE",
        "TEXTO_INSUFICIENTE",
        "ANULACAO_PROPOSITAL",
        "PARTE_DESCONECTADA_DO_TEMA",
        "IDENTIFICACAO_INDEVIDA",
        "LINGUA_ESTRANGEIRA",
        "TEXTO_ILEGIVEL",
        "OCR_DUVIDOSO",
        "POSSIVEL_DUPLICIDADE",
    ]
    detail: str | None = None


class MechanicalOccurrence(BaseModel):
    """A confirmed mechanical error, quoted from the essay (spec §2 — the C1
    checklist's dynamic rows). Never invented to pad the list: an empty
    ``mechanical_review`` tuple on ``EssayEngineOutput`` is valid and expected
    for essays with no confirmed errors of these kinds.

    Unchanged from v4 on purpose: v15's demand that a CRASE explanation name
    the pronoun type, and a PONTUACAO explanation name the syntactic
    construction, is carried by ``rule_explanation``'s own text. A closed
    taxonomy of pronoun/comma types would force real borderline cases into the
    wrong bucket - see the spec's §1 decision 3."""

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


class InputReliability(BaseModel):
    """Separa tecnicamente confiabilidade de ENTRADA de merito pedagogico
    (spec Fase B - 'falha ou baixa confiabilidade da entrada nao e
    deficiencia do aluno'). Nunca usar Alert/alerts para isto - Alert e
    julgamento pedagogico sobre o conteudo, este e julgamento sobre a
    LEITURA do texto, uma pergunta anterior e de natureza diferente."""

    model_config = _Strict

    status: Literal["RELIABLE", "USABLE_WITH_WARNING", "UNRELIABLE_NEEDS_REVIEW"]
    rationale: str = Field(min_length=1)


class EssayEngineOutput(BaseModel):
    model_config = _Strict

    identification: Identification
    input_reliability: InputReliability
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

    # Competência II - "Tipologia, tema e repertório"
    c2_tipologia_textual: str | None = None
    c2_tema: str | None = None
    c2_repertorio_sociocultural: str | None = None
    c2_orientacao_melhoria: str | None = None

    # Competência III - "Projeto argumentativo e autoria"
    c3_projeto_argumentativo: str | None = None
    c3_fatos_informacoes_opinioes: str | None = None
    c3_autoria: str | None = None
    c3_orientacao_melhoria: str | None = None

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

    @model_validator(mode="after")
    def _structured_feedback_complete_when_scored(self) -> "EssayEngineOutput":
        if self.scores is None:
            return self
        for field in STRUCTURED_FEEDBACK_FIELDS:
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{field} is required and must be non-blank when scores are present"
                )
        return self

    @model_validator(mode="after")
    def _rationales_exclude_structured_competencies_when_scored(
        self,
    ) -> "EssayEngineOutput":
        if self.scores is None:
            return self
        for rationale in self.rationales:
            if rationale.competency_code in STRUCTURED_COMPETENCY_CODES:
                raise ValueError(
                    f"rationales must not include {rationale.competency_code} - "
                    "its feedback belongs to the structured fields "
                    f"({', '.join(STRUCTURED_FEEDBACK_FIELDS)})"
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
    "InputReliability",
    "InterventionBreakdown",
    "MechanicalOccurrence",
    "OFFICIAL_LEVEL_POINTS",
    "Rewrite",
    "STRUCTURED_COMPETENCY_CODES",
    "STRUCTURED_FEEDBACK_FIELDS",
    "Scores",
    "TextOffsetAnchor",
]
