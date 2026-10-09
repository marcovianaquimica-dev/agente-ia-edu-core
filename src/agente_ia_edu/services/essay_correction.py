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
from ..essay_engine_contract.v6 import (
    COMPETENCY_CODES,
    CONTRACT_VERSION,
    EssayEngineOutput,
    Feedback,
    Scores,
)
from ..essay_prompts import competency_scoring_v3, get_essay_prompt
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
from .essay_input_reliability import (
    combine_input_reliability_signals,
    estimate_text_reliability_heuristic,
)
from .essay_zero_gate import ZeroGateDecision, evaluate_zero_gate
from .institution_settings import InstitutionSettingsService

logger = logging.getLogger(__name__)

_ENGINE_VERSION = "r3_correction_engine_v4"
_PROMPT_VERSION = "essay_correction_v16"
_QUALITY_GATE_VERSION = "quality_gate_v1"
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

#: Aspect label -> structured field, per competency, in rendering order. The
#: same pairs (same labels, same order) live in web/essay-report.js's
#: COMPETENCY_ASPECTS and essay_pdf_export.py's _COMPETENCY_ASPECTS - three
#: copies on purpose, one per runtime, never three different orders.
_STRUCTURED_ASPECTS: dict[str, tuple[tuple[str, str], ...]] = {
    "C2": (
        ("Tipologia textual", "c2_tipologia_textual"),
        ("Tema", "c2_tema"),
        ("Repertório sociocultural", "c2_repertorio_sociocultural"),
        ("Como melhorar", "c2_orientacao_melhoria"),
    ),
    "C3": (
        ("Projeto argumentativo", "c3_projeto_argumentativo"),
        ("Informações, fatos e opiniões", "c3_fatos_informacoes_opinioes"),
        ("Autoria", "c3_autoria"),
        ("Como melhorar", "c3_orientacao_melhoria"),
    ),
}


