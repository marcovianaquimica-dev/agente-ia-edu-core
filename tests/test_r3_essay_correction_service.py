# tests/test_r3_essay_correction_service.py
import json
import re
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    EssaySubmissionPage,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.providers.errors import ProviderTimeoutError
from agente_ia_edu.providers.models import TextGenerationResult
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_correction import EssayCorrectionService
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder
from agente_ia_edu.services.institution_settings import InstitutionSettingsService


class _StubTextProvider:
    """Answers all three shapes of call _run_ai now makes when scores are
    present: the phase-1 (essay_correction_v14) call always gets ``text``
    back verbatim, same as before phase 2 existed; a phase-2a
    (competency_scoring_v1) call - recognized by the "SCORING_RULES:"
    marker only that prompt ever writes - gets a small
    {"points": ..., "reasoning": ...} response, using per_competency_points
    (default: 160 for all five, matching _happy_payload's own default) so
    existing final_scores assertions keep meaning what they said before
    phase 2 existed; a phase-2b (alert_review_v1) call - recognized by the
    "ALERTS_TO_REVIEW:" marker - gets a {"confirmed_alert_codes": [...]}
    response that by default CONFIRMS every code it was asked to review
    (reject_alert_codes narrows that to a specific subset, for tests of the
    rejection path)."""

    def __init__(
        self, *, text="", model="gpt-test", raise_error=None, per_competency_points=None,
        phase2_raise_error=None, phase2_text=None, reject_alert_codes=None,
        alert_review_raise_error=None, alert_review_text=None,
    ):
        self._text = text
        self.model = model
        self._raise_error = raise_error
        self._phase2_raise_error = phase2_raise_error
        self._phase2_text = phase2_text
        self._reject_alert_codes = reject_alert_codes or set()
        self._alert_review_raise_error = alert_review_raise_error
        self._alert_review_text = alert_review_text
        self.last_request = None
        # Separate from last_request: phase 2 runs several concurrent calls
        # AFTER phase 1, so plain last_request ends up being whichever one
        # happened to finish last - existing tests asserting on the phase-1
        # prompt's own content (SCORING_MODE, rubric signals, etc.) want
        # THIS one instead.
        self.last_phase1_request = None
        self.call_count = 0
        self._per_competency_points = per_competency_points or {
            c: 160 for c in ("C1", "C2", "C3", "C4", "C5")
        }

    async def generate(self, request):
        self.last_request = request
        self.call_count += 1
        if "SCORING_RULES:" in request.prompt:
            if self._phase2_raise_error is not None:
                raise self._phase2_raise_error
            if self._phase2_text is not None:
                return TextGenerationResult(text=self._phase2_text, provider="stub", model=self.model)
            match = re.search(r"RUBRIC_(C\d)", request.prompt)
            code = match.group(1) if match else "C1"
            points = self._per_competency_points.get(code, 160)
            return TextGenerationResult(
                text=json.dumps({"points": points, "reasoning": "stub"}),
                provider="stub", model=self.model,
            )
        if "ALERTS_TO_REVIEW:" in request.prompt:
            if self._alert_review_raise_error is not None:
                raise self._alert_review_raise_error
            if self._alert_review_text is not None:
                return TextGenerationResult(text=self._alert_review_text, provider="stub", model=self.model)
            candidate_codes = re.findall(r"- ([A-Z_]+): ", request.prompt)
            confirmed = [c for c in candidate_codes if c not in self._reject_alert_codes]
            return TextGenerationResult(
                text=json.dumps({"confirmed_alert_codes": confirmed, "reasoning": "stub"}),
                provider="stub", model=self.model,
            )
        if self._raise_error is not None:
            raise self._raise_error
        self.last_phase1_request = request
        return TextGenerationResult(text=self._text, provider="stub", model=self.model)


