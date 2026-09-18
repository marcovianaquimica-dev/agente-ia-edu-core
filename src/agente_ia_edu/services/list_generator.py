"""PHASE 14 - Question List / Exercise Generator (deterministic, AI-agnostic).

Pipeline:

    QuestionSelection  (PHASE 12/13 - ordered official question_version_ids)
        -> ListConfiguration   (title / instructions / activity mode / answer-key mode)
        -> GeneratedListDefinition   (in-memory, validated, deterministic ordering)

The generator operates ONLY on immutable ``question_version_id`` references. It
never copies or mutates the official question, statement, options or answer. It
reuses :class:`QuestionBankService` for validation and batch retrieval - it adds
no bank-query logic of its own.

Answer-key data is authoritative: it comes from ``AnswerKeyEntry`` (official
revision) with the option ``is_valid_option`` flag as a cross-check. It is NEVER
inferred from AI, text similarity, curriculum code or option ordering. Official
step-by-step resolutions are not present in the current schema, so a resolution
is represented as *unavailable* rather than invented.

This module imports no provider, no OpenAI SDK, and nothing under
``agente_ia_edu.providers`` / ``classification_prompts`` / ``ai_classification_service``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    QuestionOption,
)
from agente_ia_edu.services.question_bank import QuestionBankItem, QuestionBankService

# ---------------------------------------------------------------------------
# Configuration vocabulary
# ---------------------------------------------------------------------------

ACTIVITY_MODE_EXERCISE_LIST = "EXERCISE_LIST"
#: Implemented now. The others are reserved so the model/API is forward-compatible
#: (see PHASE 14 report - they are NOT implemented in this phase).
SUPPORTED_ACTIVITY_MODES = (ACTIVITY_MODE_EXERCISE_LIST,)
RESERVED_ACTIVITY_MODES = (
    "SIMULADO", "PROVA", "TAREFA", "DIAGNOSTICO", "PRATICA_INDIVIDUAL",
)

ANSWER_KEY_NONE = "NONE"
ANSWER_KEY_AT_END = "KEY_AT_END"
ANSWER_KEY_AND_RESOLUTION_AT_END = "KEY_AND_RESOLUTION_AT_END"
ANSWER_KEY_PRESENTATIONS = (
    ANSWER_KEY_NONE, ANSWER_KEY_AT_END, ANSWER_KEY_AND_RESOLUTION_AT_END,
)

RESOLUTION_SUMMARY = "SUMMARY"
RESOLUTION_STEP_BY_STEP = "STEP_BY_STEP"
RESOLUTION_STYLES = (RESOLUTION_SUMMARY, RESOLUTION_STEP_BY_STEP)

_MAX_QUESTIONS = 200
_MAX_TITLE = 200
_MAX_INSTRUCTIONS = 4000


class ListGenerationError(ValueError):
    """Raised for any invalid selection or configuration. Maps to HTTP 422."""


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListConfiguration:
    title: str
    instructions: str | None = None
    activity_mode: str = ACTIVITY_MODE_EXERCISE_LIST
    answer_key_presentation: str = ANSWER_KEY_NONE
    resolution_style: str | None = None  # only meaningful with KEY_AND_RESOLUTION_AT_END

    def validated(self) -> "ListConfiguration":
        title = (self.title or "").strip()
        if not title:
            raise ListGenerationError("A lista precisa de um título.")
        if len(title) > _MAX_TITLE:
            raise ListGenerationError(f"Título excede {_MAX_TITLE} caracteres.")
        instructions = (self.instructions or "").strip() or None
        if instructions and len(instructions) > _MAX_INSTRUCTIONS:
            raise ListGenerationError(f"Instruções excedem {_MAX_INSTRUCTIONS} caracteres.")
        mode = (self.activity_mode or ACTIVITY_MODE_EXERCISE_LIST).upper()
        if mode not in SUPPORTED_ACTIVITY_MODES:
            if mode in RESERVED_ACTIVITY_MODES:
                raise ListGenerationError(
                    f"O modo '{mode}' está reservado para uma fase futura; use "
                    f"'{ACTIVITY_MODE_EXERCISE_LIST}'."
                )
            raise ListGenerationError(f"Modo de atividade desconhecido: {mode!r}.")
        presentation = (self.answer_key_presentation or ANSWER_KEY_NONE).upper()
        if presentation not in ANSWER_KEY_PRESENTATIONS:
            raise ListGenerationError(
                f"Configuração de gabarito inválida: {presentation!r}."
            )
        style = self.resolution_style
        if presentation == ANSWER_KEY_AND_RESOLUTION_AT_END:
            style = (style or RESOLUTION_SUMMARY).upper()
            if style not in RESOLUTION_STYLES:
                raise ListGenerationError(f"Estilo de resolução inválido: {style!r}.")
        else:
            style = None
        return ListConfiguration(
            title=title, instructions=instructions, activity_mode=mode,
            answer_key_presentation=presentation, resolution_style=style,
        )


@dataclass(frozen=True)
class GeneratedOption:
    key: str
    position: int
    text: str


@dataclass(frozen=True)
class ResolutionView:
    available: bool
    style: str | None
    text: str | None
    unavailable_reason: str | None


@dataclass(frozen=True)
class AnswerKeyView:
    correct_option_key: str
    correct_option_id: str | None
    source: str  # "official_answer_key" | "option_flag"
    revision_number: int | None
    resolution: ResolutionView | None


@dataclass(frozen=True)
class GeneratedListItem:
    position: int
    question_version_id: str
    question_id: str
    # preserved identity / source / ordering metadata
    source: str  # e.g. "ENEM"
    year: int | None
    day: int | None
    official_number: int | None
    booklet_code: str | None
    original_position: int | None
    enem_area: str | None
    discipline_code: str | None
    content_code: str | None
    subcontent_code: str | None
    classification_state: str
    statement: str
    options: list[GeneratedOption]
    answer_key: AnswerKeyView | None  # populated only when the presentation includes it


@dataclass(frozen=True)
class GeneratedListDefinition:
    configuration: ListConfiguration
    items: list[GeneratedListItem]
    question_count: int
    question_version_ids: list[str]
    answer_key_included: bool
    resolution_included: bool
    generated_at: str
    selection_fingerprint: str
    # forward-compat: this DTO is shaped so a future persisted List/Assessment can
    # reference the same question_version_ids without duplicating question content.
    future_compat: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ListGeneratorService:
    """Deterministic, read-only list generator. One :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._bank = QuestionBankService(session)

    async def generate(
        self,
        question_version_ids: Sequence[UUID],
        configuration: ListConfiguration,
        *,
        source_label: str = "professor-list-generator",
        school_id: str | UUID | None = None,
    ) -> GeneratedListDefinition:
        config = configuration.validated()

        ids = [UUID(str(v)) for v in question_version_ids]
        if not ids:
            raise ListGenerationError("Selecione ao menos uma questão.")
        if len(ids) > _MAX_QUESTIONS:
            raise ListGenerationError(f"A lista suporta no máximo {_MAX_QUESTIONS} questões.")
        # reuse PHASE 12 validation verbatim: existence, official_original, no dup, order
        try:
            selection = await self._bank.build_selection(ids, source=source_label, school_id=school_id)
        except ValueError as exc:
            raise ListGenerationError(str(exc)) from exc
        ordered_ids = selection.question_version_ids

        items_by_version = {
            item.question_version_id: item
            for item in await self._bank.get_questions_by_version_ids(ordered_ids)
        }
        missing = [str(v) for v in ordered_ids if v not in items_by_version]
        if missing:  # pragma: no cover - build_selection already guarantees presence
            raise ListGenerationError(f"Questões não encontradas: {missing}")

        include_key = config.answer_key_presentation != ANSWER_KEY_NONE
        include_resolution = config.answer_key_presentation == ANSWER_KEY_AND_RESOLUTION_AT_END
        answer_keys = (
            await self._authoritative_answer_keys(ordered_ids) if include_key else {}
        )

        items: list[GeneratedListItem] = []
        for position, vid in enumerate(ordered_ids, start=1):
            bank_item = items_by_version[vid]
            answer_view = None
            if include_key:
                answer_view = self._answer_key_view(
                    bank_item, answer_keys.get(vid), config, include_resolution
                )
            items.append(self._to_generated_item(position, bank_item, answer_view))

        payload = {
            "ids": [str(v) for v in ordered_ids],
            "config": asdict(config),
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

        return GeneratedListDefinition(
            configuration=config,
            items=items,
            question_count=len(items),
            question_version_ids=[str(v) for v in ordered_ids],
            answer_key_included=include_key,
            resolution_included=include_resolution,
            generated_at=datetime.now(timezone.utc).isoformat(),
            selection_fingerprint=fingerprint,
            future_compat={
                "canonical_reference": "question_version_id",
                "maps_to": "AssessmentItem(question_version_id, position, answer_key_revision_id)",
                "question_usage_history": (
                    "integration point - no application/usage-history table exists yet; "
                    "a future Question Usage History service would consume "
                    "GeneratedListDefinition.question_version_ids + official_number + year"
                ),
                "authored_questions": (
                    "integration point - QuestionSelection currently accepts only "
                    "official_original versions; per-school authored questions are the "
                    "documented PHASE 12 tenant integration point"
                ),
            },
        )

    # -- answer key (authoritative) -----------------------------------------

    async def _authoritative_answer_keys(
        self, version_ids: Sequence[UUID]
    ) -> dict[UUID, dict]:
        """One batched query: official AnswerKeyEntry per question_version_id."""
        ids = list(version_ids)
        if not ids:
            return {}
        q = (
            select(
                BookletQuestion.question_version_id,
                AnswerKeyEntry.official_answer_label,
                AnswerKeyEntry.resolved_option_id,
                AnswerKeyRevision.revision_number,
            )
            .join(AnswerKeyEntry, AnswerKeyEntry.booklet_question_id == BookletQuestion.id)
            .join(AnswerKeyRevision, AnswerKeyRevision.id == AnswerKeyEntry.answer_key_revision_id)
            .where(
                BookletQuestion.question_version_id.in_(ids),
                AnswerKeyRevision.is_official.is_(True),
            )
            .order_by(AnswerKeyRevision.revision_number.desc())
        )
        out: dict[UUID, dict] = {}
        for version_id, label, option_id, revision in (await self._session.execute(q)).all():
            out.setdefault(version_id, {
                "label": label,
                "resolved_option_id": str(option_id) if option_id else None,
                "revision_number": revision,
            })
        return out

    @staticmethod
    def _answer_key_view(
        bank_item: QuestionBankItem,
        official: dict | None,
        config: ListConfiguration,
        include_resolution: bool,
    ) -> AnswerKeyView:
        # official answer-key entry is authoritative; option flag is a cross-check
        flagged = [o for o in bank_item.options if o.is_valid_option]
        flagged_key = flagged[0].key if len(flagged) == 1 else None
        flagged_id = str(flagged[0].id) if len(flagged) == 1 else None

        if official and official.get("label"):
            key = official["label"]
            option_id = official.get("resolved_option_id") or flagged_id
            source = "official_answer_key"
            revision = official.get("revision_number")
        elif flagged_key is not None:
            key = flagged_key
            option_id = flagged_id
            source = "option_flag"
            revision = None
        else:  # pragma: no cover - every question in the bank has an official key
            key = "?"
            option_id = None
            source = "unavailable"
            revision = None

        resolution = None
        if include_resolution:
            resolution = ResolutionView(
                available=False,
                style=config.resolution_style,
                text=None,
                unavailable_reason=(
                    "Não há resolução oficial passo a passo armazenada para esta questão. "
                    "A geração de resolução por IA é uma fase futura e não é usada aqui."
                ),
            )
        return AnswerKeyView(
            correct_option_key=key,
            correct_option_id=option_id,
            source=source,
            revision_number=revision,
            resolution=resolution,
        )

    # -- item assembly ----------------------------------------------------

    @staticmethod
    def _to_generated_item(
        position: int, bank_item: QuestionBankItem, answer_view: AnswerKeyView | None
    ) -> GeneratedListItem:
        c = bank_item.classification
        return GeneratedListItem(
            position=position,
            question_version_id=str(bank_item.question_version_id),
            question_id=str(bank_item.question_id),
            source="ENEM",
            year=bank_item.year,
            day=bank_item.day,
            official_number=bank_item.official_number,
            booklet_code=bank_item.booklet_code,
            original_position=bank_item.position,
            enem_area=bank_item.enem_area,
            discipline_code=c.discipline_code if c else None,
            content_code=c.content_code if c else None,
            subcontent_code=c.subcontent_code if c else None,
            classification_state=bank_item.classification_state,
            statement=bank_item.statement or bank_item.canonical_text,
            options=[
                GeneratedOption(key=o.key, position=o.position, text=o.text)
                for o in sorted(bank_item.options, key=lambda x: x.position)
            ],
            answer_key=answer_view,
        )


def config_options() -> dict:
    """Static vocabulary for the frontend configuration screen."""
    return {
        "activity_modes": {
            "supported": list(SUPPORTED_ACTIVITY_MODES),
            "reserved_future": list(RESERVED_ACTIVITY_MODES),
        },
        "answer_key_presentations": [
            {"value": ANSWER_KEY_NONE, "label": "Sem gabarito / resolução"},
            {"value": ANSWER_KEY_AT_END, "label": "Gabarito ao final"},
            {"value": ANSWER_KEY_AND_RESOLUTION_AT_END, "label": "Gabarito + resolução ao final"},
        ],
        "resolution_styles": [
            {"value": RESOLUTION_SUMMARY, "label": "Resolução resumida"},
            {"value": RESOLUTION_STEP_BY_STEP, "label": "Resolução passo a passo"},
        ],
        "resolution_availability": "UNAVAILABLE",
        "resolution_note": (
            "Não há resoluções oficiais armazenadas no sistema. As opções de estilo ficam "
            "disponíveis para uma fase futura; nesta fase a resolução é marcada como indisponível."
        ),
    }


__all__ = [
    "ACTIVITY_MODE_EXERCISE_LIST",
    "ANSWER_KEY_AND_RESOLUTION_AT_END",
    "ANSWER_KEY_AT_END",
    "ANSWER_KEY_NONE",
    "ANSWER_KEY_PRESENTATIONS",
    "AnswerKeyView",
    "GeneratedListDefinition",
    "GeneratedListItem",
    "GeneratedOption",
    "ListConfiguration",
    "ListGenerationError",
    "ListGeneratorService",
    "RESERVED_ACTIVITY_MODES",
    "RESOLUTION_STEP_BY_STEP",
    "RESOLUTION_STYLES",
    "RESOLUTION_SUMMARY",
    "ResolutionView",
    "SUPPORTED_ACTIVITY_MODES",
    "config_options",
]
