"""Engine output contract, artifact version v4 (remaining ANULA_REDACAO codes).

Layer 1 of validation: shape. Everything checkable without the rubric and without
the essay text lives here - field types, the six-value score scale, the total
being the sum of five competencies, and anchor consistency. The rubric-aware and
text-aware checks are layers 2 and 3, in services/essay_engine_validation.py.

Scores are OPTIONAL on purpose. In an institution configured as FORMATIVO the
engine produces analysis and no grade at all, and that output is valid. What is
not valid is a partial score: three competencies out of five means the engine
failed, not that it was being careful.

Shape change from v3
---------------------
v3 wired up three of the cartilha's nine ANULA_REDACAO conditions (p. 9-10 and
p. 28 of rubrics/enem_2025.yaml's scoring_rules): fuga total ao tema, texto
insuficiente, predominância de outro tipo textual. This version adds alert
codes for five of the remaining six:

* ANULACAO_PROPOSITAL - impropérios, desenhos e outras formas propositais de
  anulação (scoring_rules key `anulacao_proposital`);
* PARTE_DESCONECTADA_DO_TEMA - reflexões sobre a prova/o próprio desempenho,
  bilhetes à banca, mensagens políticas/religiosas/de protesto, frases sem
  relação com o tema (key `parte_desconectada_do_tema`);
* IDENTIFICACAO_INDEVIDA - nome, assinatura, rubrica ou outra forma de
  identificação do(a) participante dentro do corpo do texto, fora do espaço
  destinado a isso (key `identificacao_indevida`);
* LINGUA_ESTRANGEIRA - texto predominante ou integralmente em língua
  estrangeira (key `lingua_estrangeira`);
* TEXTO_ILEGIVEL - texto que impossibilita a leitura por avaliadores
  independentes (key `texto_ilegivel`) - a stronger, official-zero-grade
  judgment call, deliberately kept distinct from OCR_DUVIDOSO (this engine's
  own everyday transcription-uncertainty flag, which carries no scoring
  consequence at all - conflating "I'm not 100% sure I read every word" with
  "this cannot be assessed" would zero far more essays than the cartilha
  intends).

The sixth remaining condition, `texto_em_branco` ("Em Branco" - no text at
all on the answer sheet), gets no alert code: the submission API already
requires non-empty text for both TYPED (essay_submissions.py's
create_essay_submission) and the reviewed_text a PHOTO/PDF submission
confirms with (PageReviewRequest.reviewed_text, min_length=1) - a truly
blank submission cannot reach the engine at all, so a code for it would be
dead: never triggerable, never tested against a real case.

All five new codes join the existing _ANULA_REDACAO_ALERT_CODES set in
essay_correction.py - each is a whole-essay zero, same enforcement path v3
already built for fuga_ao_tema/texto_insuficiente/tipo_textual_predominante.
This is purely additive to the Alert.code Literal - nothing else in the
shape changed, and no previously-valid alert code stopped being valid.

Shape change from v2 (unchanged from v3's own docstring, kept for history)
---------------------------------------------------------------------------
Auditing the official ENEM 2025 rubric (rubrics/enem_2025.yaml's scoring_rules,
sourced cell-by-cell from the INEP cartilha) against what the engine actually
does turned up rules that exist as DATA - transcribed, hash-verified against
the source PDF - but were never wired into either the prompt or the scoring
code: a total zero for `fuga_ao_tema`/`nao_atendimento_ao_tipo_textual`/
`texto_insuficiente`, a zero on Competência V alone for
`desrespeito_aos_direitos_humanos`, and a hard 40-point ceiling on Competências
III and V for `tangenciamento_teto_c3`/`tangenciamento_teto_c5`. The alert
vocabulary this contract already had (v1's Alert.code) could not express two
of the distinctions those rules depend on:

* "fuga total ao tema" (nem o assunto amplo nem o tema especifico foram
  desenvolvidos - a ZERO condition) versus merely tangenciando it (a much
  softer condition, worth a 40-point ceiling on III and V, not a zero) were
  both being reported as the same FUGA_AO_TEMA code;
* "predominância de outro tipo textual" (also a ZERO condition, p. 28 of the
  cartilha) versus an essay that is still predominantly dissertative-
  argumentative but shows some characteristics of another type (no zero -
  the cartilha says this is "penalizada na Competência II" through C2's own
  descriptor, which already mentions this) were both TIPO_TEXTUAL.

v3 added TANGENCIAMENTO_AO_TEMA and TIPO_TEXTUAL_PREDOMINANTE as their own
codes, so a deterministic scoring step (essay_correction.py's
_apply_deterministic_scoring_rules) can apply the right official consequence
instead of guessing from an ambiguous code - and so FUGA_AO_TEMA and
TIPO_TEXTUAL keep meaning exactly what they meant before, now unambiguously.

Shape change from v1 (unchanged from v2/v3's own docstring, kept for history)
---------------------------------------------------------------------------
v1's ImageRegionAnchor asked the model for x/y/width/height in pixels. Confirmed
live (2026-09-25) across multiple real submissions and prompt revisions
(essay_prompts v3 even told the model the page's real pixel dimensions): the
model consistently returned generic, round-number coordinates unrelated to the
real position of the quoted text - a systematic failure to ground continuous
pixel estimates, not occasional noise (retries reproduced the same pattern).
This version replaces x/y/width/height with line/total_lines: the model reports
which line of visible text the quote is on (1-based from the top, matching a
ruled page's own printed line numbers when present) and how many lines it
counts on the page total - an ordinal counting task the model performs far
more reliably than continuous spatial estimation. The caller converts
line/total_lines into a full-width horizontal band for rendering (see
services/essay_pdf_export.py and web/essay-annotations.js), which also removes
the horizontal (x) position - a second axis the model was never reliable at
either - from what has to be estimated at all.

A correction persisted under ``essay_engine_output_v1``, ``..._v2`` or
``..._v3`` must stay readable by the schema it was born with, so this is a
NEW module, never an edit to v3.py - the same rule ``classification_prompts``
and ``essay_prompts``
follow.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "essay_engine_output_v4"
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
    """Weaker anchor: only page/line bounds are verifiable. ``read_text`` is
    what the model says it read on that line and cannot be checked against
    anything. Line-based, not pixel-based - see module docstring."""

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
    contract_version: Literal["essay_engine_output_v4"]
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
