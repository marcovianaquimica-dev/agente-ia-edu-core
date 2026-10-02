"""R4 lote - roster_for_batch: escopo turma/serie/escola do casamento
automatico de aluno.

match_student e _assignment_for_student nao sao tocados nesta leva - so o
TAMANHO do roster que chega neles muda. Este arquivo testa so o roster em
si, nao o match (ja coberto em test_r4_essay_batch_matching.py).
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchUpload, EssayPrompt, GradeLevel, Person,
    School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.services.essay_batch import EssayBatchService


class RosterForBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.session = self.session_factory()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def _seed(self):
        """2 turmas da MESMA serie (A e B) + 1 turma de OUTRA serie (C),
        1 aluno ativo em cada, mais 1 aluno com status EXITED na turma A (nunca deve
        aparecer em roster nenhum)."""
        school = School(id=uuid.uuid4(), code="RS-1", name="Escola")
        self.session.add(school)
        await self.session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-RS")
        self.session.add(segment)
        await self.session.flush()
        grade_x = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie X", external_id="GRADE-RS-X",
        )
        grade_y = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie Y", external_id="GRADE-RS-Y",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-RS")
        self.session.add_all([grade_x, grade_y, year])
        await self.session.flush()

        class_a = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma A", external_id="TURMA-RS-A",
        )
        class_b = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma B", external_id="TURMA-RS-B",
        )
        class_c = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_y.id, name="Turma C", external_id="TURMA-RS-C",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        self.session.add_all([class_a, class_b, class_c, prompt])
        await self.session.flush()

        def _make_student(nome: str, klass, status: str = "ACTIVE") -> uuid.UUID:
            person = Person(id=uuid.uuid4(), school_id=school.id, full_name=nome)
            self.session.add(person)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id)
            self.session.add(student)
            enrollment = StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status=status,
            )
            self.session.add(enrollment)
            return student.id

        student_a = _make_student("Aluno Turma A", class_a)
        student_b = _make_student("Aluno Turma B", class_b)
        student_c = _make_student("Aluno Turma C", class_c)
        student_inativo = _make_student("Aluno Inativo", class_a, status="EXITED")
        await self.session.flush()

        return {
            "school": school, "prompt": prompt,
            "class_a": class_a, "class_b": class_b, "class_c": class_c,
            "grade_x": grade_x, "grade_y": grade_y,
            "student_a": student_a, "student_b": student_b, "student_c": student_c,
            "student_inativo": student_inativo,
        }

    async def test_escopo_turma_delega_para_class_roster(self):
        seed = await self._seed()
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
            class_id=seed["class_a"].id, grade_level_id=None,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        service = EssayBatchService(self.session)
        roster = await service.roster_for_batch(batch)
        ids = {student_id for student_id, _, _ in roster}
        self.assertEqual(ids, {seed["student_a"]})

    async def test_escopo_serie_inclui_turmas_a_e_b_mas_nao_c(self):
        seed = await self._seed()
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
            class_id=None, grade_level_id=seed["grade_x"].id,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        service = EssayBatchService(self.session)
        roster = await service.roster_for_batch(batch)
        ids = {student_id for student_id, _, _ in roster}
        self.assertEqual(ids, {seed["student_a"], seed["student_b"]})

    async def test_escopo_escola_inclui_as_3_turmas(self):
        seed = await self._seed()
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
            class_id=None, grade_level_id=None,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        service = EssayBatchService(self.session)
        roster = await service.roster_for_batch(batch)
        ids = {student_id for student_id, _, _ in roster}
        self.assertEqual(ids, {seed["student_a"], seed["student_b"], seed["student_c"]})

    async def test_matricula_inativa_nunca_aparece_em_nenhum_escopo(self):
        seed = await self._seed()
        for class_id, grade_level_id in (
            (seed["class_a"].id, None), (None, seed["grade_x"].id), (None, None),
        ):
            batch = EssayBatchUpload(
                id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=class_id, grade_level_id=grade_level_id,
                uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
            )
            service = EssayBatchService(self.session)
            roster = await service.roster_for_batch(batch)
            ids = {student_id for student_id, _, _ in roster}
            self.assertNotIn(seed["student_inativo"], ids)
