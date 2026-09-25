"""Layers 1-3 of engine-output validation (spec v1.0 §7, §18).

Layer 1 checks shape: does the payload even parse as :class:`EssayEngineOutput`.
Layer 2 checks the output against the rubric it claims to have used. Layer 3
checks that every specific claim points at something real.

Layer 3 is the reason this module exists. Spec §4 states a "regra de segurança
pedagógica": the engine must not invent an error to justify a score. As prose,
that is a paragraph in a PDF. Here it is a rejection condition - an annotation
that criticises a specific passage must resolve to that passage, or declare
itself a global judgement of the competency.

Spec §7 promises a single failure currency: every rejection, at any layer, is an
:class:`EssayEngineOutputRejected` carrying a reason code, the raw output and the
input hash. Layer 1 previously broke that promise - a malformed payload fails
:meth:`EssayEngineOutput.model_validate` with Pydantic's own ``ValidationError``,
which carries none of those three things. :func:`validate_engine_output_from_payload`
is the fix and the intended entry point: it runs all three layers and always
raises the one exception type, however the payload failed.

``RubricView`` is a plain frozen snapshot rather than an ORM object so the
validator stays pure: no session, no I/O, no async. ``load_rubric_view`` is the
only part that touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricSignal,
)
from agente_ia_edu.essay_engine_contract.v1 import EssayEngineOutput


class EssayEngineOutputRejected(ValueError):
    """The output did not survive validation and must not be published.

    Carries what a reprocessing run needs: why it failed, what the model actually
    returned, and the hash of the input that produced it.
    """

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        raw_output: Any = None,
        input_hash: str | None = None,
    ) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.raw_output = raw_output
        self.input_hash = input_hash


class RubricHasNoLevelsError(ValueError):
    """A rubric row exists but has no competencies or levels to validate against.

    This is the reader's counterpart to ``EssayRubricSeeder``'s
    ``IncompleteRubricSeedError``: that one guards the write path, refusing to
    treat a bare rubric header row as a complete seed; this one guards the read
    path, refusing to treat a bare header row as a usable rubric. Without this
    guard, ``load_rubric_view`` would happily return
    ``RubricView(levels={}, signal_keys=frozenset())`` for such a row, and a
    formative output with no annotations would validate clean against it - while
    any output with real content would fail with the misleading
    ``UNKNOWN_COMPETENCY`` message rather than naming the actual problem: the
    rubric itself was never fully seeded.
    """


@dataclass(frozen=True)
class RubricView:
    """Read-only snapshot of the parts of a rubric the validator needs."""

    rubric_version: str
    levels: Mapping[str, frozenset[int]]
    signal_keys: frozenset[str]


async def load_rubric_view(session: AsyncSession, rubric_version: str) -> RubricView:
    rubric = await session.scalar(
        select(EssayRubric).where(EssayRubric.rubric_version == rubric_version)
    )
    if rubric is None:
        raise ValueError(f"Unknown rubric version {rubric_version!r}")

    rows = (
        await session.execute(
            select(EssayRubricCompetency.code, EssayRubricLevel.points)
            .join(
                EssayRubricLevel,
                EssayRubricLevel.competency_id == EssayRubricCompetency.id,
            )
            .where(EssayRubricCompetency.rubric_id == rubric.id)
        )
    ).all()

    levels: dict[str, set[int]] = {}
    for code, points in rows:
        levels.setdefault(code, set()).add(points)

    if not levels:
        raise RubricHasNoLevelsError(
            f"Rubric version {rubric_version!r} (id={rubric.id}) exists but has "
            "no competencies or levels. A rubric row with no children cannot be "
            "validated against; seed it fully with EssayRubricSeeder before using "
            "it, or investigate how a bare header row was written outside a full "
            "seed()."
        )

    signal_rows = (
        await session.execute(
            select(EssayRubricSignal.key)
            .join(
                EssayRubricCompetency,
                EssayRubricCompetency.id == EssayRubricSignal.competency_id,
            )
            .where(
                EssayRubricCompetency.rubric_id == rubric.id,
                EssayRubricSignal.active.is_(True),
            )
        )
    ).all()

    return RubricView(
        rubric_version=rubric.rubric_version,
        levels={code: frozenset(points) for code, points in levels.items()},
        signal_keys=frozenset(key for (key,) in signal_rows),
    )


def validate_engine_output(
    output: EssayEngineOutput,
    *,
    rubric: RubricView,
    text: str | None = None,
    page_boxes: Mapping[int, tuple[float, float]] | None = None,
    raw_output: Any = None,
    input_hash: str | None = None,
) -> None:
    """Raise :class:`EssayEngineOutputRejected` unless the output is publishable.

    ``text`` is required for TEXT_OFFSET outputs; ``page_boxes`` maps page number
    to ``(width, height)`` and is required for IMAGE_REGION outputs.
    """

    def reject(reason_code: str, message: str) -> None:
        raise EssayEngineOutputRejected(
            reason_code, message, raw_output=raw_output, input_hash=input_hash
        )

    # --- Layer 2: coherence with the rubric -------------------------------
    if output.identification.rubric_version != rubric.rubric_version:
        reject(
            "RUBRIC_VERSION_MISMATCH",
            f"output claims {output.identification.rubric_version!r} but was "
            f"validated against {rubric.rubric_version!r}",
        )

    if output.scores is not None:
        for code, score in output.scores.per_competency.items():
            allowed = rubric.levels.get(code)
            if allowed is None:
                reject("UNKNOWN_COMPETENCY", f"rubric has no competency {code!r}")
            if score.points not in allowed:
                reject(
                    "SCORE_NOT_IN_RUBRIC_LEVELS",
                    f"{code} scored {score.points}, which is not a level of "
                    f"{rubric.rubric_version}",
                )

    # signal_keys are auxiliary tags - nothing outside this validator reads
    # them (not the frontend, not the PDF export, not the evolution
    # dashboard), and the prompt sent to the engine never enumerates the
    # rubric's actual registered signal keys, so the model has no way to
    # know which ones are valid. Rejecting the whole correction over a
    # made-up tag on an otherwise-sound rationale/annotation throws away
    # real pedagogical content for a mismatch in decoration. Unknown keys
    # are simply not cross-checked here - not worth failing the correction.

    for rationale in output.rationales:
        if rationale.competency_code not in rubric.levels:
            reject(
                "UNKNOWN_COMPETENCY",
                f"rubric has no competency {rationale.competency_code!r}",
            )

    for annotation in output.annotations:
        if annotation.competency_code not in rubric.levels:
            reject(
                "UNKNOWN_COMPETENCY",
                f"rubric has no competency {annotation.competency_code!r}",
            )

    valid_letters = {annotation.letter for annotation in output.annotations}
    annotation_competency_by_letter = {
        annotation.letter: annotation.competency_code for annotation in output.annotations
    }
    for rewrite in output.rewrites:
        if rewrite.letter not in valid_letters:
            reject(
                "REWRITE_LETTER_NOT_FOUND",
                f"rewrite references letter {rewrite.letter!r}, which is not "
                f"among the annotations' letters {sorted(valid_letters)}",
            )
        elif rewrite.competency_code != annotation_competency_by_letter[rewrite.letter]:
            reject(
                "REWRITE_COMPETENCY_MISMATCH",
                f"rewrite for letter {rewrite.letter!r} declares competency "
                f"{rewrite.competency_code!r} but that annotation is "
                f"{annotation_competency_by_letter[rewrite.letter]!r}",
            )
        if rewrite.competency_code not in rubric.levels:
            reject(
                "UNKNOWN_COMPETENCY",
                f"rubric has no competency {rewrite.competency_code!r}",
            )

    # --- Layer 3: anchoring ------------------------------------------------
    mode = output.identification.anchor_mode

    if mode == "TEXT_OFFSET":
        if text is None:
            reject(
                "MISSING_CANONICAL_TEXT",
                "a TEXT_OFFSET output can only be validated against its text",
            )
        for annotation in output.annotations:
            anchor = annotation.anchor
            if anchor is None:
                # GLOBAL sem âncora - Camada 1 já garante que só GLOBAL chega
                # aqui sem anchor; nada de posição/texto pra verificar.
                continue
            if anchor.end > len(text):
                reject(
                    "OFFSET_OUT_OF_BOUNDS",
                    f"annotation {annotation.letter!r} ends at {anchor.end} but the "
                    f"text has {len(text)} characters",
                )
            if text[anchor.start : anchor.end] != anchor.quote:
                reject(
                    "QUOTE_DOES_NOT_MATCH_TEXT",
                    f"annotation {annotation.letter!r} quotes {anchor.quote!r} but "
                    f"the text reads {text[anchor.start : anchor.end]!r}",
                )
            _require_evidence(annotation, anchor.quote, reject)
    else:
        if page_boxes is None:
            reject(
                "MISSING_PAGE_BOXES",
                "an IMAGE_REGION output can only be validated against page sizes",
            )
        for annotation in output.annotations:
            anchor = annotation.anchor
            if anchor is None:
                # GLOBAL sem âncora - Camada 1 já garante que só GLOBAL chega
                # aqui sem anchor; nada de posição/texto pra verificar.
                continue
            box = page_boxes.get(anchor.page)
            if box is None:
                reject(
                    "REGION_OUT_OF_PAGE",
                    f"annotation {annotation.letter!r} anchors on page "
                    f"{anchor.page}, which does not exist",
                )
            width, height = box
            if anchor.x + anchor.width > width or anchor.y + anchor.height > height:
                reject(
                    "REGION_OUT_OF_PAGE",
                    f"annotation {annotation.letter!r} anchors outside page "
                    f"{anchor.page} ({width}x{height})",
                )
            _require_evidence(annotation, anchor.read_text, reject)


def validate_engine_output_from_payload(
    raw_payload: dict,
    *,
    rubric: RubricView,
    text: str | None = None,
    page_boxes: Mapping[int, tuple[float, float]] | None = None,
    raw_output: Any = None,
    input_hash: str | None = None,
) -> EssayEngineOutput:
    """Single entry point for engine-output validation: layers 1, 2 and 3.

    Callers with a raw model payload should use this instead of calling
    :func:`validate_engine_output` directly, because that function only ever
    sees an already-parsed :class:`EssayEngineOutput` and therefore cannot
    guard layer 1 (shape). A malformed payload previously failed
    ``EssayEngineOutput.model_validate`` with Pydantic's own ``ValidationError``
    - carrying none of the reason code, raw output or input hash that spec §7
    promises for every rejection - which forced every caller to catch two
    exception types and hand-build the layer-1 audit record itself. This
    function closes that gap: whichever layer rejects the payload, the caller
    catches exactly one exception type, :class:`EssayEngineOutputRejected`,
    always carrying the same three things.

    ``raw_output`` defaults to ``raw_payload`` itself when not given, since the
    payload passed in here already IS the raw model output worth keeping for
    reprocessing.
    """
    effective_raw_output = raw_payload if raw_output is None else raw_output
    try:
        output = EssayEngineOutput.model_validate(raw_payload)
    except ValidationError as exc:
        raise EssayEngineOutputRejected(
            "CONTRACT_SHAPE_INVALID",
            str(exc),
            raw_output=effective_raw_output,
            input_hash=input_hash,
        ) from exc

    validate_engine_output(
        output,
        rubric=rubric,
        text=text,
        page_boxes=page_boxes,
        raw_output=effective_raw_output,
        input_hash=input_hash,
    )
    return output


def _require_evidence(annotation, evidence: str, reject) -> None:
    """Spec §4: a specific critique must point at something, or say it is global."""
    if annotation.evidence_kind == "GLOBAL":
        return
    if not evidence.strip():
        reject(
            "SPECIFIC_CRITIQUE_WITHOUT_EVIDENCE",
            f"annotation {annotation.letter!r} claims localized evidence but "
            f"anchors on blank text; it must declare evidence_kind=GLOBAL instead",
        )


__all__ = [
    "EssayEngineOutputRejected",
    "RubricHasNoLevelsError",
    "RubricView",
    "load_rubric_view",
    "validate_engine_output",
    "validate_engine_output_from_payload",
]
