import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.essay_correction_key import essay_text_hash, normalize_essay_text
from agente_ia_edu.services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService


class TypedSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_typed_submission_is_immediately_submitted(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            raw = "  Minha redação   com espaços.  \r\n\r\nSegundo paragrafo.  "

            submission = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text=raw,
            )

            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.canonical_text, normalize_essay_text(raw))
            self.assertEqual(submission.normalized_text_hash, essay_text_hash(raw))
            self.assertIsNotNone(submission.submitted_at)
            self.assertIsNotNone(submission.essay_id)

    async def test_formativo_resubmission_supersedes_the_previous_version(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            first = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Primeira versao.",
            )
            second = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Segunda versao.",
                essay_id=first.essay_id, correction_mode="FORMATIVO",
            )

            self.assertEqual(second.essay_id, first.essay_id)
            self.assertNotEqual(second.id, first.id)
            self.assertEqual(second.status, "SUBMITTED")

            refreshed_first = await session.get(type(first), first.id)
            self.assertEqual(refreshed_first.status, "SUPERSEDED")

    async def test_avaliativo_resubmission_is_blocked(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            first = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Unica versao.",
            )

            with self.assertRaises(EssayResubmissionBlockedError):
                await svc.start_typed_submission(
                    school_id=school_id, prompt_assignment_id=assignment_id,
                    student_id=student_id, text="Tentativa negada.",
                    essay_id=first.essay_id, correction_mode="AVALIATIVO",
                )


if __name__ == "__main__":
    unittest.main()
