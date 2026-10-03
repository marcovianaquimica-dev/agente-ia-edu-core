import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    EssaySubmission,
    EssaySubmissionPage,
    GradeLevel,
    PlatformEssayPrompt,
    PromptAssignment,
    PromptAssignmentLog,
    PromptMaterial,
    School,
    Segment,
    Student,
)


class EssayProposalModelTests(unittest.IsolatedAsyncioTestCase):
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

    async def _class_and_student(self, session, code):
        school = School(id=uuid.uuid4(), code=f"EP-{code}", name=f"school-{code}")
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
        await session.commit()
        return school, klass, student

    async def test_full_chain_round_trips(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "1")

            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema X",
                statement="Disserte sobre X.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            self.assertEqual(prompt.status, "DRAFT")

            material = PromptMaterial(
                id=uuid.uuid4(), essay_prompt_id=prompt.id, material_type="TEXT",
                content="Texto motivador.", position=0,
            )
            session.add(material)

            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:prof1",
            )
            session.add(assignment)
            await session.flush()
            self.assertEqual(assignment.status, "OPEN")
            self.assertTrue(assignment.validation_enabled)

            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                canonical_text="Redacao completa.", normalized_text_hash="a" * 64,
                submitted_at=datetime.now(timezone.utc),
            )
            session.add(submission)
            await session.commit()

            fetched = await session.get(EssaySubmission, submission.id)
            self.assertEqual(fetched.mode, "TYPED")
            self.assertEqual(fetched.status, "SUBMITTED")

    async def test_essay_submission_page_requires_positive_page_number(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "2")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="T", statement="S", year=2026,
                created_by_external_identity="teacher:p",
            )
            session.add(prompt)
            await session.flush()
            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:p",
            )
            session.add(assignment)
            await session.flush()
            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="PHOTO", anchor_mode="IMAGE_REGION", status="PENDING_TRANSCRIPTION",
            )
            session.add(submission)
            await session.flush()

            page = EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id,
                page_number=0, storage_uri="var/x.png",
            )
            session.add(page)
            with self.assertRaises(Exception):
                await session.flush()

    async def test_essay_submission_page_token_usage_columns_round_trip(self):
        """input_tokens/output_tokens default to NULL (a page that never
        called the transcriber, e.g. a digitally-typed PDF) and accept plain
        integers (a page that did) - see _ocr_page in
        services/essay_submission.py."""
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "3")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="T", statement="S", year=2026,
                created_by_external_identity="teacher:p",
            )
            session.add(prompt)
            await session.flush()
            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:p",
            )
            session.add(assignment)
            await session.flush()
            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="PHOTO", anchor_mode="IMAGE_REGION", status="PENDING_TRANSCRIPTION",
            )
            session.add(submission)
            await session.flush()

            page_no_usage = EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id,
                page_number=1, storage_uri="var/no_usage.png",
            )
            page_with_usage = EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id,
                page_number=2, storage_uri="var/with_usage.png",
                input_tokens=4200, output_tokens=310,
            )
            session.add_all([page_no_usage, page_with_usage])
            await session.commit()

            fetched_no_usage = await session.get(EssaySubmissionPage, page_no_usage.id)
            fetched_with_usage = await session.get(EssaySubmissionPage, page_with_usage.id)
            self.assertIsNone(fetched_no_usage.input_tokens)
            self.assertIsNone(fetched_no_usage.output_tokens)
            self.assertEqual(fetched_with_usage.input_tokens, 4200)
            self.assertEqual(fetched_with_usage.output_tokens, 310)

    async def test_assignment_with_student_id_and_no_class_id_succeeds(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "4")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema Y",
                statement="Disserte sobre Y.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id,
                essay_prompt_id=prompt.id, class_id=None,
                student_id=student.id, assigned_by_external_identity="prof",
            )
            session.add(assignment)
            await session.commit()
            refreshed = await session.get(PromptAssignment, assignment.id)
            self.assertIsNone(refreshed.class_id)
            self.assertEqual(refreshed.student_id, student.id)

    async def test_assignment_with_class_id_and_student_id_together_violates_check(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "5")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema Z",
                statement="Disserte sobre Z.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id,
                essay_prompt_id=prompt.id, class_id=klass.id,
                student_id=student.id, assigned_by_external_identity="prof",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_same_student_assigned_twice_to_same_prompt_violates_unique(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "6")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema W",
                statement="Disserte sobre W.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id,
                essay_prompt_id=prompt.id, class_id=None,
                student_id=student.id, assigned_by_external_identity="prof",
            ))
            await session.commit()
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id,
                essay_prompt_id=prompt.id, class_id=None,
                student_id=student.id, assigned_by_external_identity="prof",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_prompt_assignment_log_round_trips_target_summary_json(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "7")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema V",
                statement="Disserte sobre V.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            log = PromptAssignmentLog(
                id=uuid.uuid4(), school_id=school.id,
                essay_prompt_id=prompt.id,
                assigned_by_external_identity="prof",
                target_summary={
                    "turmas": [{"class_id": str(klass.id), "name": "Turma A"}],
                    "series": [],
                    "alunos": [{"student_id": str(student.id), "name": "Aluno"}],
                },
            )
            session.add(log)
            await session.commit()
            refreshed = await session.get(PromptAssignmentLog, log.id)
            self.assertEqual(refreshed.target_summary["turmas"][0]["name"], "Turma A")
            self.assertEqual(refreshed.target_summary["series"], [])

    async def test_prompt_material_with_platform_essay_prompt_id_and_no_essay_prompt_id_succeeds(self):
        async with self.session_factory() as session:
            platform_prompt = PlatformEssayPrompt(
                id=uuid.uuid4(), title="Tema plataforma", statement="Disserte.",
                created_by_external_identity="user:ADMIN",
            )
            session.add(platform_prompt)
            await session.flush()
            material = PromptMaterial(
                id=uuid.uuid4(), essay_prompt_id=None, platform_essay_prompt_id=platform_prompt.id,
                material_type="FILE", storage_uri="var/materials/x.pdf", position=0,
            )
            session.add(material)
            await session.commit()
            refreshed = await session.get(PromptMaterial, material.id)
            self.assertIsNone(refreshed.essay_prompt_id)
            self.assertEqual(refreshed.platform_essay_prompt_id, platform_prompt.id)

    async def test_prompt_material_with_both_essay_prompt_id_and_platform_essay_prompt_id_violates_check(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="PM-1", name="Escola")
            session.add(school)
            await session.flush()
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                year=2026, created_by_external_identity="prof",
            )
            platform_prompt = PlatformEssayPrompt(
                id=uuid.uuid4(), title="Tema plataforma", statement="Disserte.",
                created_by_external_identity="user:ADMIN",
            )
            session.add_all([prompt, platform_prompt])
            await session.flush()
            session.add(PromptMaterial(
                id=uuid.uuid4(), essay_prompt_id=prompt.id,
                platform_essay_prompt_id=platform_prompt.id,
                material_type="FILE", storage_uri="var/materials/x.pdf", position=0,
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_prompt_material_with_neither_essay_prompt_id_nor_platform_essay_prompt_id_violates_check(self):
        async with self.session_factory() as session:
            session.add(PromptMaterial(
                id=uuid.uuid4(), essay_prompt_id=None, platform_essay_prompt_id=None,
                material_type="FILE", storage_uri="var/materials/x.pdf", position=0,
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_same_position_twice_for_the_same_platform_prompt_violates_unique(self):
        async with self.session_factory() as session:
            platform_prompt = PlatformEssayPrompt(
                id=uuid.uuid4(), title="Tema plataforma", statement="Disserte.",
                created_by_external_identity="user:ADMIN",
            )
            session.add(platform_prompt)
            await session.flush()
            session.add(PromptMaterial(
                id=uuid.uuid4(), platform_essay_prompt_id=platform_prompt.id,
                material_type="TEXT", content="a", position=0,
            ))
            await session.commit()
            session.add(PromptMaterial(
                id=uuid.uuid4(), platform_essay_prompt_id=platform_prompt.id,
                material_type="TEXT", content="b", position=0,
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()


if __name__ == "__main__":
    unittest.main()
