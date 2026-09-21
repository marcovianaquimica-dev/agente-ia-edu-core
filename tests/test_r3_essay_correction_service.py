# tests/test_r3_essay_correction_service.py
import unittest
import uuid
from datetime import datetime, timezone

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
    def __init__(self, *, text="", model="gpt-test", raise_error=None):
        self._text = text
        self.model = model
        self._raise_error = raise_error
        self.last_request = None

    async def generate(self, request):
        self.last_request = request
        if self._raise_error is not None:
            raise self._raise_error
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
    quote_override: str | None = None,
) -> str:
    import json

    if anchor_mode == "TEXT_OFFSET":
        quote = quote_override if quote_override is not None else text[0:10]
        anchor = {"type": "TEXT_OFFSET", "start": 0, "end": 10, "quote": quote}
    else:
        anchor = {
            "type": "IMAGE_REGION", "page": page, "x": 5.0, "y": 5.0,
            "width": min(50.0, box[0] - 5.0), "height": min(20.0, box[1] - 5.0),
            "read_text": "trecho lido na imagem",
        }
    return json.dumps(
        {
            "scores": {
                "per_competency": {
                    "C1": {"points": 160, "confidence": 0.9},
                    "C2": {"points": 160, "confidence": 0.9},
                    "C3": {"points": 160, "confidence": 0.9},
                    "C4": {"points": 160, "confidence": 0.9},
                    "C5": {"points": 160, "confidence": 0.9},
                },
                "total": 800,
            },
            "rationales": [
                {"competency_code": "C1", "summary": "Boa norma padrao.", "signal_keys": []}
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
                "detalhamento": "com fiscalizacao", "respeita_direitos_humanos": True,
            },
            "alerts": [],
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
            self.assertIn("SCORING_MODE: FORMATIVO", provider.last_request.prompt)

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
            self.assertIn("SCORING_MODE: AVALIATIVO", provider.last_request.prompt)

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
            service = EssayCorrectionService(session, image_provider=provider)
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


if __name__ == "__main__":
    unittest.main()
