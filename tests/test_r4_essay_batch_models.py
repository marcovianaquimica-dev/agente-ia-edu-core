"""R4 lote - shape das duas tabelas novas e da coluna de logo da escola.

SQLite in-memory + create_all, mesmo padrão de tests/test_r2_essay_proposal_models.py.
As CHECK constraints sao validadas de verdade (SQLite aplica CHECK), as FKs
compostas so no teste de migration em PostgreSQL (SQLite nao aplica FK por padrao).
"""

import unittest
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt,
    GradeLevel, School, Segment,
)


class EssayBatchModelsTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed(self, session):
        school = School(id=uuid.uuid4(), code="LOTE-1", name="Escola Lote")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-L1")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a serie", external_id="GRADE-L1",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-L1"
        )
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-L1",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof_lote",
        )
        session.add_all([klass, prompt])
        await session.commit()
        return school, klass, prompt

    async def _batch(self, session, school, klass, prompt, *, status="PROCESSING", total_pages=0):
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof_lote",
            status=status, total_pages=total_pages,
        )
        session.add(batch)
        await session.flush()
        return batch

    async def test_school_has_nullable_logo_storage_uri(self):
        async with self.session_factory() as session:
            school, _, _ = await self._seed(session)
            self.assertIsNone(school.logo_storage_uri)
            school.logo_storage_uri = "/var/material_storage/ab/abc/logo.png"
            await session.commit()
            refreshed = await session.get(School, school.id)
            self.assertEqual(
                refreshed.logo_storage_uri, "/var/material_storage/ab/abc/logo.png"
            )

    async def test_batch_upload_defaults_to_processing(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = EssayBatchUpload(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, uploaded_by_external_identity="prof_lote",
                total_pages=3,
            )
            session.add(batch)
            await session.commit()
            self.assertEqual(batch.status, "PROCESSING")
            self.assertEqual(batch.total_pages, 3)

    async def test_batch_upload_rejects_unknown_status(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            with self.assertRaises(IntegrityError):
                await self._batch(session, school, klass, prompt, status="FINISHED")

    async def test_batch_page_defaults_to_needs_review(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=1)
            page = EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=1,
                storage_uri="/var/material_storage/aa/aaa/page_1.png",
            )
            session.add(page)
            await session.commit()
            self.assertEqual(page.status, "NEEDS_REVIEW")
            self.assertIsNone(page.ocr_name_raw)
            self.assertIsNone(page.ocr_cpf_raw)
            self.assertIsNone(page.ocr_body_text)
            self.assertIsNone(page.matched_student_id)
            self.assertIsNone(page.essay_submission_id)

    async def test_batch_page_rejects_unknown_status(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=1)
            session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=1,
                storage_uri="/x.png", status="PENDING",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_batch_page_rejects_page_number_zero(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=1)
            session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=0, storage_uri="/x.png",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_batch_page_number_is_unique_per_batch(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=2)
            session.add_all([
                EssayBatchPage(
                    id=uuid.uuid4(), batch_id=batch.id, page_number=1, storage_uri="/a.png"
                ),
                EssayBatchPage(
                    id=uuid.uuid4(), batch_id=batch.id, page_number=1, storage_uri="/b.png"
                ),
            ])
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_class_id_e_opcional_quando_grade_level_id_esta_setado(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="MOD-1", name="Escola")
            session.add(school)
            await session.flush()
            segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-MOD")
            session.add(segment)
            await session.flush()
            grade = GradeLevel(
                id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                name="3a", external_id="GRADE-MOD",
            )
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                year=2026, created_by_external_identity="prof",
            )
            session.add_all([grade, prompt])
            await session.flush()

            batch = EssayBatchUpload(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=None, grade_level_id=grade.id,
                uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
            )
            session.add(batch)
            await session.flush()

            fetched = await session.get(EssayBatchUpload, batch.id)
            self.assertIsNone(fetched.class_id)
            self.assertEqual(fetched.grade_level_id, grade.id)

    async def test_class_id_e_grade_level_id_juntos_violam_o_check(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="MOD-2", name="Escola")
            session.add(school)
            await session.flush()
            segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-MOD2")
            session.add(segment)
            await session.flush()
            grade = GradeLevel(
                id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                name="3a", external_id="GRADE-MOD2",
            )
            year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-MOD2")
            session.add_all([grade, year])
            await session.flush()
            klass = Class(
                id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                grade_level_id=grade.id, name="3A", external_id="TURMA-MOD2",
            )
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                year=2026, created_by_external_identity="prof",
            )
            session.add_all([klass, prompt])
            await session.flush()

            batch = EssayBatchUpload(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, grade_level_id=grade.id,
                uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
            )
            session.add(batch)
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
