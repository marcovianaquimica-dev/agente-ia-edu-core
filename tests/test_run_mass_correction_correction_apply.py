# tests/test_run_mass_correction_correction_apply.py
"""Achado C4 do relatorio final (segunda revisao final): _apply_correction_results,
em scripts/run_mass_correction.py, era a unica das 3 funcoes _apply_*_results
sem try/except por item dentro do loop - qualquer falha inesperada numa linha
(ex: content=None do modelo) derrubava a aplicacao do lote inteiro, inclusive
as OUTRAS linhas boas do mesmo lote. Este teste prova que isso nao acontece
mais: uma linha ruim e isolada (pulada ou vira NEEDS_REVIEW) e a linha boa ao
lado continua sendo aplicada e comitada normalmente - mesmo padrao que
test_run_mass_correction_scoring_sampling.py ja prova para _apply_scoring_results.
"""
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder
from agente_ia_edu.services.institution_settings import InstitutionSettingsService

from scripts.run_mass_correction import _apply_correction_results

RUBRIC_FILE = load_rubric_file("enem_2025")


class ApplyCorrectionResultsIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(RUBRIC_FILE)
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_submission(self, session, code: str) -> EssaySubmission:
        school = School(id=uuid.uuid4(), code=f"CORR-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        await InstitutionSettingsService(session).configure(
            school.id, performed_by_external_id="test", correction_mode="AVALIATIVO",
        )
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
        student = Student(
            id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}"
        )
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
            validation_enabled=True,
        )
        session.add(assignment)
        await session.flush()
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text="Texto qualquer da redacao para fins de teste.",
            normalized_text_hash="a" * 64, submitted_at=datetime.now(timezone.utc),
        )
        session.add(submission)
        await session.commit()
        return submission

    async def test_a_null_content_result_line_does_not_abort_the_other_lines_in_the_batch(self):
        async with self.session_factory() as session:
            good = await self._seed_submission(session, "good")
            bad = await self._seed_submission(session, "bad")

            good_line = {
                "custom_id": str(good.id),
                "response": {
                    "status_code": 200,
                    "body": {
                        "model": "gpt-test",
                        "choices": [{"message": {"content": "not valid json on purpose"}}],
                    },
                },
                "error": None,
            }
            bad_line = {
                "custom_id": str(bad.id),
                "response": {
                    "status_code": 200,
                    "body": {"model": "gpt-test", "choices": [{"message": {"content": None}}]},
                },
                "error": None,
            }

            await _apply_correction_results(session, [good_line, bad_line])

            good_correction = await session.scalar(
                select(EssayCorrection).where(EssayCorrection.essay_submission_id == good.id)
            )
            bad_correction = await session.scalar(
                select(EssayCorrection).where(EssayCorrection.essay_submission_id == bad.id)
            )

            # A linha "boa" (JSON invalido, uma falha ja tratada por
            # apply_correction_batch_result) sempre vira uma EssayCorrection
            # normal em NEEDS_REVIEW (ai_output=None) - nunca propaga.
            self.assertIsNotNone(good_correction)
            self.assertEqual(good_correction.status, "NEEDS_REVIEW")
            self.assertIsNone(good_correction.ai_output)

            # A linha "ruim" (content=None) e isolada pelo try/except do
            # achado C4: ou vira NEEDS_REVIEW, ou e pulada - nunca derruba a
            # aplicacao da linha boa ao lado, e nunca propaga uma excecao.
            if bad_correction is not None:
                self.assertEqual(bad_correction.status, "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
