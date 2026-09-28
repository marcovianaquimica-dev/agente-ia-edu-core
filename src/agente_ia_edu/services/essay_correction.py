# src/agente_ia_edu/services/essay_correction.py
"""R3 - EssayCorrectionService: the correction engine's orchestration
(spec §4, the 7-step correction flow).

Branches on EssaySubmission.anchor_mode: TEXT_OFFSET calls the text
provider with the submission's canonical_text; IMAGE_REGION calls the
image provider with the submission's page images directly - the "IA
corrige direto da imagem tambem" decision made during planning, so a
submission with no canonical text (transcription disabled) is not merely
parked in NEEDS_REVIEW, it is actually corrected. Both paths converge on
the same validation (essay_engine_validation.py, R1, unmodified) and the
same EssayCorrection row shape.

Every failure mode - a provider error (including a misconfigured
deployment), a malformed JSON response, a rejected engine output - lands
in the same place: a NEEDS_REVIEW row with ai_output=None and a readable
failure_reason. _run_ai never raises for an AI-side failure; only a
genuine precondition violation (submission not found, not SUBMITTED,
already corrected) raises, from correct()/retry() themselves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AdminAuditLog,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    EssaySubmissionPage,
    PromptAssignment,
)
from ..essay_engine_contract.v4 import (
    COMPETENCY_CODES,
    CONTRACT_VERSION,
    EssayEngineOutput,
    Feedback,
    Scores,
)
from ..essay_prompts import alert_review_v1, competency_scoring_v1, get_essay_prompt
from ..providers.contracts import EssayImageCorrectionProvider, TextGenerationProvider
from ..providers.errors import ProviderError
from ..providers.factory import build_essay_image_corrector, build_text_provider
from ..providers.models import EssayImageCorrectionRequest, TextGenerationRequest
from ..rubrics.loader import RubricFile, load_rubric_file
from .canonical_hash import canonical_hash
from .essay_correction_key import correction_key as compute_correction_key
from .essay_engine_validation import (
    EssayEngineOutputRejected,
    RubricHasNoLevelsError,
    load_rubric_view,
    validate_engine_output_from_payload,
)
from .institution_settings import InstitutionSettingsService

logger = logging.getLogger(__name__)

_ENGINE_VERSION = "r3_correction_engine_v2"
_PROMPT_VERSION = "essay_correction_v14"
_RUBRIC_FILE_NAME = "enem_2025"

# Determinism: "mesma redacao = mesma nota" (the C1 protocol's own item 25
# already names this as a requirement). Confirmed live (2026-09-28
# calibration run) that the SAME essay text corrected 4 times in a row
# swung C1 from 0 to 80 and the total score from 0 to 360 - pure sampling
# noise from the provider's own default generation, unrelated to any prompt
# content. temperature is deliberately NOT pinned here - confirmed live
# that the model backing this deployment (OPENAI_MODEL) rejects any
# temperature other than its default (1) with a 400 error ("Unsupported
# value: 'temperature' does not support 0.0 with this model"). seed is a
# best-effort reproducibility hint the API does not guarantee but does
# accept for this model, and costs nothing when a provider ignores it.
_CORRECTION_SEED = 20260928


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


#: Alert codes whose official consequence (cartilha p. 9-10 and p. 28) is a
#: whole-essay zero, not a per-competency deduction - see essay_engine_contract
#: v3's docstring for why FUGA_AO_TEMA/TIPO_TEXTUAL needed splitting from their
#: softer counterparts before this could be applied safely, and v4's for the
#: five codes added below (ANULACAO_PROPOSITAL through TEXTO_ILEGIVEL) plus
#: why `texto_em_branco` has no code at all - it cannot reach the engine.
_ANULA_REDACAO_ALERT_CODES = frozenset({
    "FUGA_AO_TEMA", "TIPO_TEXTUAL_PREDOMINANTE", "TEXTO_INSUFICIENTE",
    "ANULACAO_PROPOSITAL", "PARTE_DESCONECTADA_DO_TEMA",
    "IDENTIFICACAO_INDEVIDA", "LINGUA_ESTRANGEIRA", "TEXTO_ILEGIVEL",
})


def _apply_deterministic_scoring_rules(
    output: EssayEngineOutput,
    phase2_points: dict[str, int] | None = None,
    alert_codes: set[str] | None = None,
) -> dict | None:
    """Enforces the ENEM 2025 rubric's own normative scoring_rules
    (rubrics/enem_2025.yaml) on top of the starting per-competency points,
    deterministically - these are checkable, official consequences of
    specific signals the model already reports (alerts,
    intervention.respeita_direitos_humanos), not pedagogical judgment calls
    the model should be trusted to apply consistently entry by entry.
    ai_output keeps the model's own phase-1 scores exactly as it reported
    them; only this function's result - final_scores, what actually gets
    published - can differ from that, the same split approve() already
    relies on for a teacher's manual score edit.

    ``phase2_points``, when given (see
    EssayCorrectionService._score_competencies_from_evidence), REPLACES
    output.scores.per_competency's own points as the starting point before
    alert/tangenciamento/direitos-humanos rules are applied - phase 1's own
    per-competency points are calibration-noisy (2026-09-28 finding: the
    same essay swung 0-80 on C1 alone across repeated identical calls) and
    are never what gets published once phase 2 has run.

    ``alert_codes``, when given (see
    EssayCorrectionService._review_anula_redacao_alerts), REPLACES
    {alert.code for alert in output.alerts} for this function's own purposes
    - phase 1's own ANULA_REDACAO alerts are ALSO calibration-noisy (same
    2026-09-28 finding: a normal, gradeable essay got zeroed by a
    whole-essay alert in 2 of 4 identical repeated corrections) and are
    never what final_scores is computed from once the alert review has run.

    Returns None when output.scores is None (FORMATIVO produces no grade to
    adjust - phase 2 never runs in that mode either, see correct()/_run_ai).
    """
    if output.scores is None:
        return None

    if alert_codes is None:
        alert_codes = {alert.code for alert in output.alerts}
    if phase2_points is not None:
        points = dict(phase2_points)
    else:
        points = {code: score.points for code, score in output.scores.per_competency.items()}
    confidences = {code: score.confidence for code, score in output.scores.per_competency.items()}

    if alert_codes & _ANULA_REDACAO_ALERT_CODES:
        # Cartilha p. 9-10 (fuga total ao tema / nao atendimento ao tipo
        # dissertativo-argumentativo / texto insuficiente) and p. 28
        # (predominancia de outro tipo textual): a whole-essay zero, not a
        # per-competency deduction.
        points = {code: 0 for code in points}
    else:
        if "TANGENCIAMENTO_AO_TEMA" in alert_codes:
            # Cartilha p. 27, quadro ATENCAO!: tangenciamento affects C2
            # through C2's own descriptor (already surfaced to the model, since
            # the descriptor text itself mentions tangenciamento) but ALSO
            # caps C3 and C5 at 40 points - a cross-competency ceiling neither
            # competency's own descriptor can express on its own, so it can't
            # be left to the model to apply just by scoring C3/C5 normally.
            points["C3"] = min(points["C3"], 40)
            points["C5"] = min(points["C5"], 40)
        if not output.intervention.respeita_direitos_humanos:
            # Cartilha p. 39, quadro ATENCAO!: zeroes Competencia V alone,
            # never the whole essay - keep this in the `else` branch so it's
            # a no-op (already zero) when an ANULA_REDACAO alert fired above.
            points["C5"] = 0

    total = sum(points.values())
    return {
        "per_competency": {
            code: {"points": points[code], "confidence": confidences[code]}
            for code in points
        },
        "total": total,
    }


def _effective_essay_statement(submission: EssaySubmission, essay_prompt: EssayPrompt) -> str:
    """"Tema livre" (EssayPrompt.is_free_theme): the student typed their own
    theme at submission time (EssaySubmission.student_declared_theme), so
    ESSAY_STATEMENT must be built from THAT, not the prompt's own generic
    statement - otherwise FUGA_AO_TEMA would be judged against a theme the
    student never actually had. Every other submission is unaffected:
    student_declared_theme is None, so this returns the prompt's statement
    exactly as before."""
    if not submission.student_declared_theme:
        return essay_prompt.statement
    return (
        "Redacao de tema livre: o(a) participante escolheu escrever sobre o "
        f"seguinte tema, declarado por ele(a) mesmo(a) antes de escrever: "
        f"\"{submission.student_declared_theme}\". Redija um texto "
        "dissertativo-argumentativo em modalidade escrita formal da lingua "
        "portuguesa sobre EXATAMENTE esse tema declarado, apresentando "
        "proposta de intervencao que respeite os direitos humanos. Avalie "
        "fuga ao tema, tangenciamento e compreensao do tema comparando o "
        "texto produzido contra este tema declarado pelo proprio "
        "participante - nao contra nenhum outro tema."
    )


def _rubric_payload(rubric_file: RubricFile) -> dict:
    return {
        "rubric_version": rubric_file.rubric_version,
        "competencies": [
            {
                "code": competency.code,
                "official_title": competency.official_title,
                "levels": [
                    {"points": level.points, "descriptor": level.descriptor}
                    for level in competency.levels
                ],
                # Confirmed by rubric audit (2026-09-27): rubrics/enem_2025.yaml
                # transcribes a full controlled vocabulary of specific,
                # cartilha-sourced concepts per competency (e.g. C1's
                # "estrutura_sintatica", C3's "autoria") for exactly the
                # signal_keys field below - previously seeded into the DB but
                # never included in this payload, so the model had no way to
                # know these keys existed and free-invented its own instead.
                "signals": [
                    {"key": signal.key, "label": signal.label, "description": signal.description}
                    for signal in competency.signals
                ],
            }
            for competency in rubric_file.competencies
        ],
    }


def _image_pages_hash(pages: list[EssaySubmissionPage]) -> str:
    """Surrogate for essay_text_hash() when there is no canonical text.

    An IMAGE_REGION submission never has normalized_text_hash (R2's
    documented, by-design consequence of skipped transcription), but
    correction_key() still needs some stable identity for "this content,
    these versions". Built from the ordered list of page storage URIs, so
    the same set of page images always produces the same key input - a
    new R3-owned hash, not a repurposing of essay_text_hash, whose
    contract stays text-only and untouched.
    """
    return canonical_hash([page.storage_uri for page in pages])


def _requires_teacher_review(
    *, total_score: int | None, validation_threshold_points: int | None, validation_enabled: bool
) -> bool:
    """Spec §5: below the threshold, review is mandatory regardless of the
    assignment's own toggle; otherwise the assignment's validation_enabled
    decides. Only ever called in AVALIATIVO - FORMATIVO always auto-publishes
    since it produces no score to gate on."""
    if (
        validation_threshold_points is not None
        and total_score is not None
        and total_score < validation_threshold_points
    ):
        return True
    return validation_enabled


class EssayCorrectionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        text_provider: TextGenerationProvider | None = None,
        image_provider: EssayImageCorrectionProvider | None = None,
    ) -> None:
        self.session = session
        # Lazily built, same reasoning as EssaySubmissionService._transcriber:
        # a school that never uses IMAGE_REGION submissions should never need
        # OPENAI_VISION_MODEL configured.
        self._text_provider = text_provider
        self._image_provider = image_provider

    def _get_text_provider(self) -> TextGenerationProvider:
        if self._text_provider is None:
            self._text_provider = build_text_provider()
        return self._text_provider

    def _get_image_provider(self) -> EssayImageCorrectionProvider:
        if self._image_provider is None:
            self._image_provider = build_essay_image_corrector()
        return self._image_provider

    async def correct(self, essay_submission_id: uuid.UUID) -> EssayCorrection:
        """Spec §4 step 2: idempotent, not merely guarded. A network retry of
        the same confirm request must never call the AI twice or fail the
        second time - it must silently return the correction the first call
        already produced (of ANY status, including NEEDS_REVIEW - retrying a
        genuine AI failure is retry()'s job, a distinct action, not
        something a bare repeated confirm should trigger on its own)."""
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")
        if submission.status != "SUBMITTED":
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status}, not "
                "SUBMITTED - only a submitted essay can be corrected."
            )
        existing = await self.session.scalar(
            select(EssayCorrection).where(
                EssayCorrection.essay_submission_id == essay_submission_id
            )
        )
        if existing is not None:
            return existing

        fields = await self._run_ai(submission)
        correction = EssayCorrection(
            id=uuid.uuid4(), school_id=submission.school_id,
            essay_submission_id=submission.id, **fields,
        )
        await self._apply_review_policy(correction, submission)
        self.session.add(correction)
        await self.session.flush()
        return correction

    async def retry(self, essay_correction_id: uuid.UUID) -> EssayCorrection:
        correction = await self.session.get(EssayCorrection, essay_correction_id)
        if correction is None:
            raise ValueError(f"EssayCorrection not found: {essay_correction_id}")
        if correction.status != "NEEDS_REVIEW":
            raise ValueError(
                f"EssayCorrection {essay_correction_id} is {correction.status}, not "
                "NEEDS_REVIEW - only a failed correction can be retried."
            )
        submission = await self.session.get(EssaySubmission, correction.essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {correction.essay_submission_id}")

        fields = await self._run_ai(submission)
        for field, value in fields.items():
            setattr(correction, field, value)
        await self._apply_review_policy(correction, submission)
        await self.session.flush()
        return correction

    async def approve(
        self, essay_correction_id: uuid.UUID, *, reviewed_by_external_identity: str,
        final_scores: dict | None = None, final_feedback: dict | None = None,
    ) -> EssayCorrection:
        correction = await self.session.get(EssayCorrection, essay_correction_id)
        if correction is None:
            raise ValueError(f"EssayCorrection not found: {essay_correction_id}")
        if correction.status != "PENDING_REVIEW":
            raise ValueError(
                f"EssayCorrection {essay_correction_id} is {correction.status}, not "
                "PENDING_REVIEW - only a pending correction can be approved."
            )
        edits: dict[str, dict] = {}
        if final_scores is not None:
            try:
                Scores.model_validate(final_scores)
            except ValidationError as exc:
                raise ValueError(f"final_scores is not a valid Scores payload: {exc}") from exc
            edits["final_scores"] = {"before": correction.final_scores, "after": final_scores}
            correction.final_scores = final_scores
        if final_feedback is not None:
            try:
                Feedback.model_validate(final_feedback)
            except ValidationError as exc:
                raise ValueError(f"final_feedback is not a valid Feedback payload: {exc}") from exc
            edits["final_feedback"] = {"before": correction.final_feedback, "after": final_feedback}
            correction.final_feedback = final_feedback

        correction.status = "APPROVED"
        correction.reviewed_by_external_identity = reviewed_by_external_identity
        correction.reviewed_at = _utcnow()
        correction.published_at = _utcnow()
        self.session.add(AdminAuditLog(
            school_id=correction.school_id, performed_by_external_id=reviewed_by_external_identity,
            action="ESSAY_CORRECTION_APPROVED", entity_type="ESSAY_CORRECTION",
            entity_id=str(correction.id), metadata_=edits or None,
        ))
        await self.session.flush()
        return correction

    async def reject(
        self, essay_correction_id: uuid.UUID, *, reviewed_by_external_identity: str,
    ) -> EssayCorrection:
        correction = await self.session.get(EssayCorrection, essay_correction_id)
        if correction is None:
            raise ValueError(f"EssayCorrection not found: {essay_correction_id}")
        if correction.status != "PENDING_REVIEW":
            raise ValueError(
                f"EssayCorrection {essay_correction_id} is {correction.status}, not "
                "PENDING_REVIEW - only a pending correction can be rejected."
            )
        correction.status = "REJECTED"
        correction.reviewed_by_external_identity = reviewed_by_external_identity
        correction.reviewed_at = _utcnow()
        self.session.add(AdminAuditLog(
            school_id=correction.school_id, performed_by_external_id=reviewed_by_external_identity,
            action="ESSAY_CORRECTION_REJECTED", entity_type="ESSAY_CORRECTION",
            entity_id=str(correction.id), metadata_=None,
        ))
        await self.session.flush()
        return correction

    async def bulk_approve(
        self, essay_correction_ids: list[uuid.UUID], *, reviewed_by_external_identity: str,
    ) -> tuple[list[EssayCorrection], dict[uuid.UUID, str]]:
        """Best-effort: each id is attempted independently via approve() (spec
        §5: no decision logic differs from a single approve), so one
        correction a race already moved out of PENDING_REVIEW never blocks
        the rest of the batch. Returns (approved, failures) - failures maps
        the id to why it failed."""
        approved: list[EssayCorrection] = []
        failures: dict[uuid.UUID, str] = {}
        for correction_id in essay_correction_ids:
            try:
                approved.append(
                    await self.approve(
                        correction_id, reviewed_by_external_identity=reviewed_by_external_identity
                    )
                )
            except ValueError as exc:
                failures[correction_id] = str(exc)
        return approved, failures

    async def list_by_status(self, school_id: uuid.UUID, *, status: str) -> list[EssayCorrection]:
        result = await self.session.execute(
            select(EssayCorrection)
            .where(EssayCorrection.school_id == school_id, EssayCorrection.status == status)
            .order_by(EssayCorrection.created_at)
        )
        return list(result.scalars().all())

    async def _apply_review_policy(
        self, correction: EssayCorrection, submission: EssaySubmission
    ) -> None:
        if correction.ai_output is None:
            correction.status = "NEEDS_REVIEW"
            return

        settings = await InstitutionSettingsService(self.session).get_settings(submission.school_id)
        if settings.correction_mode == "FORMATIVO":
            self._publish(correction)
            return

        assignment = await self.session.get(PromptAssignment, submission.prompt_assignment_id)
        total = (correction.final_scores or {}).get("total")
        if total is None:
            # AVALIATIVO must never auto-publish without a grade - a null
            # score is either a malformed AI response or a real edge case,
            # either way it needs a human, not a silent auto-approval.
            correction.status = "PENDING_REVIEW"
            return
        if submission.student_declared_theme:
            # "Tema livre": the student picked their own theme, so there's no
            # official gabarito a teacher would validate the grade against -
            # always auto-publish, the same way FORMATIVO does, regardless of
            # this school's AVALIATIVO threshold policy.
            self._publish(correction)
            return
        needs_review = _requires_teacher_review(
            total_score=total,
            validation_threshold_points=settings.validation_threshold_points,
            validation_enabled=assignment.validation_enabled,
        )
        if needs_review:
            correction.status = "PENDING_REVIEW"
        else:
            self._publish(correction)

    def _publish(self, correction: EssayCorrection) -> None:
        """Auto-publication: reviewed_at is set (the terminal-state CHECK
        requires it for APPROVED) but reviewed_by_external_identity stays
        NULL - that column's job is distinguishing "a teacher decided this"
        from "policy decided this", not merely recording a timestamp."""
        correction.status = "APPROVED"
        correction.reviewed_at = _utcnow()
        correction.published_at = _utcnow()
        correction.reviewed_by_external_identity = None

    async def _run_ai(self, submission: EssaySubmission) -> dict:
        try:
            rubric_file = load_rubric_file(_RUBRIC_FILE_NAME)
        except Exception as exc:
            # load_rubric_file's own failure mode (a packaged rubric YAML
            # going missing or becoming malformed - low probability, but
            # this module's docstring promises every AI/data-side failure
            # becomes NEEDS_REVIEW, never an escaped exception). rubric_version
            # is NOT NULL on EssayCorrection and the real value is exactly
            # what failed to load, so "unknown" is the only honest placeholder.
            logger.warning(
                "essay correction for submission %s: failed to load rubric file %r: %s",
                submission.id, _RUBRIC_FILE_NAME, exc,
            )
            return {
                "correction_key": None, "rubric_version": "unknown",
                "model_version": None, "prompt_version": _PROMPT_VERSION,
                "engine_version": _ENGINE_VERSION, "ai_output": None,
                "final_scores": None, "final_feedback": None,
                "failure_reason": f"Failed to load rubric file {_RUBRIC_FILE_NAME!r}: {exc}",
            }
        rubric_version = rubric_file.rubric_version
        failure_fields = {
            "correction_key": None, "rubric_version": rubric_version,
            "model_version": None, "prompt_version": _PROMPT_VERSION,
            "engine_version": _ENGINE_VERSION, "ai_output": None,
            "final_scores": None, "final_feedback": None, "failure_reason": None,
        }
        try:
            rubric_view = await load_rubric_view(self.session, rubric_version)
        except (ValueError, RubricHasNoLevelsError) as exc:
            logger.warning(
                "essay correction for submission %s: failed to load rubric view %s: %s",
                submission.id, rubric_version, exc,
            )
            return {**failure_fields, "failure_reason": f"{type(exc).__name__}: {exc}"}

        assignment = await self.session.get(PromptAssignment, submission.prompt_assignment_id)
        essay_prompt = await self.session.get(EssayPrompt, assignment.essay_prompt_id)
        rubric_payload = _rubric_payload(rubric_file)
        prompt_artifact = get_essay_prompt(_PROMPT_VERSION)
        # Spec §4 step 4: the prompt branches on correction_mode too, not just
        # anchor_mode - AVALIATIVO asks for a full grade, FORMATIVO asks for
        # scores=null. Read here (not just later in _apply_review_policy) so
        # the AI is never asked to produce a grade FORMATIVO will discard.
        settings = await InstitutionSettingsService(self.session).get_settings(submission.school_id)
        include_scores = settings.correction_mode == "AVALIATIVO"

        try:
            if submission.anchor_mode == "TEXT_OFFSET":
                raw_payload, model_version, text, page_boxes, input_hash = (
                    await self._call_text_provider(
                        submission=submission, essay_prompt=essay_prompt,
                        rubric_payload=rubric_payload, prompt_artifact=prompt_artifact,
                        include_scores=include_scores,
                    )
                )
            else:
                raw_payload, model_version, text, page_boxes, input_hash = (
                    await self._call_image_provider(
                        submission=submission, essay_prompt=essay_prompt,
                        rubric_payload=rubric_payload, prompt_artifact=prompt_artifact,
                        include_scores=include_scores,
                    )
                )
        except ProviderError as exc:
            logger.warning(
                "essay correction for submission %s: provider error: %s",
                submission.id, exc,
            )
            reason = f"{type(exc).__name__}: {exc}"
            diagnostic = getattr(exc, "diagnostic_message", None)
            if diagnostic:
                reason = f"{reason} ({diagnostic})"
            low_level_type = getattr(exc, "low_level_error_type", None)
            if low_level_type:
                low_level_message = getattr(exc, "low_level_diagnostic_message", None)
                reason = f"{reason} [caused by {low_level_type}: {low_level_message}]"
            return {**failure_fields, "failure_reason": reason}
        except json.JSONDecodeError as exc:
            logger.warning(
                "essay correction for submission %s: model returned invalid JSON: %s",
                submission.id, exc,
            )
            return {**failure_fields, "failure_reason": f"Model returned invalid JSON: {exc}"}
        except ValueError as exc:
            logger.warning(
                "essay correction for submission %s: %s: %s",
                submission.id, type(exc).__name__, exc,
            )
            return {**failure_fields, "failure_reason": f"{type(exc).__name__}: {exc}"}

        identification = {
            "essay_id": str(submission.essay_id),
            "essay_version_id": str(submission.id),
            "rubric_version": rubric_version,
            "model_version": model_version,
            "prompt_version": prompt_artifact.version,
            "engine_version": _ENGINE_VERSION,
            "contract_version": CONTRACT_VERSION,
            "anchor_mode": submission.anchor_mode,
        }
        full_payload = {**raw_payload, "identification": identification}

        try:
            output = validate_engine_output_from_payload(
                full_payload, rubric=rubric_view, text=text, page_boxes=page_boxes,
                raw_output=raw_payload, input_hash=input_hash,
            )
        except EssayEngineOutputRejected as exc:
            # str(exc) already carries "{reason_code}: {message}" - see
            # EssayEngineOutputRejected.__init__ in essay_engine_validation.py.
            logger.warning(
                "essay correction for submission %s: engine output rejected: %s",
                submission.id, exc,
            )
            return {
                **failure_fields, "model_version": model_version,
                "failure_reason": f"{exc}",
            }

        phase2_points: dict[str, int] | None = None
        confirmed_alert_codes: set[str] | None = None
        if output.scores is not None:
            # Phase 2: only when there is a grade to refine at all - FORMATIVO
            # (output.scores is None) has nothing for a competency score or
            # an alert-driven zero to replace. Both sub-phases are
            # independent of each other (neither reads the other's result),
            # so they run concurrently. See _score_competencies_from_evidence
            # and _review_anula_redacao_alerts docstrings for why each
            # exists.
            try:
                phase2_points, confirmed_alert_codes = await asyncio.gather(
                    self._score_competencies_from_evidence(
                        output=output, rubric_file=rubric_file,
                    ),
                    self._review_anula_redacao_alerts(
                        output=output,
                        essay_statement=_effective_essay_statement(submission, essay_prompt),
                    ),
                )
            except ProviderError as exc:
                logger.warning(
                    "essay correction for submission %s: phase-2 provider "
                    "error: %s", submission.id, exc,
                )
                return {
                    **failure_fields, "model_version": model_version,
                    "failure_reason": f"CompetencyScoringFailed: {type(exc).__name__}: {exc}",
                }
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning(
                    "essay correction for submission %s: phase-2 returned "
                    "an unusable response: %s", submission.id, exc,
                )
                return {
                    **failure_fields, "model_version": model_version,
                    "failure_reason": f"CompetencyScoringFailed: {type(exc).__name__}: {exc}",
                }

        key = compute_correction_key(
            normalized_text_hash=input_hash, essay_prompt_id=str(essay_prompt.id),
            rubric_version=rubric_version, model_version=model_version,
            prompt_version=prompt_artifact.version, engine_version=_ENGINE_VERSION,
        )
        return {
            "correction_key": key, "rubric_version": rubric_version,
            "model_version": model_version, "prompt_version": prompt_artifact.version,
            "engine_version": _ENGINE_VERSION,
            "ai_output": output.model_dump(mode="json"),
            "final_scores": _apply_deterministic_scoring_rules(
                output, phase2_points, confirmed_alert_codes,
            ),
            "final_feedback": output.feedback.model_dump(mode="json"),
            "failure_reason": None,
        }

    async def _score_competencies_from_evidence(
        self, *, output: EssayEngineOutput, rubric_file: RubricFile,
    ) -> dict[str, int]:
        """Phase 2 of the correction pipeline (r3_correction_engine_v2): one
        small, evidence-only call per competency, run concurrently - see
        essay_prompts/competency_scoring_v1.py's module docstring for the
        calibration finding that motivated this (2026-09-28: the SAME essay
        text, corrected 4 times with an identical phase-1 prompt and a fixed
        seed, swung C1 from 0 to 80 points; isolating "decide the level"
        from "find the evidence" into its own small call answered
        identically across 5/5 repeated calls on two different essays).

        Raises ProviderError / json.JSONDecodeError / KeyError / ValueError
        on any failure - the caller (_run_ai) turns those into the same
        NEEDS_REVIEW failure_reason shape every other AI-side failure in
        this module already uses. Never called for FORMATIVO (see caller).
        """
        competency_by_code = {c.code: c for c in rubric_file.competencies}
        annotations_by_code: dict[str, list] = {code: [] for code in COMPETENCY_CODES}
        for annotation in output.annotations:
            annotations_by_code.setdefault(annotation.competency_code, []).append(annotation)
        mechanical_review = [
            {
                "category": m.category, "excerpt": m.excerpt,
                "suggested_form": m.suggested_form, "rule_explanation": m.rule_explanation,
            }
            for m in output.mechanical_review
        ]

        async def _score_one(code: str) -> tuple[str, int]:
            competency = competency_by_code[code]
            levels = [(level.points, level.descriptor) for level in competency.levels]
            annotations = [
                {"short_comment": a.short_comment, "long_comment": a.long_comment}
                for a in annotations_by_code.get(code, [])
            ]
            prompt_text = competency_scoring_v1.build_prompt(
                competency_code=code, competency_label=competency.official_title,
                levels=levels, annotations=annotations,
                # mechanical_review is exclusively C1's own domain (norma
                # padrao) - see MechanicalOccurrence.category's Literal.
                mechanical_review=mechanical_review if code == "C1" else (),
            )
            result = await self._get_text_provider().generate(
                TextGenerationRequest(prompt=prompt_text, seed=_CORRECTION_SEED)
            )
            payload = json.loads(result.text)
            points = int(payload["points"])
            if points not in (0, 40, 80, 120, 160, 200):
                raise ValueError(
                    f"competency scoring for {code} returned an invalid points "
                    f"value: {points!r} (must be one of 0/40/80/120/160/200)"
                )
            return code, points

        results = await asyncio.gather(*(_score_one(code) for code in COMPETENCY_CODES))
        return dict(results)

    async def _review_anula_redacao_alerts(
        self, *, output: EssayEngineOutput, essay_statement: str,
    ) -> set[str]:
        """Phase 2b of the correction pipeline (r3_correction_engine_v2): a
        small, focused re-check of any whole-essay-zero alert phase 1
        raised - see essay_prompts/alert_review_v1.py's module docstring
        for the calibration finding that motivated this (2026-09-28: a
        normal, gradeable essay - official C1=80 - got zeroed by an
        ANULA_REDACAO alert in 2 of 4 identical repeated corrections, the
        same inconsistency competency_scoring_v1 already fixed for the
        per-competency score, but for the single highest-stakes decision in
        the pipeline).

        Only ever calls the provider when output.alerts contains at least
        one _ANULA_REDACAO_ALERT_CODES candidate - the common case (no such
        alert) returns immediately, at no extra cost. Never introduces a
        code that was not already a candidate; non-ANULA_REDACAO alerts
        (OCR_DUVIDOSO, POSSIVEL_DUPLICIDADE, TANGENCIAMENTO_AO_TEMA) are
        passed through untouched, since only ANULA_REDACAO codes zero the
        whole essay - see _apply_deterministic_scoring_rules.

        Raises ProviderError / json.JSONDecodeError / KeyError / ValueError
        on any failure - same caller-side handling as
        _score_competencies_from_evidence. Never called for FORMATIVO.
        """
        all_codes = {alert.code for alert in output.alerts}
        candidates = [
            {"code": alert.code, "detail": alert.detail}
            for alert in output.alerts
            if alert.code in _ANULA_REDACAO_ALERT_CODES
        ]
        if not candidates:
            return all_codes

        prompt_text = alert_review_v1.build_prompt(
            essay_statement=essay_statement, alerts=candidates,
        )
        result = await self._get_text_provider().generate(
            TextGenerationRequest(prompt=prompt_text, seed=_CORRECTION_SEED)
        )
        payload = json.loads(result.text)
        confirmed = set(payload["confirmed_alert_codes"])
        if not isinstance(payload["confirmed_alert_codes"], list) or not all(
            isinstance(code, str) for code in payload["confirmed_alert_codes"]
        ):
            raise ValueError(
                f"alert review returned a non-list-of-strings "
                f"confirmed_alert_codes: {payload['confirmed_alert_codes']!r}"
            )
        # Never let the review invent a code that was not itself a
        # candidate - it may only narrow, never widen, the set phase 1 raised.
        candidate_codes = {c["code"] for c in candidates}
        confirmed &= candidate_codes
        passthrough_codes = all_codes - candidate_codes
        return confirmed | passthrough_codes

    async def _call_text_provider(
        self, *, submission: EssaySubmission, essay_prompt: EssayPrompt,
        rubric_payload: dict, prompt_artifact, include_scores: bool,
    ) -> tuple[dict, str, str, None, str]:
        text = submission.canonical_text
        prompt_text = prompt_artifact.build(
            anchor_mode="TEXT_OFFSET",
            essay_statement=_effective_essay_statement(submission, essay_prompt),
            rubric=rubric_payload, include_scores=include_scores, text=text,
        )
        result = await self._get_text_provider().generate(
            TextGenerationRequest(
                prompt=prompt_text,
                seed=_CORRECTION_SEED,
            )
        )
        raw_payload = json.loads(result.text)
        return raw_payload, result.model, text, None, submission.normalized_text_hash

    async def _call_image_provider(
        self, *, submission: EssaySubmission, essay_prompt: EssayPrompt,
        rubric_payload: dict, prompt_artifact, include_scores: bool,
    ) -> tuple[dict, str, None, dict[int, tuple[float, float]], str]:
        result = await self.session.execute(
            select(EssaySubmissionPage)
            .where(EssaySubmissionPage.essay_submission_id == submission.id)
            .order_by(EssaySubmissionPage.page_number)
        )
        pages = list(result.scalars().all())
        if not pages:
            raise ValueError(f"EssaySubmission {submission.id} has no pages to correct")
        for page in pages:
            if page.width is None or page.height is None:
                raise ValueError(
                    f"EssaySubmissionPage {page.id} (page {page.page_number}) has no "
                    "recorded dimensions - cannot validate IMAGE_REGION anchors against it"
                )

        prompt_text = prompt_artifact.build(
            anchor_mode="IMAGE_REGION",
            essay_statement=_effective_essay_statement(submission, essay_prompt),
            rubric=rubric_payload, include_scores=include_scores, page_count=len(pages),
        )
        image_paths = tuple(Path(page.storage_uri) for page in pages)
        request = EssayImageCorrectionRequest(
            image_paths=image_paths, mime_type=_guess_mime(image_paths[0]), prompt=prompt_text,
            seed=_CORRECTION_SEED,
        )
        result = await self._get_image_provider().correct_from_images(request)
        raw_payload = json.loads(result.text)
        page_boxes = {page.page_number: (page.width, page.height) for page in pages}
        input_hash = _image_pages_hash(pages)
        return raw_payload, result.model, None, page_boxes, input_hash


__all__ = ["EssayCorrectionService"]