def _structured_rationale(output: EssayEngineOutput, code: str) -> dict[str, str] | None:
    """Rebuild phase 1's per-competency rationale for C2/C3 from contract v5's
    eight structured fields.

    Under v4 the phase-2a scorer read each competency's own
    CompetencyRationale (summary/strengths/growth_area) - 2026-09-28
    calibration finding: diffusely weak essays with few quotable errors had
    too little signal in annotations alone. v5 removed C2/C3 from
    ``rationales``, so without this the scorer would silently lose that
    signal for exactly two competencies - a scoring change this leva
    deliberately does NOT make (spec §2, "Não entrega").

    Returns None for C1/C4/C5 (they still carry a real rationale) and for any
    output missing one of the four fields (FORMATIVO, or older data) - the
    caller then falls back to the rationale it already had.
    """
    aspects = _STRUCTURED_ASPECTS.get(code)
    if aspects is None:
        return None
    values: list[tuple[str, str]] = []
    for label, field in aspects:
        value = getattr(output, field, None)
        if not isinstance(value, str) or not value.strip():
            return None
        values.append((label, value))
    *evidence, (_, improvement) = values
    return {
        "summary": " ".join(text for _, text in evidence),
        "strengths": "\n".join(f"{label}: {text}" for label, text in evidence),
        "growth_area": improvement,
    }


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
    alert_codes: set[str] | ZeroGateDecision | None = None,
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

    ``alert_codes`` accepts two shapes, kept both for backward compatibility:

    * a plain ``set[str] | None`` - the pre-Fase-C shape, still used by
      services/mass_correction_batch.py (a separate, deliberately-pinned
      pipeline this task does not touch - see its own module docstring).
      ``None`` falls back to ``{alert.code for alert in output.alerts}``,
      exactly as before any alert review existed.
    * a :class:`~agente_ia_edu.services.essay_zero_gate.ZeroGateDecision`
      (Fase C, spec 2026-10-06) - the new shape EssayCorrectionService._run_ai
      passes. ``decision="ZERAR"`` forces the whole-essay-zero branch below
      regardless of output.alerts (the Zero Gate's own independent read
      replaces phase 1's noisy alert detection entirely, the same way the
      old ``alert_codes`` override did); ``decision="NAO_ZERAR"`` uses
      output.alerts minus the eight ANULA_REDACAO codes (already ruled out
      by the Zero Gate) as the alert set, so TANGENCIAMENTO_AO_TEMA/
      OCR_DUVIDOSO/POSSIVEL_DUPLICIDADE still pass through unaffected.
      ``decision="ENCAMINHAR_REVISAO"`` is never passed here - the caller
      short-circuits before this function is ever called for that case (see
      _run_ai).

    Returns None when output.scores is None (FORMATIVO produces no grade to
    adjust - phase 2 never runs in that mode either, see correct()/_run_ai).
    """
    if output.scores is None:
        return None

    if isinstance(alert_codes, ZeroGateDecision):
        if alert_codes.decision == "ZERAR":
            alert_codes = set(_ANULA_REDACAO_ALERT_CODES)
        else:
            alert_codes = {alert.code for alert in output.alerts} - _ANULA_REDACAO_ALERT_CODES
    elif alert_codes is None:
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


#: Floor below which canonical_text_for_zero_gate (the real transcription,
#: or _approximate_text_for_zero_gate's substitute for IMAGE_REGION) is
#: treated as "no real text to evaluate independently" rather than sent to
#: the Zero Gate. 20 characters is well under any real sentence of
#: Portuguese essay prose - deliberately generous so this only ever catches
#: a genuinely empty or near-empty approximation (e.g. an engine output made
#: up entirely of GLOBAL-evidence annotations, which carry no anchor/
#: read_text at all - see _run_ai's own comment at the call site), never a
#: short-but-real one.
_ZERO_GATE_MIN_TEXT_LENGTH = 20


def _approximate_text_for_zero_gate(output: EssayEngineOutput) -> str:
    """Best-effort substitute for canonical_text on an IMAGE_REGION
    submission, which never has one (R2's documented consequence of skipped
    transcription - see essay_submission.py's confirm_submission). The Zero
    Gate's whole reason for existing (essay_zero_gate.py's module docstring)
    is reading the FULL essay text independently, from scratch - but no
    full-text reconstruction mechanism exists anywhere in this codebase for
    IMAGE_REGION (confirmed by direct investigation for Task 11:
    EssaySubmissionPage.ocr_tokens/reviewed_text are populated only when
    transcription runs, i.e. only for TEXT_OFFSET; validate_engine_output
    never builds one either).

    This joins every annotation's own ImageRegionAnchor.read_text (what
    phase 1 itself already read off that specific line) in page/line order,
    deduplicated - real text the model already produced, not invented here.
    It is a real degradation relative to TEXT_OFFSET: it only covers
    annotated lines, never lines with no annotation, so the Zero Gate's
    independent read of an IMAGE_REGION essay is weaker than for a
    TEXT_OFFSET one. A dedicated image-based Zero Gate call (sending the
    page images themselves, the way phase 1 already does) would close this
    gap properly but is out of this task's scope - flagged in Task 11's
    report.
    """
    lines: list[tuple[int, int, str]] = []
    seen_text: set[str] = set()
    for annotation in output.annotations:
        anchor = annotation.anchor
        if anchor is None or anchor.type != "IMAGE_REGION":
            continue
        if anchor.read_text in seen_text:
            continue
        seen_text.add(anchor.read_text)
        lines.append((anchor.page, anchor.line, anchor.read_text))
    lines.sort(key=lambda item: (item[0], item[1]))
    return "\n".join(read_text for _, _, read_text in lines)


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
        if correction.quality_gate_status == "UNRELIABLE_NEEDS_REVIEW":
            # _run_ai already returns final_scores=None for this case - this
            # guard is defensive, for correct()/retry() calling this method
            # on a result already marked UNRELIABLE_NEEDS_REVIEW by some
            # other future path (spec Fase B).
            correction.status = "NEEDS_REVIEW"
            return
        if correction.zero_gate_decision is not None and correction.zero_gate_decision.get(
            "requires_human_review"
        ):
            # Fase C: a genuine Zero Gate ENCAMINHAR_REVISAO (3-way
            # disagreement that never resolved - see essay_zero_gate.py's
            # docstring) must NEVER fall through to a normal grade decision,
            # even though ai_output IS populated here (kept for audit - the
            # reviewing teacher should see what the model actually produced,
            # unlike a genuine provider failure where there is nothing to
            # show). Without this guard, the "ai_output is None" check right
            # below would miss this case (ai_output is not None) and fall
            # into the "total is None" branch further down, which sets
            # PENDING_REVIEW - violating
            # ck_essay_corrections_failure_reason_requires_needs_review,
            # since _run_ai also sets a non-null failure_reason for this
            # outcome. Same "needs a human, not a silent auto-decision"
            # contract as the Quality Gate's own short-circuit above.
            correction.status = "NEEDS_REVIEW"
            return
        if correction.ai_output is None:
            correction.status = "NEEDS_REVIEW"
            return

        settings = await InstitutionSettingsService(self.session).get_settings(submission.school_id)
        total = (correction.final_scores or {}).get("total")
        if total is None:
            # Neither mode may ever auto-publish without a grade - a null
            # score is either a malformed AI response or a real edge case,
            # either way it needs a human, not a silent auto-approval.
            # _run_ai always asks for a grade now (include_scores=True
            # regardless of correction_mode - 2026-10-05), so this guard
            # is no longer AVALIATIVO-only.
            correction.status = "PENDING_REVIEW"
            return
        if settings.correction_mode == "FORMATIVO":
            # Once a grade exists, FORMATIVO still never requires teacher
            # sign-off against it (no validation_threshold/
            # validation_enabled check) - that's the entire remaining
            # difference from AVALIATIVO: the grade is real, it's just
            # never gated behind validation.
            self._publish(correction)
            return

        assignment = await self.session.get(PromptAssignment, submission.prompt_assignment_id)
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
                "input_tokens": None, "output_tokens": None,
                "quality_gate_status": None, "quality_gate_version": None,
                "zero_gate_decision": None, "zero_gate_version": None,
            }
        rubric_version = rubric_file.rubric_version
        failure_fields = {
            "correction_key": None, "rubric_version": rubric_version,
            "model_version": None, "prompt_version": _PROMPT_VERSION,
            "engine_version": _ENGINE_VERSION, "ai_output": None,
            "final_scores": None, "final_feedback": None, "failure_reason": None,
            "input_tokens": None, "output_tokens": None,
            "quality_gate_status": None, "quality_gate_version": None,
            "zero_gate_decision": None, "zero_gate_version": None,
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
        # Decisao do usuario 2026-10-05, revertendo a leitura original do
        # spec R3 §4 step 4 (ali, FORMATIVO pedia scores=null): mesmo
        # FORMATIVO deve ser corrigido COM nota - o que correction_mode
        # decide nao e mais se a nota existe, e sim se ela e VALIDADA como
        # pontuacao oficial (ver _apply_review_policy abaixo, que continua
        # publicando FORMATIVO sem checar validation_threshold/
        # validation_enabled, so exigindo que a nota exista mesmo assim).
        include_scores = True

        try:
            if submission.anchor_mode == "TEXT_OFFSET":
                raw_payload, model_version, text, page_boxes, input_hash, input_tokens, output_tokens = (
                    await self._call_text_provider(
                        submission=submission, essay_prompt=essay_prompt,
                        rubric_payload=rubric_payload, prompt_artifact=prompt_artifact,
                        include_scores=include_scores,
                    )
                )
            else:
                raw_payload, model_version, text, page_boxes, input_hash, input_tokens, output_tokens = (
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
                output_model=EssayEngineOutput,
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
                "input_tokens": input_tokens, "output_tokens": output_tokens,
            }

        ocr_average_confidence = None
        if submission.anchor_mode == "IMAGE_REGION":
            pages_result = await self.session.execute(
                select(EssaySubmissionPage).where(EssaySubmissionPage.essay_submission_id == submission.id)
            )
            all_tokens = [
                t for page in pages_result.scalars().all() for t in (page.ocr_tokens or [])
            ]
            if all_tokens:
                ocr_average_confidence = sum(t["confidence"] for t in all_tokens) / len(all_tokens)

        heuristic_ratio = estimate_text_reliability_heuristic(text) if text is not None else None
        quality_gate_status = combine_input_reliability_signals(
            heuristic_ratio=heuristic_ratio,
            model_status=output.input_reliability.status,
            ocr_average_confidence=ocr_average_confidence,
        )

        if quality_gate_status == "UNRELIABLE_NEEDS_REVIEW":
            # Quality Gate decide ANTES de qualquer fase 2 - nunca gasta uma
            # chamada de IA refinando uma nota sobre leitura nao confiavel, e
            # NUNCA publica nota como se fosse valida (spec Fase B).
            key = compute_correction_key(
                normalized_text_hash=input_hash, essay_prompt_id=str(essay_prompt.id),
                rubric_version=rubric_version, model_version=model_version,
                prompt_version=prompt_artifact.version, engine_version=_ENGINE_VERSION,
            )
            # O modelo frequentemente tambem emite um alert de
            # _ANULA_REDACAO_ALERT_CODES (TEXTO_INSUFICIENTE confirmado live,
            # 4/4 em Larissa/Joao Miguel - Task 9) na MESMA resposta de fase 1
            # que ja carrega input_reliability=UNRELIABLE_NEEDS_REVIEW. Spec
            # Fase B e explicita: confiabilidade de entrada "nunca aparece em
            # alerts, nunca passa por _ANULA_REDACAO_ALERT_CODES" - uma vez
            # que o Quality Gate decidiu que a LEITURA nao e confiavel,
            # nenhum alert de julgamento pedagogico com semantica de
            # zerar-a-redacao pode ficar exposto no ai_output persistido como
            # se fosse um sinal confiavel. Alerts fora desse conjunto
            # (OCR_DUVIDOSO, POSSIVEL_DUPLICIDADE, TANGENCIAMENTO_AO_TEMA)
            # nunca tiveram semantica de zerar e continuam, como sinal
            # informativo. Note que final_scores ja e None de qualquer forma
            # - este filtro protege apenas o ai_output exposto, nao a nota.
            ai_output = output.model_dump(mode="json")
            ai_output["alerts"] = [
                alert for alert in ai_output["alerts"]
                if alert["code"] not in _ANULA_REDACAO_ALERT_CODES
            ]
            return {
                "correction_key": key, "rubric_version": rubric_version,
                "model_version": model_version, "prompt_version": prompt_artifact.version,
                "engine_version": _ENGINE_VERSION,
                "ai_output": ai_output,
                "final_scores": None, "final_feedback": output.feedback.model_dump(mode="json"),
                "failure_reason": f"QUALITY_GATE_UNRELIABLE: {output.input_reliability.rationale}",
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "quality_gate_status": quality_gate_status, "quality_gate_version": _QUALITY_GATE_VERSION,
                "zero_gate_decision": None, "zero_gate_version": None,
            }

        phase2_points: dict[str, int] | None = None
        zero_gate_decision: ZeroGateDecision | None = None
        if output.scores is not None:
            # Zero Gate (Fase C, spec 2026-10-06): resolves BEFORE C1-C5
            # scoring, and ALWAYS runs - unlike the phase-2b alert review it
            # replaces, it never depends on phase 1 having already raised an
            # ANULA_REDACAO candidate (see essay_zero_gate.py's docstring for
            # why: that dependency is exactly what let 7 of 8 real
            # corrupted-text cases slip through undetected, since phase 1
            # never raised a candidate for them to review in the first
            # place). Competency scoring only runs afterwards, and only when
            # the Zero Gate itself decided NAO_ZERAR - a ZERAR essay has no
            # competency score left to refine, and an ENCAMINHAR_REVISAO one
            # short-circuits below before ever reaching competency scoring.
            canonical_text_for_zero_gate = (
                text if text is not None else _approximate_text_for_zero_gate(output)
            )
            if len(canonical_text_for_zero_gate.strip()) < _ZERO_GATE_MIN_TEXT_LENGTH:
                # IMAGE_REGION has no full transcription, so
                # _approximate_text_for_zero_gate substitutes the text of
                # every annotation's own ImageRegionAnchor.read_text - but
                # Annotation.anchor is nullable (evidence_kind=GLOBAL has
                # none) and nothing requires a minimum count of LOCALIZED
                # annotations. An engine output made up entirely of GLOBAL
                # annotations produces an EMPTY approximation here. Sending
                # that straight to evaluate_zero_gate would ask the model to
                # judge "" against the eight zero codes - TEXTO_INSUFICIENTE/
                # TEXTO_ILEGIVEL are both plausible verdicts on an empty
                # string, and a ZERAR there would persist a REAL, all-zero
                # final_scores, not a NEEDS_REVIEW - exactly the fabricated-
                # zero failure class this whole plan exists to eliminate,
                # now reachable on IMAGE_REGION, the default submission mode
                # (InstitutionSettings.transcription_enabled=False). Instead,
                # treat this the same as a genuine Zero Gate
                # ENCAMINHAR_REVISAO below: no model call, no risk of a
                # confident zero on no real evidence, same as the Quality
                # Gate's own principle that a reliability gap alone must
                # never produce a confident zero. 20 chars is a small,
                # deliberately generous floor - well under one real sentence
                # of Portuguese essay text, so it only ever triggers on
                # genuinely empty/near-empty approximations, never on a
                # short-but-real one.
                zero_gate_decision = ZeroGateDecision(
                    decision="ENCAMINHAR_REVISAO", rule_code=None,
                    evidence=(
                        "texto aproximado para IMAGE_REGION esta vazio ou "
                        "quase vazio - Zero Gate nao pode avaliar "
                        "independentemente sem anotacoes com texto"
                    ),
                    confidence=None, requires_human_review=True,
                )
            else:
                try:
                    zero_gate_decision = await evaluate_zero_gate(
                        canonical_text=canonical_text_for_zero_gate,
                        essay_statement=_effective_essay_statement(submission, essay_prompt),
                        rubric_payload=rubric_payload,
                        text_provider=self._get_text_provider(),
                    )
                except ProviderError as exc:
                    logger.warning(
                        "essay correction for submission %s: zero gate provider "
                        "error: %s", submission.id, exc,
                    )
                    return {
                        **failure_fields, "model_version": model_version,
                        "failure_reason": f"ZeroGateFailed: {type(exc).__name__}: {exc}",
                    }
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    logger.warning(
                        "essay correction for submission %s: zero gate returned "
                        "an unusable response: %s", submission.id, exc,
                    )
                    return {
                        **failure_fields, "model_version": model_version,
                        "failure_reason": f"ZeroGateFailed: {type(exc).__name__}: {exc}",
                    }

            if zero_gate_decision.decision == "ENCAMINHAR_REVISAO":
                # Genuine 3-way disagreement that never resolved - the whole
                # point of this outcome (see essay_zero_gate.py's docstring)
                # is that it must NEVER fall through to a normal pedagogical
                # grade as if the doubt did not exist. final_scores stays
                # None, exactly like the Quality Gate's own
                # UNRELIABLE_NEEDS_REVIEW short-circuit above.
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
                    "final_scores": None, "final_feedback": output.feedback.model_dump(mode="json"),
                    "failure_reason": f"ZERO_GATE_UNCERTAIN: {zero_gate_decision.evidence}",
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "quality_gate_status": quality_gate_status, "quality_gate_version": _QUALITY_GATE_VERSION,
                    "zero_gate_decision": {
                        "decision": zero_gate_decision.decision,
                        "rule_code": zero_gate_decision.rule_code,
                        "evidence": zero_gate_decision.evidence,
                        "confidence": zero_gate_decision.confidence,
                        "requires_human_review": zero_gate_decision.requires_human_review,
                    },
                    "zero_gate_version": zero_gate_decision.rule_version,
                }

            if zero_gate_decision.decision == "NAO_ZERAR":
                # Only spend a competency-scoring call once the Zero Gate
                # has ruled out every whole-essay-zero code - a ZERAR essay
                # has nothing left to refine (every competency goes to 0
                # regardless), so this used to run unconditionally
                # (concurrently with the old alert review) but now only runs
                # after the Zero Gate's own verdict is known.
                try:
                    phase2_points = await self._score_competencies_from_evidence(
                        output=output, rubric_file=rubric_file,
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
            # decision == "ZERAR": phase2_points stays None on purpose -
            # _apply_deterministic_scoring_rules zeroes every competency,
            # there is no score left to refine.

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
                output, phase2_points, zero_gate_decision,
            ),
            "final_feedback": output.feedback.model_dump(mode="json"),
            "failure_reason": None,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "quality_gate_status": quality_gate_status, "quality_gate_version": _QUALITY_GATE_VERSION,
            "zero_gate_decision": (
                {
                    "decision": zero_gate_decision.decision,
                    "rule_code": zero_gate_decision.rule_code,
                    "evidence": zero_gate_decision.evidence,
                    "confidence": zero_gate_decision.confidence,
                    "requires_human_review": zero_gate_decision.requires_human_review,
                }
                if zero_gate_decision is not None else None
            ),
            "zero_gate_version": zero_gate_decision.rule_version if zero_gate_decision is not None else None,
        }

    async def _score_competencies_from_evidence(
        self, *, output: EssayEngineOutput, rubric_file: RubricFile,
    ) -> dict[str, int]:
        """Phase 2 of the correction pipeline (r3_correction_engine_v3): one
        small, evidence-only call per competency, run concurrently - see
        essay_prompts/competency_scoring_v1.py's module docstring for the
        calibration finding that motivated this (2026-09-28: the SAME essay
        text, corrected 4 times with an identical phase-1 prompt and a fixed
        seed, swung C1 from 0 to 80 points; isolating "decide the level"
        from "find the evidence" into its own small call answered
        identically across 5/5 repeated calls on two different essays), and
        competency_scoring_v2.py's for a later finding (2026-10-05: the top
        band was awarded far more often than real ENEM data supports).

        Raises ProviderError / json.JSONDecodeError / KeyError / ValueError
        on any failure - the caller (_run_ai) turns those into the same
        NEEDS_REVIEW failure_reason shape every other AI-side failure in
        this module already uses. Only called when output.scores is not
        None (see caller) - that used to mean "never for FORMATIVO", but
        FORMATIVO now gets a real grade too (2026-10-05, see _run_ai).
        """
        competency_by_code = {c.code: c for c in rubric_file.competencies}
        annotations_by_code: dict[str, list] = {code: [] for code in COMPETENCY_CODES}
        for annotation in output.annotations:
            annotations_by_code.setdefault(annotation.competency_code, []).append(annotation)
        rationale_by_code = {r.competency_code: r for r in output.rationales}
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
            rationale_obj = rationale_by_code.get(code)
            rationale = _structured_rationale(output, code)
            if rationale is None and rationale_obj is not None:
                rationale = {
                    "summary": rationale_obj.summary,
                    "strengths": rationale_obj.strengths,
                    "growth_area": rationale_obj.growth_area,
                }
            prompt_text = competency_scoring_v3.build_prompt(
                competency_code=code, competency_label=competency.official_title,
                levels=levels, annotations=annotations,
                # mechanical_review is exclusively C1's own domain (norma
                # padrao) - see MechanicalOccurrence.category's Literal.
                mechanical_review=mechanical_review if code == "C1" else (),
                rationale=rationale,
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

    async def _call_text_provider(
        self, *, submission: EssaySubmission, essay_prompt: EssayPrompt,
        rubric_payload: dict, prompt_artifact, include_scores: bool,
    ) -> tuple[dict, str, str, None, str, int | None, int | None]:
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
        return (
            raw_payload, result.model, text, None, submission.normalized_text_hash,
            result.input_tokens, result.output_tokens,
        )

    async def _call_image_provider(
        self, *, submission: EssaySubmission, essay_prompt: EssayPrompt,
        rubric_payload: dict, prompt_artifact, include_scores: bool,
    ) -> tuple[dict, str, None, dict[int, tuple[float, float]], str, int | None, int | None]:
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
        return (
            raw_payload, result.model, None, page_boxes, input_hash,
            result.input_tokens, result.output_tokens,
        )


__all__ = ["EssayCorrectionService"]