class _StubImageProvider:
    def __init__(self, *, text="", model="gpt-vision-test", raise_error=None):
        self._text = text
        self.model = model
        self._raise_error = raise_error
        self.last_request = None

    async def correct_from_images(self, request):
        self.last_request = request
        if self._raise_error is not None:
            raise self._raise_error
        return TextGenerationResult(text=self._text, provider="stub", model=self.model)


def _happy_payload(
    *, anchor_mode: str, text: str = "", page: int = 1, box=(800.0, 600.0),
    quote_override: str | None = None, include_scores: bool = True,
    alerts: list | None = None, respeita_direitos_humanos: bool = True,
    per_competency_points: dict | None = None,
) -> str:
    import json

    if anchor_mode == "TEXT_OFFSET":
        quote = quote_override if quote_override is not None else text[0:10]
        anchor = {"type": "TEXT_OFFSET", "start": 0, "end": 10, "quote": quote}
    else:
        anchor = {
            "type": "IMAGE_REGION", "page": page, "line": 2, "total_lines": 30,
            "read_text": "trecho lido na imagem",
        }
    points = per_competency_points or {c: 160 for c in ("C1", "C2", "C3", "C4", "C5")}
    scores = (
        {
            "per_competency": {
                code: {"points": p, "confidence": 0.9} for code, p in points.items()
            },
            "total": sum(points.values()),
        }
        if include_scores
        else None
    )
    return json.dumps(
        {
            "scores": scores,
            "rationales": [
                {
                    "competency_code": "C1", "summary": "Boa norma padrao.",
                    "strengths": "Boa norma padrao.", "growth_area": "Aprofundar repertorio.",
                    "signal_keys": [],
                }
            ],
            "annotations": [
                {
                    "letter": "A", "competency_code": "C1", "kind": "ACERTO",
                    "evidence_kind": "LOCALIZED", "anchor": anchor,
                    "short_comment": "Bom uso da norma.",
                    "long_comment": "Uso consistente da norma padrao ao longo do texto.",
                    "pedagogical_suggestion": None, "signal_keys": [],
                }
            ],
            "rewrites": [],
            "feedback": {
                "strengths": ["Boa argumentacao"],
                "improvements": ["Aprofundar a proposta de intervencao"],
                "next_essay_strategy": "Revisar conectivos.",
            },
            "intervention": {
                "agente": "Estado", "acao": "criar programa",
                "meio_modo": "por meio de campanhas", "finalidade": "reduzir o problema",
                "detalhamento": "com fiscalizacao",
                "respeita_direitos_humanos": respeita_direitos_humanos,
            },
            "alerts": alerts or [],
            "intro_message": "Ola! Vamos ver como foi sua redacao.",
            "closing_message": "Continue praticando, voce esta no caminho certo.",
        }
    )


class EssayCorrectionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(load_rubric_file("enem_2025"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _submission(
        self, session, code, *, anchor_mode="TEXT_OFFSET", validation_enabled=True,
        correction_mode=None, validation_threshold_points=None, with_pages=False,
        student_declared_theme=None,
    ) -> EssaySubmission:
        school = School(id=uuid.uuid4(), code=f"CORR-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="grade", external_id=f"GRADE-{code}",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}")
        session.add(student)
        await session.flush()
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte sobre X.",
            year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
        )
        session.add(prompt)
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="teacher:t",
            validation_enabled=validation_enabled,
        )
        session.add(assignment)
        await session.flush()

        if correction_mode is not None:
            await InstitutionSettingsService(session).configure(
                school.id, performed_by_external_id="test",
                correction_mode=correction_mode,
                validation_threshold_points=validation_threshold_points,
            )

        text = "Texto qualquer da redacao para fins de teste de correcao automatica."
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED" if anchor_mode == "TEXT_OFFSET" else "PHOTO",
            anchor_mode=anchor_mode, status="SUBMITTED",
            canonical_text=text if anchor_mode == "TEXT_OFFSET" else None,
            normalized_text_hash="a" * 64 if anchor_mode == "TEXT_OFFSET" else None,
            submitted_at=datetime.now(timezone.utc),
            student_declared_theme=student_declared_theme,
        )
        session.add(submission)
        await session.flush()

        if with_pages:
            session.add(EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id, page_number=1,
                storage_uri="/tmp/r3_fake_page_1.png", width=800.0, height=600.0,
            ))
            await session.flush()

        await session.commit()
        return submission

    async def test_text_offset_formative_auto_publishes(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "1", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNotNone(correction.published_at)
            self.assertIsNotNone(correction.reviewed_at)
            self.assertIsNone(correction.reviewed_by_external_identity)
            self.assertIsNotNone(correction.ai_output)
            self.assertEqual(correction.final_scores["total"], 800)
            self.assertEqual(correction.model_version, "gpt-test")
            self.assertIsNotNone(correction.correction_key)
            self.assertIn("SCORING_MODE: FORMATIVO", provider.last_phase1_request.prompt)

    async def test_real_rubric_signals_reach_the_assembled_prompt(self):
        """End-to-end: rubrics/enem_2025.yaml's controlled signal vocabulary
        (seeded by EssayRubricSeeder in asyncSetUp) flows through
        _rubric_payload into the actual prompt sent to the provider - not
        just the essay_prompts-level unit test with a hand-built rubric
        dict."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "23", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            await service.correct(submission.id)

            prompt = provider.last_phase1_request.prompt
            self.assertIn("estrutura_sintatica", prompt)
            self.assertIn("Estrutura sintática", prompt)

    async def test_fuga_ao_tema_alert_zeroes_final_scores_but_not_ai_output(self):
        """End-to-end: essay_engine_contract v3's alert codes flow through
        real validation into _apply_deterministic_scoring_rules. ai_output
        keeps the model's own (unrealistically high) scores exactly as
        reported; final_scores - what's actually published - is zeroed by
        the ENEM 2025 rubric's own ANULA_REDACAO rule for total fuga ao
        tema, deterministically, not because the model did the arithmetic."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "20", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}],
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertEqual(correction.ai_output["scores"]["total"], 800)
            self.assertEqual(correction.final_scores["total"], 0)
            for code in ("C1", "C2", "C3", "C4", "C5"):
                self.assertEqual(correction.final_scores["per_competency"][code]["points"], 0)

    async def test_human_rights_violation_zeroes_only_c5_in_final_scores(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "21", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                respeita_direitos_humanos=False,
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.ai_output["scores"]["total"], 800)
            self.assertEqual(correction.final_scores["per_competency"]["C5"]["points"], 0)
            self.assertEqual(correction.final_scores["per_competency"]["C1"]["points"], 160)
            self.assertEqual(correction.final_scores["total"], 640)

    async def test_tangenciamento_alert_caps_c3_and_c5_in_final_scores(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "22", correction_mode="FORMATIVO")
            points = {"C1": 160, "C2": 80, "C3": 200, "C4": 160, "C5": 200}
            provider = _StubTextProvider(
                text=_happy_payload(
                    anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                    alerts=[{"code": "TANGENCIAMENTO_AO_TEMA", "detail": "So aborda o assunto amplo."}],
                    per_competency_points=points,
                ),
                per_competency_points=points,
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.ai_output["scores"]["per_competency"]["C3"]["points"], 200)
            self.assertEqual(correction.final_scores["per_competency"]["C3"]["points"], 40)
            self.assertEqual(correction.final_scores["per_competency"]["C5"]["points"], 40)
            self.assertEqual(correction.final_scores["per_competency"]["C2"]["points"], 80)
            self.assertEqual(correction.final_scores["total"], 160 + 80 + 40 + 160 + 40)

    async def test_texto_ilegivel_alert_zeroes_final_scores_end_to_end(self):
        """One representative end-to-end check for the v4/v12 ANULA_REDACAO
        additions - the pure-function tests in
        test_r3_deterministic_scoring_rules.py already cover all five new
        codes individually."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "24", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                alerts=[{"code": "TEXTO_ILEGIVEL", "detail": "Nao foi possivel ler o texto."}],
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.ai_output["scores"]["total"], 800)
            self.assertEqual(correction.final_scores["total"], 0)

    async def test_text_offset_avaliativo_with_validation_enabled_holds_for_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "2", correction_mode="AVALIATIVO", validation_enabled=True,
            )
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "PENDING_REVIEW")
            self.assertIsNone(correction.published_at)
            self.assertIsNone(correction.reviewed_at)
            self.assertIn("SCORING_MODE: AVALIATIVO", provider.last_phase1_request.prompt)

    async def test_tema_livre_always_auto_publishes_even_in_avaliativo(self):
        """"Tema livre" (EssaySubmission.student_declared_theme) has no
        official gabarito for a teacher to validate the grade against, so it
        always auto-publishes - the same AVALIATIVO+validation_enabled=True
        settings that hold a normal submission for review (see the test
        immediately above) must NOT hold this one."""
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "25", correction_mode="AVALIATIVO", validation_enabled=True,
                student_declared_theme="Um tema escolhido pelo aluno",
            )
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNotNone(correction.published_at)
            self.assertIsNotNone(correction.reviewed_at)
            self.assertIsNone(correction.reviewed_by_external_identity)

    async def test_tema_livre_with_null_scores_still_needs_review(self):
        """The tema-livre auto-publish bypass sits AFTER the malformed-
        response safety net - a null total is still a data-integrity
        problem regardless of theme, and must still stop at PENDING_REVIEW."""
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "26", correction_mode="AVALIATIVO", validation_enabled=False,
                student_declared_theme="Um tema escolhido pelo aluno",
            )
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text, include_scores=False,
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "PENDING_REVIEW")

    async def test_text_offset_avaliativo_with_validation_disabled_auto_publishes(self):
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "3", correction_mode="AVALIATIVO", validation_enabled=False,
            )
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")

    async def test_below_threshold_forces_review_even_when_validation_disabled(self):
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "4", correction_mode="AVALIATIVO", validation_enabled=False,
                validation_threshold_points=850,
            )
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.final_scores["total"], 800)
            self.assertEqual(correction.status, "PENDING_REVIEW")

    async def test_provider_failure_becomes_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "5", correction_mode="FORMATIVO")
            provider = _StubTextProvider(raise_error=ProviderTimeoutError("boom"))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNone(correction.ai_output)
            self.assertIn("ProviderTimeoutError", correction.failure_reason)

    async def test_quote_mismatch_becomes_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "6", correction_mode="FORMATIVO")
            bad_payload = _happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                quote_override="isto nao esta no texto original",
            )
            provider = _StubTextProvider(text=bad_payload)
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNone(correction.ai_output)
            self.assertIn("QUOTE_DOES_NOT_MATCH_TEXT", correction.failure_reason)

    async def test_image_region_happy_path(self):
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "7", anchor_mode="IMAGE_REGION", correction_mode="FORMATIVO",
                with_pages=True,
            )
            provider = _StubImageProvider(text=_happy_payload(anchor_mode="IMAGE_REGION"))
            service = EssayCorrectionService(
                session, image_provider=provider, text_provider=_StubTextProvider(),
            )
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNotNone(correction.ai_output)
            self.assertEqual(correction.ai_output["identification"]["anchor_mode"], "IMAGE_REGION")

    async def test_correct_rejects_a_submission_that_is_not_submitted(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "8", correction_mode="FORMATIVO")
            submission.status = "SUPERSEDED"
            await session.commit()
            service = EssayCorrectionService(session, text_provider=_StubTextProvider())
            with self.assertRaises(ValueError):
                await service.correct(submission.id)

    async def test_correct_is_idempotent_for_a_submission_that_already_has_a_correction(self):
        """Spec §4 step 2: a network retry of the same confirm call must
        never re-invoke the AI or fail - it must return the same row."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "9", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            first = await service.correct(submission.id)
            second = await service.correct(submission.id)

            self.assertEqual(first.id, second.id)
            count = await session.scalar(select(func.count()).select_from(EssayCorrection))
            self.assertEqual(count, 1)

    async def test_retry_reprocesses_a_needs_review_correction_in_place(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "10", correction_mode="FORMATIVO")
            failing = _StubTextProvider(raise_error=ProviderTimeoutError("boom"))
            service = EssayCorrectionService(session, text_provider=failing)
            correction = await service.correct(submission.id)
            self.assertEqual(correction.status, "NEEDS_REVIEW")
            correction_id = correction.id

            service._text_provider = _StubTextProvider(
                text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text)
            )
            retried = await service.retry(correction_id)

            self.assertEqual(retried.id, correction_id)
            self.assertEqual(retried.status, "APPROVED")
            count = await session.scalar(select(func.count()).select_from(EssayCorrection))
            self.assertEqual(count, 1)

    async def test_retry_rejects_a_correction_that_is_not_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "11", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)
            with self.assertRaises(ValueError):
                await service.retry(correction.id)

    async def test_image_region_pages_are_sent_in_page_number_order(self):
        """Pages are inserted out of page-number order on purpose, so this
        only passes if the service actually orders by page_number rather
        than by insertion/id order - and it checks the real outbound
        request (image_paths, PAGE_COUNT) plus that correction_key was
        computed from the real page set, not just that status ended up
        APPROVED."""
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "12", anchor_mode="IMAGE_REGION", correction_mode="FORMATIVO",
            )
            session.add(EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id, page_number=2,
                storage_uri="/tmp/r3_fake_page_2.png", width=900.0, height=700.0,
            ))
            session.add(EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id, page_number=1,
                storage_uri="/tmp/r3_fake_page_1.png", width=800.0, height=600.0,
            ))
            await session.commit()

            provider = _StubImageProvider(
                text=_happy_payload(anchor_mode="IMAGE_REGION", page=1, box=(800.0, 600.0))
            )
            service = EssayCorrectionService(
                session, image_provider=provider, text_provider=_StubTextProvider(),
            )
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNotNone(correction.correction_key)
            self.assertEqual(
                provider.last_request.image_paths,
                (Path("/tmp/r3_fake_page_1.png"), Path("/tmp/r3_fake_page_2.png")),
            )
            self.assertIn("PAGE_COUNT: 2", provider.last_request.prompt)

    async def test_formativo_real_shape_with_null_scores_persists_no_scores(self):
        """Every other FORMATIVO test uses a stub that returns a full score
        block regardless of what SCORING_MODE asked for. The real model,
        asked for FORMATIVO, returns scores=null (RESPONSE_SCHEMA's actual
        shape for that mode) - this is the first test to send that shape
        through validation and the DB constraints."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "13", correction_mode="FORMATIVO")
            payload = _happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text, include_scores=False,
            )
            provider = _StubTextProvider(text=payload)
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNone(correction.final_scores)
            self.assertIsNotNone(correction.ai_output)
            self.assertIsNone(correction.ai_output["scores"])

    async def test_model_supplied_identification_never_overrides_the_service_built_one(self):
        """The AI never authors its own identification block - the service
        builds it from data it already has (essay_id, versions). A raw
        payload that happens to carry its own conflicting 'identification'
        key (forged essay_id/model_version/etc.) must never win the merge -
        the persisted ai_output must always carry the real, service-built
        values."""
        import json

        async with self.session_factory() as session:
            submission = await self._submission(session, "14", correction_mode="FORMATIVO")
            payload = json.loads(
                _happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text)
            )
            payload["identification"] = {
                "essay_id": str(uuid.uuid4()), "essay_version_id": str(uuid.uuid4()),
                "rubric_version": "forged", "model_version": "forged-model",
                "prompt_version": "forged", "engine_version": "forged",
                "contract_version": "essay_engine_output_v1", "anchor_mode": "TEXT_OFFSET",
            }
            provider = _StubTextProvider(text=json.dumps(payload))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertEqual(
                correction.ai_output["identification"]["essay_id"], str(submission.essay_id)
            )
            self.assertEqual(
                correction.ai_output["identification"]["model_version"], "gpt-test"
            )

    async def test_avaliativo_with_null_scores_and_validation_disabled_forces_review(self):
        """Regression: AVALIATIVO's own prompt asks for a full grade, but
        nothing stops a malformed AI response from returning scores=null
        anyway. With validation_enabled=False and no threshold configured,
        _apply_review_policy must never auto-publish a graded-mode
        correction with no grade - that would be silent wrong data no human
        ever reviews."""
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "16", correction_mode="AVALIATIVO", validation_enabled=False,
            )
            payload = _happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text, include_scores=False,
            )
            provider = _StubTextProvider(text=payload)
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "PENDING_REVIEW")
            self.assertIsNone(correction.final_scores)
            self.assertIsNone(correction.published_at)
            self.assertIsNone(correction.reviewed_at)

    async def test_engine_output_rejected_failure_reason_is_not_doubled(self):
        """Regression: EssayEngineOutputRejected.__str__ already prefixes the
        message with "{reason_code}: {message}", so failure_reason must not
        prepend exc.reason_code a second time (which produced e.g.
        "QUOTE_DOES_NOT_MATCH_TEXT: QUOTE_DOES_NOT_MATCH_TEXT: ...")."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "17", correction_mode="FORMATIVO")
            bad_payload = _happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                quote_override="isto nao esta no texto original",
            )
            provider = _StubTextProvider(text=bad_payload)
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIn("QUOTE_DOES_NOT_MATCH_TEXT", correction.failure_reason)
            self.assertEqual(
                correction.failure_reason.count("QUOTE_DOES_NOT_MATCH_TEXT"), 1
            )

    async def test_rubric_file_load_failure_becomes_needs_review_not_raised(self):
        """Regression: load_rubric_file(_RUBRIC_FILE_NAME) previously ran
        before this method's failure-handling try/except began, so if it
        ever raised (packaged YAML going missing/malformed), the exception
        would escape _run_ai/correct() entirely, contradicting this module's
        own docstring promise that every AI/data-side failure becomes a
        NEEDS_REVIEW row, never an escaped exception."""
        from unittest.mock import patch

        async with self.session_factory() as session:
            submission = await self._submission(session, "18", correction_mode="FORMATIVO")
            service = EssayCorrectionService(session, text_provider=_StubTextProvider())
            with patch(
                "agente_ia_edu.services.essay_correction.load_rubric_file",
                side_effect=RuntimeError("rubric file went missing"),
            ):
                correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNone(correction.ai_output)
            self.assertEqual(correction.rubric_version, "unknown")
            self.assertIn("rubric file went missing", correction.failure_reason)

    async def test_image_region_page_missing_dimensions_becomes_needs_review_not_crash(self):
        """Legacy data: an IMAGE_REGION submission whose page predates Task 3
        (R2's documented, pre-existing gap) has permanently-NULL width/height.
        This must land as an ordinary NEEDS_REVIEW row - never raise out of
        correct() and crash the caller (Task 8's confirm-submission route)."""
        async with self.session_factory() as session:
            submission = await self._submission(
                session, "15", anchor_mode="IMAGE_REGION", correction_mode="FORMATIVO",
            )
            session.add(EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id, page_number=1,
                storage_uri="/tmp/r3_fake_page_legacy.png", width=None, height=None,
            ))
            await session.commit()

            provider = _StubImageProvider(text=_happy_payload(anchor_mode="IMAGE_REGION"))
            service = EssayCorrectionService(session, image_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNone(correction.ai_output)
            self.assertIn("ValueError", correction.failure_reason)

    def test_production_prompt_version_is_v14(self):
        from agente_ia_edu.services.essay_correction import _PROMPT_VERSION
        self.assertEqual(_PROMPT_VERSION, "essay_correction_v14")

    def test_production_engine_version_is_v2(self):
        from agente_ia_edu.services.essay_correction import _ENGINE_VERSION
        self.assertEqual(_ENGINE_VERSION, "r3_correction_engine_v2")

    async def test_phase2_scores_override_phase1_raw_scores_in_final_scores(self):
        """The whole point of phase 2 (calibration run 2026-09-28, see
        essay_prompts/competency_scoring_v1.py's docstring): ai_output keeps
        phase 1's own (noisy) per-competency points exactly as reported,
        but final_scores - what actually gets published - comes from the
        separate, small, evidence-only phase-2 call instead."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "25", correction_mode="FORMATIVO")
            phase1_points = {c: 160 for c in ("C1", "C2", "C3", "C4", "C5")}
            phase2_points = {"C1": 80, "C2": 40, "C3": 120, "C4": 200, "C5": 0}
            provider = _StubTextProvider(
                text=_happy_payload(
                    anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                    per_competency_points=phase1_points,
                ),
                per_competency_points=phase2_points,
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            for code, points in phase1_points.items():
                self.assertEqual(
                    correction.ai_output["scores"]["per_competency"][code]["points"], points,
                )
            for code, points in phase2_points.items():
                self.assertEqual(
                    correction.final_scores["per_competency"][code]["points"], points,
                )
            self.assertEqual(correction.final_scores["total"], sum(phase2_points.values()))
            # Phase 1 (1 call) + phase 2 (5 concurrent calls, one per competency).
            self.assertEqual(provider.call_count, 6)

    async def test_phase2_provider_failure_becomes_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "26", correction_mode="FORMATIVO")
            provider = _StubTextProvider(
                text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text),
                phase2_raise_error=ProviderTimeoutError("competency scoring timed out"),
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNone(correction.ai_output)
            self.assertIsNone(correction.final_scores)
            self.assertIn("CompetencyScoringFailed", correction.failure_reason)

    async def test_phase2_invalid_points_value_becomes_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "27", correction_mode="FORMATIVO")
            provider = _StubTextProvider(
                text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text),
                phase2_text=json.dumps({"points": 50, "reasoning": "nivel invalido"}),
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIn("CompetencyScoringFailed", correction.failure_reason)
            self.assertIn("ValueError", correction.failure_reason)

    async def test_formativo_never_invokes_phase2(self):
        """output.scores is None in FORMATIVO - there is no grade for phase
        2 to refine, so it must never be called at all (not just ignored)."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "28", correction_mode="FORMATIVO")
            provider = _StubTextProvider(
                text=_happy_payload(
                    anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                    include_scores=False,
                )
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNone(correction.final_scores)
            self.assertEqual(provider.call_count, 1)

    async def test_phase2_mechanical_review_only_sent_for_c1(self):
        """MechanicalOccurrence.category is exclusively C1's own domain
        (norma padrao) - the phase-2 prompt for every other competency must
        never see it."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "29", correction_mode="FORMATIVO")
            payload = json.loads(
                _happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text)
            )
            payload["mechanical_review"] = [
                {
                    "category": "ORTOGRAFIA", "excerpt": "erro de grafia",
                    "suggested_form": "correcao", "rule_explanation": "regra x",
                }
            ]
            seen_prompts: dict[str, str] = {}

            class _RecordingProvider(_StubTextProvider):
                async def generate(self, request):
                    if "SCORING_RULES:" in request.prompt:
                        match = re.search(r"RUBRIC_(C\d)", request.prompt)
                        if match:
                            seen_prompts[match.group(1)] = request.prompt
                    return await super().generate(request)

            provider = _RecordingProvider(text=json.dumps(payload))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertIn("ORTOGRAFIA", seen_prompts["C1"])
            for code in ("C2", "C3", "C4", "C5"):
                self.assertNotIn("ocorrencias mecanicas", seen_prompts[code])

    async def test_alert_review_can_reject_a_false_positive_anula_redacao_alert(self):
        """The whole point of phase 2b (calibration run 2026-09-28, see
        essay_prompts/alert_review_v1.py's docstring): a normal, gradeable
        essay - phase 1 wrongly raised TEXTO_INSUFICIENTE - must NOT be
        zeroed once the alert review rejects it. ai_output keeps phase 1's
        own alert exactly as reported; final_scores reflects the review's
        verdict instead."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "30", correction_mode="FORMATIVO")
            phase2_points = {c: 160 for c in ("C1", "C2", "C3", "C4", "C5")}
            provider = _StubTextProvider(
                text=_happy_payload(
                    anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                    alerts=[{"code": "TEXTO_INSUFICIENTE", "detail": "Texto muito curto."}],
                    per_competency_points=phase2_points,
                ),
                per_competency_points=phase2_points,
                reject_alert_codes={"TEXTO_INSUFICIENTE"},
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertEqual(correction.ai_output["alerts"][0]["code"], "TEXTO_INSUFICIENTE")
            self.assertEqual(correction.final_scores["total"], sum(phase2_points.values()))
            for code, points in phase2_points.items():
                self.assertEqual(correction.final_scores["per_competency"][code]["points"], points)

    async def test_alert_review_confirming_the_alert_still_zeroes_final_scores(self):
        """The mirror case of the rejection test above - the stub's default
        behavior (confirm everything asked) must still zero the essay, the
        same outcome the pre-phase-2b alert tests already covered."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "31", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}],
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertEqual(correction.final_scores["total"], 0)

    async def test_alert_review_never_called_when_no_anula_redacao_alert_was_raised(self):
        """The common case: no candidate alert means no extra call at all -
        not just an ignored one."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "32", correction_mode="FORMATIVO")
            provider = _StubTextProvider(
                text=_happy_payload(anchor_mode="TEXT_OFFSET", text=submission.canonical_text)
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            # Phase 1 (1 call) + phase 2a (5 concurrent competency calls) -
            # no phase-2b call, since output.alerts was empty.
            self.assertEqual(provider.call_count, 6)

    async def test_alert_review_non_anula_redacao_alerts_pass_through_without_a_call(self):
        """OCR_DUVIDOSO/POSSIVEL_DUPLICIDADE/TANGENCIAMENTO_AO_TEMA never
        zero the whole essay, so they are never sent to alert_review_v1 -
        only _ANULA_REDACAO_ALERT_CODES candidates trigger that call."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "33", correction_mode="FORMATIVO")
            provider = _StubTextProvider(text=_happy_payload(
                anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                alerts=[{"code": "OCR_DUVIDOSO", "detail": "Trecho de leitura duvidosa."}],
            ))
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "APPROVED")
            self.assertEqual(correction.final_scores["total"], 800)
            self.assertEqual(provider.call_count, 6)

    async def test_alert_review_provider_failure_becomes_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "34", correction_mode="FORMATIVO")
            provider = _StubTextProvider(
                text=_happy_payload(
                    anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                    alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}],
                ),
                alert_review_raise_error=ProviderTimeoutError("alert review timed out"),
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNone(correction.ai_output)
            self.assertIsNone(correction.final_scores)
            self.assertIn("CompetencyScoringFailed", correction.failure_reason)

    async def test_alert_review_invalid_response_shape_becomes_needs_review(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "35", correction_mode="FORMATIVO")
            provider = _StubTextProvider(
                text=_happy_payload(
                    anchor_mode="TEXT_OFFSET", text=submission.canonical_text,
                    alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}],
                ),
                alert_review_text=json.dumps({"confirmed_alert_codes": "FUGA_AO_TEMA", "reasoning": "x"}),
            )
            service = EssayCorrectionService(session, text_provider=provider)
            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIn("CompetencyScoringFailed", correction.failure_reason)
            self.assertIn("ValueError", correction.failure_reason)


if __name__ == "__main__":
    unittest.main()
