"""R4 lote - status agregado que a tela de acompanhamento do professor le."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt, GradeLevel,
    Person, PromptAssignment, School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.services.essay_batch import EssayBatchService


class BatchStatusTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="ST-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-ST")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-ST",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-ST")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-ST",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        session.add(PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        ))
        students = {}
        for index, (full_name, document_number) in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                document_number=document_number, external_id=f"PER-S{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-S{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-S{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, students

    async def _batch(self, session, school, klass, prompt, page_specs, *, status="DONE"):
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof",
            status=status, total_pages=len(page_specs),
        )
        session.add(batch)
        await session.flush()
        for number, spec in enumerate(page_specs, start=1):
            session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=number,
                storage_uri=f"/p{number}.png", status=spec["status"],
                ocr_name_raw=spec.get("name"), ocr_cpf_raw=spec.get("cpf"),
                ocr_body_text=spec.get("body"),
                matched_student_id=spec.get("student_id"),
                essay_submission_id=spec.get("submission_id"),
            ))
        await session.commit()
        return batch

    async def test_counts_matched_and_needs_review(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", "11122233344"), ("João da Silva", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "MATCHED_AUTO", "name": "ANA LUCIA FERREIRA", "body": "t",
                 "student_id": students["Ana Lúcia Ferreira"], "submission_id": uuid.uuid4()},
                {"status": "NEEDS_REVIEW", "name": "CARLOS MENDES", "cpf": "99988877766",
                 "body": "t"},
                {"status": "RESOLVED_MANUAL", "name": None, "body": "t",
                 "student_id": students["João da Silva"], "submission_id": uuid.uuid4()},
            ])

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual(data["total_pages"], 3)
            self.assertEqual(data["matched_count"], 2)
            self.assertEqual(data["needs_review_count"], 1)
            self.assertEqual(data["status"], "DONE")

    async def test_lists_needs_review_pages_with_their_ocr_hints(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "NEEDS_REVIEW", "name": "CARLOS MENDES", "cpf": "99988877766",
                 "body": "algum texto"},
                {"status": "NEEDS_REVIEW", "name": None, "cpf": None, "body": None},
            ])

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual([p["page_number"] for p in data["needs_review_pages"]], [1, 2])
            first, second = data["needs_review_pages"]
            self.assertEqual(first["ocr_name_raw"], "CARLOS MENDES")
            self.assertEqual(first["ocr_cpf_raw"], "99988877766")
            self.assertTrue(first["has_text"])
            self.assertIsNone(second["ocr_name_raw"])
            self.assertFalse(second["has_text"])

    async def test_available_students_excludes_whoever_already_has_a_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", "11122233344"), ("João da Silva", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "MATCHED_AUTO", "body": "t",
                 "student_id": students["Ana Lúcia Ferreira"], "submission_id": uuid.uuid4()},
                {"status": "NEEDS_REVIEW", "body": "t"},
            ])

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual(
                data["available_students"],
                [{"student_id": students["João da Silva"], "full_name": "João da Silva",
                  "document_number": None}],
            )

    async def test_a_processing_batch_reports_its_partial_state(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "NEEDS_REVIEW"}, {"status": "NEEDS_REVIEW"},
            ], status="PROCESSING")

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual(data["status"], "PROCESSING")
            self.assertEqual(data["matched_count"], 0)
            self.assertEqual(data["needs_review_count"], 2)

    async def test_unknown_batch_raises(self):
        async with self.session_factory() as session:
            with self.assertRaises(ValueError):
                await EssayBatchService(session).get_batch_status(uuid.uuid4())


if __name__ == "__main__":
    unittest.main()
