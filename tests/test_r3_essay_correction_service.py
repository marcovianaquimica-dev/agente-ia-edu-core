# tests/test_r3_essay_correction_service.py
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
    quote_override: str | None = None, include_scores: bool = True,
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
    scores = (
        {
            "per_competency": {
                "C1": {"points": 160, "confidence": 0.9},
                "C2": {"points": 160, "confidence": 0.9},
                "C3": {"points": 160, "confidence": 0.9},
                "C4": {"points": 160, "confidence": 0.9},
                "C5": {"points": 160, "confidence": 0.9},
            },
            "total": 800,
        }
        if include_scores
        else None
    )
    return json.dumps(
        {
            "scores": scores,
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
            service = EssayCorrectionService(session, image_provider=provider)
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


if __name__ == "__main__":
    unittest.main()
